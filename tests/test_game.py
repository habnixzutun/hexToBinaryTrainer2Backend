"""Tests for the game endpoints.

Two generations live side by side here on purpose (plan D-6): the legacy
``/correct`` / ``/incorrect`` handlers over ``user_stats``, which US-34 deletes,
and the Sprint-4 read side over ``user_game_profile`` — ``GET /api/game/profile``
(US-35) and the rewritten ``GET /api/game/leaderboard`` (US-36).

Every expectation for the read side is a **plan literal** copied from
``docs/sprint4-plan.md`` (US-35, US-36, D-1, D-2), never derived from ``src/``,
so a wrong constant cannot hide behind a wrong expectation.

The last section drops the HTTP layer and drives the read-side services directly:
their guards (a blank identity, an unbounded ``limit``) sit *behind* the router's own
validation, so no endpoint test can reach them, and they exist for the next caller
that will not be stopped by a Pydantic schema.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.models.game import UserGameProfile, UserStats
from src.services.leaderboard_service import leaderboard_entries
from src.services.profile_service import load_snapshot, profile_response
from src.utils.jwt_utils import create_access_token, create_refresh_token

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-game-001"
USER_EMAIL = "game_test@example.com"
USER_USERNAME = "GameTester"

# --------------------------------------------------------------------------- #
# Plan literals for the US-35 / US-36 read side (docs/sprint4-plan.md)
# --------------------------------------------------------------------------- #

# D-2 unlock ladder, plan order: slot 1/2/3/4 -> L1/L4/L8/L13 for the three
# conversion modes, L1/L2/L3/L4 for unicode (widths are BITS in every mode, OQ-7).
UNLOCK_LADDER: tuple[dict[str, Any], ...] = (
    {"mode": "hex_to_bin", "bit_width": 4, "min_level": 1},
    {"mode": "hex_to_bin", "bit_width": 8, "min_level": 4},
    {"mode": "hex_to_bin", "bit_width": 16, "min_level": 8},
    {"mode": "hex_to_bin", "bit_width": 32, "min_level": 13},
    {"mode": "hex_to_dec", "bit_width": 4, "min_level": 1},
    {"mode": "hex_to_dec", "bit_width": 8, "min_level": 4},
    {"mode": "hex_to_dec", "bit_width": 16, "min_level": 8},
    {"mode": "hex_to_dec", "bit_width": 32, "min_level": 13},
    {"mode": "bin_to_dec", "bit_width": 4, "min_level": 1},
    {"mode": "bin_to_dec", "bit_width": 8, "min_level": 4},
    {"mode": "bin_to_dec", "bit_width": 16, "min_level": 8},
    {"mode": "bin_to_dec", "bit_width": 32, "min_level": 13},
    {"mode": "unicode", "bit_width": 8, "min_level": 1},
    {"mode": "unicode", "bit_width": 16, "min_level": 2},
    {"mode": "unicode", "bit_width": 24, "min_level": 3},
    {"mode": "unicode", "bit_width": 32, "min_level": 4},
)

# US-35 AC-2: L1 opens exactly the four slot-1 entries — the three 4-bit
# conversions and unicode's 1 BYTE, which is 8 bits (OQ-7), not 4.
UNLOCKS_LEVEL_1: tuple[dict[str, Any], ...] = (
    {"mode": "hex_to_bin", "bit_width": 4, "min_level": 1},
    {"mode": "hex_to_dec", "bit_width": 4, "min_level": 1},
    {"mode": "bin_to_dec", "bit_width": 4, "min_level": 1},
    {"mode": "unicode", "bit_width": 8, "min_level": 1},
)


# The subset of the ladder open at a level (``min_level <= level``), read off the
# literal table above — the §4.9 UN-02 filter, applied to the plan table rather
# than to ``unlock_service``, so the service cannot grade its own homework.
def _unlocks_at(level: int) -> list[dict[str, Any]]:
    return [row for row in UNLOCK_LADDER if row["min_level"] <= level]


# Plan §4.8/D-1 cumulative thresholds: cum(1)=0, cum(2)=100, cum(3)=252,
# cum(4)=476, cum(5)=812, cum(6)=1320 — so total_xp 1320 IS level 6 and the
# span 6 -> 7 is req(6) = 760. Those are the US-33 golden-fixture numbers, and
# US-35 reports the same state without the per-answer delta.
PROFILE_GOLDEN: dict[str, Any] = {
    "total_xp": 1320,
    "level": 6,
    "xp_in_level": 0,
    "xp_required_for_next_level": 760,
    "current_streak": 3,
    "best_streak": 7,
    "correct_count": 42,
    "incorrect_count": 5,
    "unlocks": _unlocks_at(6),
}
# The row that state needs, stored level included.
PROFILE_GOLDEN_SEED: dict[str, int] = {
    "total_xp": 1320,
    "level": 6,
    "current_streak": 3,
    "best_streak": 7,
    "correct_count": 42,
    "incorrect_count": 5,
}


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


def _add_verified_user(session: AsyncSession, user_id: str, username: str, email: str):
    session.add(User(id=user_id, username=username, email=email))
    session.add(
        UserIdentity(
            id=f"identity-{user_id}",
            user_id=user_id,
            provider="email",
            provider_user_id=email,
            password_hash="irrelevant",
            verified_at=datetime.now(UTC),
        )
    )


def _add_profile(session: AsyncSession, user_id: str, **columns: int) -> None:
    """A ``user_game_profile`` row for an already-added user (US-35/US-36 seed).

    Written straight to the table rather than earned through ``/answer``: these
    tests are about the READ side, and a seeded row keeps the ordering and shape
    assertions independent of the economy's write path.
    """
    session.add(UserGameProfile(user_id=user_id, **columns))


def _bearer(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
async def seeded_db(db_session):
    _add_verified_user(db_session, USER_ID, USER_USERNAME, USER_EMAIL)
    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seeded_db):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_client(client):
    """Test client authenticated as the seeded user."""
    client.headers["Authorization"] = f"Bearer {create_access_token(USER_ID)}"
    return client


async def test_record_correct_answer(auth_client: AsyncClient):
    """POST /api/game/correct should record correct answer and award bits as points."""
    response = await auth_client.post("/api/game/correct", json={"bits": 8})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Correct answer recorded"
    assert data["user_id"] == USER_ID
    assert data["correct_count"] == 1
    assert data["incorrect_count"] == 0
    assert data["points"] == 8  # bits added directly as points


async def test_record_incorrect_answer(auth_client: AsyncClient):
    """POST /api/game/incorrect should increment incorrect count and deduct bits*4."""
    response = await auth_client.post("/api/game/incorrect", json={"bits": 4})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Incorrect answer recorded"
    assert data["correct_count"] == 0
    assert data["incorrect_count"] == 1
    assert data["points"] == -16  # -(4 bits * 4)


async def test_multiple_answers_accumulate(auth_client: AsyncClient):
    """Multiple game actions should accumulate stats correctly."""
    await auth_client.post("/api/game/correct", json={"bits": 4})  # +4
    await auth_client.post("/api/game/correct", json={"bits": 8})  # +8
    await auth_client.post("/api/game/incorrect", json={"bits": 4})  # -16

    response = await auth_client.post("/api/game/correct", json={"bits": 12})  # +12
    data = response.json()

    assert data["correct_count"] == 3
    assert data["incorrect_count"] == 1
    assert data["points"] == 8  # 4 + 8 - 16 + 12 = 8


@pytest.mark.parametrize("endpoint", ["/api/game/correct", "/api/game/incorrect"])
async def test_game_action_without_token_returns_401(client: AsyncClient, endpoint):
    """Game actions are protected and require a bearer token."""
    response = await client.post(endpoint, json={"bits": 8})

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# US-35: GET /api/game/profile
# --------------------------------------------------------------------------- #

PROFILE_URL = "/api/game/profile"


async def test_profile_returns_the_full_economy_state(
    auth_client: AsyncClient, seeded_db
):
    """US-35: every plan field, from one seeded row, with no extra keys."""
    _add_profile(seeded_db, USER_ID, **PROFILE_GOLDEN_SEED)
    await seeded_db.commit()

    response = await auth_client.get(PROFILE_URL)

    assert response.status_code == 200, response.text
    assert response.json() == PROFILE_GOLDEN


async def test_profile_without_a_row_returns_the_level_one_zero_state(
    auth_client: AsyncClient,
):
    """US-35: no profile yet (lazy creation is ``/answer``'s job) -> zeros at L1.

    The row is NOT created here — a GET that wrote would break the lazy-creation
    contract of US-28 AC-3, so this asserts the zero state AND the absent row.
    """
    response = await auth_client.get(PROFILE_URL)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "total_xp": 0,
        "level": 1,
        "xp_in_level": 0,
        "xp_required_for_next_level": 100,  # req(1), plan D-1
        "current_streak": 0,
        "best_streak": 0,
        "correct_count": 0,
        "incorrect_count": 0,
        # The wire is JSON, so the frozen tuple has to be read as a list here —
        # the ladder itself stays a tuple above, out of the test's mutability reach.
        "unlocks": list(UNLOCKS_LEVEL_1),
    }


async def test_profile_does_not_create_the_row_it_reports_on(
    auth_client: AsyncClient, seeded_db
):
    """The read side stays a read: no profile row before, none after."""
    before = (
        await seeded_db.execute(
            select(func.count())
            .select_from(UserGameProfile)
            .where(UserGameProfile.user_id == USER_ID)
        )
    ).scalar_one()

    assert (await auth_client.get(PROFILE_URL)).status_code == 200

    after = (
        await seeded_db.execute(
            select(func.count())
            .select_from(UserGameProfile)
            .where(UserGameProfile.user_id == USER_ID)
        )
    ).scalar_one()
    assert (before, after) == (0, 0)


@pytest.mark.parametrize(
    ("total_xp", "level", "in_level", "required"),
    [
        pytest.param(0, 1, 0, 100, id="fresh-player-at-cum(1)"),
        pytest.param(99, 1, 99, 100, id="last-step-before-cum(2)"),
        pytest.param(100, 2, 0, 152, id="exactly-on-cum(2)"),  # LV-07 inclusive
        pytest.param(251, 2, 151, 152, id="last-step-before-cum(3)"),
        pytest.param(252, 3, 0, 224, id="exactly-on-cum(3)"),
        pytest.param(476, 4, 0, 336, id="exactly-on-cum(4)"),
        pytest.param(812, 5, 0, 508, id="exactly-on-cum(5)"),
        pytest.param(1320, 6, 0, 760, id="the-us-33-golden-total"),
    ],
)
async def test_profile_progress_is_the_d1_curve(
    auth_client: AsyncClient,
    seeded_db,
    total_xp: int,
    level: int,
    in_level: int,
    required: int,
):
    """US-35 AC: the wire pair is the D-1 curve, read off the plan's ``cum`` row.

    ``cum(1..6) = 0, 100, 252, 476, 812, 1320`` and ``req(L) = cum(L+1) - cum(L)``
    are arithmetic on plan literals (D-1, §4.8 LV-05/LV-07, and the R-4 corrected
    figures), never calls into ``level_service``. Level, ``xp_in_level`` and
    ``xp_required_for_next_level`` all derive from the stored ``total_xp``, so the
    three cannot disagree with each other (D-1) — and the OQ-8 name says span, not
    threshold.
    """
    _add_profile(
        seeded_db,
        USER_ID,
        total_xp=total_xp,
        level=level,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )
    await seeded_db.commit()

    payload = (await auth_client.get(PROFILE_URL)).json()

    assert payload["total_xp"] == total_xp
    assert payload["level"] == level
    assert payload["xp_in_level"] == in_level
    assert payload["xp_required_for_next_level"] == required
    assert payload["unlocks"] == _unlocks_at(level)


@pytest.mark.parametrize(
    ("total_xp", "level", "expected_count"),
    [
        pytest.param(0, 1, 4, id="L1-four-slot-one-entries"),
        pytest.param(476, 4, 10, id="L4-the-8-bit-shelf"),  # plan R-4: cum(4)=476
        pytest.param(1320, 6, 10, id="L6-nothing-new-opens"),  # the golden total
        pytest.param(3220, 8, 13, id="L8-the-16-bit-shelf"),  # plan R-4: cum(8)=3220
        pytest.param(25752, 13, 16, id="L13-the-whole-ladder"),  # plan R-4: cum(13)
    ],
)
async def test_profile_unlocks_is_the_full_set_for_the_level(
    auth_client: AsyncClient,
    seeded_db,
    total_xp: int,
    level: int,
    expected_count: int,
):
    """US-35 AC-2: ``unlocks`` is the FULL set (D-2 filter), capped at 16 rows.

    L1 opens four entries and L13 all sixteen — past the PO caps of D-2 (13
    conversions, 4 unicode) nothing new opens, so ``unlocks(13)`` is the whole
    ladder and XP climbs for prestige only (§4.9 UN-01/UN-02 through HTTP).
    """
    _add_profile(
        seeded_db,
        USER_ID,
        total_xp=total_xp,
        level=level,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )
    await seeded_db.commit()

    payload = (await auth_client.get(PROFILE_URL)).json()

    assert payload["level"] == level
    assert len(payload["unlocks"]) == expected_count
    assert payload["unlocks"] == _unlocks_at(level)
    if expected_count == 16:
        assert payload["unlocks"] == list(UNLOCK_LADDER)


async def test_profile_unlock_element_shape_on_the_wire(
    auth_client: AsyncClient, seeded_db
):
    """US-35 AC-3 / R-9 on the wire: ``{mode, bit_width, min_level}``, plan order.

    The ONE-element-type claim is pinned at the schema level by
    ``tests/test_game_profile_models.py::test_unlock_element_type_is_shared``; what
    belongs here is that the JSON the client actually parses carries exactly those
    three keys, in seed order, with no extra field (``extra="forbid"``).
    """
    _add_profile(seeded_db, USER_ID, total_xp=476, level=4)
    await seeded_db.commit()

    unlocks = (await auth_client.get(PROFILE_URL)).json()["unlocks"]

    assert all(set(row) == {"mode", "bit_width", "min_level"} for row in unlocks)
    assert unlocks == _unlocks_at(4)
    # Seed order, so a client can render the ladder without re-sorting it.
    assert [row["mode"] for row in unlocks][:2] == ["hex_to_bin", "hex_to_bin"]


async def test_profile_requires_a_valid_token(client: AsyncClient):
    """US-35: 401 for a missing header — the same boundary as ``/answer`` (N17)."""
    response = await client.get(PROFILE_URL)

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid Authorization header."


async def test_profile_rejects_a_refresh_token(client: AsyncClient):
    response = await client.get(
        PROFILE_URL,
        headers={"Authorization": f"Bearer {create_refresh_token(USER_ID)}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token type."


async def test_profile_for_a_vanished_user_returns_404(client: AsyncClient):
    """US-35: a valid token whose user is gone is a 404, exactly as on ``/answer``.

    Plan US-35 freezes "404/401 semantics as US-33": 401 belongs to the token, 404
    to the identity. The same rule on ``/api/user/me`` is US-37 AC-3 (Rev 3 R-10),
    so the two authenticated reads never diverge on one condition.
    """
    ghost = "01960000-0000-7000-8000-000000000000"

    response = await client.get(PROFILE_URL, headers=_bearer(ghost))

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "User not found."


# --------------------------------------------------------------------------- #
# US-36: GET /api/game/leaderboard (v2 — by total XP, stays public)
# --------------------------------------------------------------------------- #

LEADERBOARD_URL = "/api/game/leaderboard"


async def test_leaderboard_entry_shape_has_no_points_and_no_pii(
    client: AsyncClient, seeded_db
):
    """US-36 AC-4: username plus game numbers — no ``points``, no email."""
    _add_profile(seeded_db, USER_ID, **PROFILE_GOLDEN_SEED)
    await seeded_db.commit()

    response = await client.get(LEADERBOARD_URL)

    assert response.status_code == 200, response.text
    data = response.json()
    assert list(data) == ["entries"]  # the unchanged wrapper
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry == {
        "user_id": USER_ID,
        "username": USER_USERNAME,
        "total_xp": 1320,
        "level": 6,
        "correct_count": 42,
        "incorrect_count": 5,
    }
    assert "points" not in entry
    assert "email" not in entry
    assert "secret" not in response.text


async def test_leaderboard_is_reachable_without_any_authorization(
    client: AsyncClient, seeded_db
):
    """US-36 AC-3 / R-3 regression pin: the leaderboard is PUBLIC.

    Pinned twice over — no header at all, and a *bogus* header that an
    "optional auth" refactor would try to reject — because the decision it defends
    is a PO decision the frontend depends on (``README`` documents it public, and
    the client polls it unauthenticated every 30 s). A future "parity with the
    other game endpoints" refactor has to break this test on purpose.
    """
    _add_profile(seeded_db, USER_ID, **PROFILE_GOLDEN_SEED)
    await seeded_db.commit()

    anonymous = await client.get(LEADERBOARD_URL)
    assert anonymous.status_code == 200, anonymous.text
    assert anonymous.json()["entries"][0]["user_id"] == USER_ID

    # A garbage credential is IGNORED, not rejected: there is no identity to
    # establish here, so nothing may turn a header into a 401.
    bogus = await client.get(
        LEADERBOARD_URL, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert bogus.status_code == 200, bogus.text
    assert bogus.json() == anonymous.json()


async def test_leaderboard_sorts_by_total_xp_then_tiebreaks(
    client: AsyncClient, seeded_db
):
    """US-36 AC-1: ``total_xp DESC``, tiebreak ``incorrect_count ASC``, ``user_id ASC``.

    Both tiebreak levels are exercised: three rows share one XP total, and the
    last two of those differ only in their primary key.
    """
    rows = (
        # (user_id, username, total_xp, incorrect_count)
        ("user-lb-a", "TieFewerMistakes", 1320, 2),  # same XP, fewer mistakes
        ("user-lb-b", "TieMoreMistakes", 1320, 9),  # tied on XP and mistakes,
        ("user-lb-c", "TieSameEverything", 1320, 9),  # ...so b < c by user_id
        ("user-lb-d", "TopScorer", 5000, 40),  # most mistakes, still first on XP
        ("user-lb-e", "BottomScorer", 4, 0),  # least XP, last despite zero mistakes
    )
    for user_id, username, total_xp, incorrect in rows:
        _add_verified_user(seeded_db, user_id, username, f"{user_id}@example.com")
        _add_profile(
            seeded_db,
            user_id,
            total_xp=total_xp,
            level=1,
            current_streak=0,
            best_streak=0,
            correct_count=1,
            incorrect_count=incorrect,
        )
    await seeded_db.commit()

    response = await client.get(LEADERBOARD_URL)

    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    # USER_ID has no profile row, so it is not on the board at all (D-6).
    assert [e["username"] for e in entries] == [
        "TopScorer",  # 5000 XP — XP outranks its 40 mistakes
        "TieFewerMistakes",  # 1320 XP, 2 mistakes — tiebreak 1 puts it first
        "TieMoreMistakes",  # 1320 XP, 9 mistakes
        "TieSameEverything",  # identical to the row above but a higher user_id
        "BottomScorer",  # 4 XP — last despite its clean record
    ]
    assert [e["user_id"] for e in entries] == [
        "user-lb-d",
        "user-lb-a",
        "user-lb-b",
        "user-lb-c",
        "user-lb-e",
    ]
    assert [e["total_xp"] for e in entries] == [5000, 1320, 1320, 1320, 4]
    assert [e["incorrect_count"] for e in entries] == [40, 2, 9, 9, 0]


# Enough rows to make the DEFAULT of 50 a measurement rather than a schema read.
_MANY_ROWS = 60


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        pytest.param(None, 50, id="absent-defaults-to-50"),
        pytest.param(1, 1, id="at-the-floor"),
        pytest.param(3, 3, id="inside-the-range"),
        pytest.param(100, 60, id="at-the-ceiling-returns-every-row-that-exists"),
    ],
)
async def test_leaderboard_limit_bounds(
    client: AsyncClient, seeded_db, limit: int | None, expected: int
):
    """US-36 AC-2: ``limit`` is bounded 1..100 (BE-6), default 50.

    Sixty ranked rows so the default is *measured* on the wire instead of read off
    the OpenAPI schema: an absent limit returns exactly the top 50, and the two
    boundaries (1 and 100) are accepted while 0 / 101 / non-integers are 422s —
    see :func:`test_leaderboard_rejects_an_out_of_range_limit`.
    """
    for index in range(_MANY_ROWS):
        user_id = f"user-many-{index:03d}"
        _add_verified_user(seeded_db, user_id, f"many{index:03d}", f"{user_id}@ex.com")
        _add_profile(
            seeded_db,
            user_id,
            total_xp=index,
            level=1,
            current_streak=0,
            best_streak=0,
            correct_count=0,
            incorrect_count=0,
        )
    await seeded_db.commit()

    url = LEADERBOARD_URL if limit is None else f"{LEADERBOARD_URL}?limit={limit}"
    response = await client.get(url)

    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert len(entries) == expected
    # Highest XP first, so a truncated board is the TOP of the board.
    top = _MANY_ROWS - 1
    assert [e["total_xp"] for e in entries] == list(range(top, top - expected, -1))


@pytest.mark.parametrize("limit", [0, 101, 999, -5, "abc", "1.5"])
async def test_leaderboard_rejects_an_out_of_range_limit(client: AsyncClient, limit):
    """0, 101, non-integers: a 422 from the query schema, never an unbounded query.

    The bound is the abuse control of a PUBLIC endpoint (R-3/OQ-9): without it a
    caller asks for the whole table, which is exactly the enumeration the
    ``limit`` exists to cap.
    """
    response = await client.get(f"{LEADERBOARD_URL}?limit={limit}")

    assert response.status_code == 422, response.text


async def test_leaderboard_reads_the_profile_table_not_user_stats(
    client: AsyncClient, seeded_db
):
    """D-6 co-existence: a legacy ``user_stats`` row alone is NOT a leaderboard entry.

    The board reads ``user_game_profile``; the legacy table survives until US-34
    but feeds nothing here.
    """
    _add_verified_user(
        seeded_db, "user-legacy-only", "LegacyOnly", "legacy@example.com"
    )
    seeded_db.add(
        UserStats(user_id="user-legacy-only", correct_count=99, points=999_999)
    )
    await seeded_db.commit()

    entries = (await client.get(LEADERBOARD_URL)).json()["entries"]

    assert [e["user_id"] for e in entries] == []


async def test_leaderboard_level_is_derived_from_total_xp(
    client: AsyncClient, seeded_db
):
    """D-1: the rank's level is a function of ``total_xp``, not a stored coincidence."""
    _add_profile(
        seeded_db,
        USER_ID,
        total_xp=1320,
        level=6,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )
    await seeded_db.commit()

    entry = (await client.get(LEADERBOARD_URL)).json()["entries"][0]

    assert (entry["total_xp"], entry["level"]) == (1320, 6)


# --------------------------------------------------------------------------- #
# The service-side half of each bound (US-35/US-36, no HTTP in between)
# --------------------------------------------------------------------------- #
# Both guards are unreachable over the wire — the bearer parser rejects a subjectless
# token before ``load_snapshot`` sees an empty identity, and the router's ``Query``
# constraints answer an out-of-range ``limit`` with a 422 — so no endpoint test can
# cover them. They exist for the NEXT caller (a CLI report, a WebSocket broadcast),
# which is precisely the caller that will not be stopped by an HTTP schema, so they are
# tested against the service functions directly instead of being left as prose.


async def test_load_snapshot_refuses_an_empty_identity(db_session):
    """Mirror of ``game_service.record_answer``: a blank identity never reaches SQL."""
    with pytest.raises(ValueError, match="non-empty"):
        await load_snapshot(db_session, "")


async def test_load_snapshot_answers_the_zero_state_without_a_row(db_session):
    """A missing row is the D-1/OQ-3 state: not an error, not an insert (US-28 AC-3)."""
    snapshot = await load_snapshot(db_session, "user-without-a-row")

    assert snapshot.total_xp == 0
    assert snapshot.level == 1
    assert (snapshot.correct_count, snapshot.incorrect_count) == (0, 0)


async def test_profile_response_derives_level_and_progress_from_one_total(db_session):
    """One ``total_xp`` yields level, span and ladder, so they cannot drift apart."""
    _add_verified_user(db_session, USER_ID, USER_USERNAME, USER_EMAIL)
    _add_profile(db_session, USER_ID, **PROFILE_GOLDEN_SEED)
    await db_session.commit()

    body = profile_response(await load_snapshot(db_session, USER_ID)).model_dump()

    assert body == PROFILE_GOLDEN


@pytest.mark.parametrize("limit", [0, 101, 999, -5])
async def test_leaderboard_entries_rejects_an_unbounded_request(db_session, limit):
    """The bound behind the public endpoint, enforced where no ``Query`` can help."""
    with pytest.raises(ValueError, match="between"):
        await leaderboard_entries(db_session, limit)


async def test_leaderboard_entries_returns_an_empty_board_before_the_first_answer(
    db_session,
):
    """Nobody has played yet -> ``[]``; the wrapper is the router's, not this one's."""
    assert await leaderboard_entries(db_session) == []
