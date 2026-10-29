"""US-33: ``POST /api/game/answer`` — the fusion point of the whole economy.

One handler, four responsibilities in a MANDATORY order (the security finding):
``authenticate → validate body → rate-limit → unlock-gate → transact``. Every test
here is an assertion about that order or about what the order guarantees:

* the golden fixture of ``docs/sprint4-plan.md`` US-33 reproduces byte-for-byte
  (section A) — the frozen wire contract, plan literals only;
* every rejection path leaves the profile row BYTE-IDENTICAL, ``updated_at``
  included (section B, N10-N14) — a refused answer is not a partial write;
* auth fails with 401 and ZERO SQL on the wire (section C, N17/N18), concurrent
  answers serialize on the ``FOR UPDATE`` lock (N19), the unlock delta is computed
  from ``(previous_level, level)`` only (N21), and the gate reads the LOCKED
  PRE-answer level so an answer can never unlock itself (N22/N23, finding F-2);
* the economy edges are reachable end-to-end: ``xp_delta == 0`` at the OQ-4 floor
  (N24), every applied delta is a multiple of 4 (N25, D-8), strict typing (N26)
  and a level that never falls (N27);
* Rev 3 R-2: the limiter runs on its OWN session, commits nothing of the answer
  transaction, and issues no ``DELETE`` on the request path (section E).

The database is the real PostgreSQL test instance (conftest's ``async_engine``),
never the SQLite of ``tests/test_game.py``: ``FOR UPDATE`` is a silent no-op on
SQLite, so every serialization claim would be theatre there.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.database.engine import get_db
from src.middleware.body_size import ANSWER_BODY_LIMIT_BYTES, ANSWER_BODY_SIZE_PATH
from src.models.auth import User
from src.models.game import UserGameProfile
from src.routers.game import answer_rate_limit_session
from src.services.answer_service import rate_limit_key
from src.services.game_config import (
    ANSWER_RATE_LIMIT,
    ANSWER_RATE_WINDOW_SECONDS,
)
from src.services.level_service import (
    cumulative_xp_for_level,
    level_from_xp,
    xp_progress,
)
from src.services.rate_limit_service import RateLimitService
from src.services.unlock_service import required_level_for, unlocks_for_level
from src.utils.jwt_utils import (
    JWT_ALGORITHM,
    create_access_token,
    create_refresh_token,
    get_jwt_secret,
)
from uuid_utils import uuid7

# --------------------------------------------------------------------------- #
# Plan literals — copied from docs/sprint4-plan.md, never derived from src/, so a
# wrong constant cannot hide behind a wrong expectation (US-26/US-29 convention).
# --------------------------------------------------------------------------- #

# US-33 golden fixture: HB-8 (= hex_to_bin at 8 bits) on post-increment streak 3.
GOLDEN_REQUEST = {"mode": "hex_to_bin", "bit_width": 8, "is_correct": True}
# The 200 body, byte for byte (plan US-33 "normative golden fixture").
GOLDEN_RESPONSE = {
    "xp_delta": 40,  # 32 core x 5/4 (XP-G04)
    "total_xp": 1320,  # cum(6)
    "level": 6,
    "xp_in_level": 0,  # landed exactly on the threshold
    "xp_required_for_next_level": 760,  # req(6), the span 6 -> 7
    "current_streak": 3,
    "best_streak": 7,
    "correct_count": 42,
    "incorrect_count": 5,
    "newly_unlocked": [],  # the D-2 seed has no unlock at L6 (R-9 toast case)
}
# The row that fixture needs BEFORE the answer: 1320 - 40 = 1280 XP, streak 2.
GOLDEN_SEED = {
    "total_xp": 1280,
    "level": 5,  # cum(5)=812 <= 1280 < cum(6)=1320
    "current_streak": 2,
    "best_streak": 7,
    "correct_count": 41,
    "incorrect_count": 5,
}

# §4.4 cores (base_xp * mode_mult) as hand-multiplied plan-table literals, and
# §4.5 tiers (1-2 x1, 3-4 x5/4, 5-7 x3/2, 8-10 x2, 11+ x5/2) / §4.7 penalty
# (= core/2, never streak-scaled). Expected values below are these products
# worked out by hand, NOT calls into xp_engine.
# (mode, bit_width, post_increment_streak, expected_xp_delta)
SECTION_4_6_CORRECT: tuple[tuple[str, int, int, int], ...] = (
    ("hex_to_bin", 4, 1, 16),  # core 16 x1
    ("hex_to_bin", 8, 3, 40),  # core 32 x5/4  (the golden cell)
    ("hex_to_bin", 32, 7, 192),  # core 128 x3/2
    ("hex_to_dec", 8, 2, 96),  # core 96 x1
    ("hex_to_dec", 32, 11, 960),  # core 384 x5/2 (cap)
    ("bin_to_dec", 4, 4, 160),  # core 128 x5/4
    ("bin_to_dec", 16, 5, 768),  # core 512 x3/2
    ("bin_to_dec", 32, 11, 2560),  # core 1024 x5/2 — XP-G14, the system maximum
    ("unicode", 8, 8, 32),  # core 16 x2
    ("unicode", 24, 1, 64),  # core 64 x1
)
# An incorrect answer scores -(core/2), whatever the streak (D-5).
SECTION_4_6_INCORRECT: tuple[tuple[str, int, int], ...] = (
    ("hex_to_bin", 4, 8),  # core 16 / 2
    ("hex_to_bin", 16, 32),  # core 64 / 2
    ("hex_to_dec", 8, 48),  # core 96 / 2
    ("hex_to_dec", 32, 192),  # core 384 / 2
    ("bin_to_dec", 16, 256),  # core 512 / 2
    ("bin_to_dec", 32, 512),  # core 1024 / 2
    ("unicode", 24, 32),  # core 64 / 2
    ("unicode", 32, 64),  # core 128 / 2
)

_ROW_SQL = text(
    "SELECT total_xp, level, current_streak, best_streak, correct_count, "
    "incorrect_count, updated_at FROM user_game_profile WHERE user_id = :u"
)
_COLUMNS = (
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
    "updated_at",
)

# The three paths one economy state is reported on, spelled once each.
ANSWER_URL = "/api/game/answer"
PROFILE_URL = "/api/game/profile"
ME_URL = "/api/user/me"

# The views differ by exactly these fields (Phase 2b M2): /answer is the only one that
# reports the delta and the unlock it caused, /profile the only one that reports the
# whole ladder, /me the only one that reports identity (US-37). Everything else is one
# row seen three times, so the comparison below is a contract and not a coincidence.
ANSWER_ONLY_FIELDS: tuple[str, ...] = ("xp_delta", "newly_unlocked")
PROFILE_ONLY_FIELDS: tuple[str, ...] = ("unlocks",)
# The six economy fields /me carries — the progress pair is not among them (D-1).
ME_ECONOMY_FIELDS: tuple[str, ...] = (
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
)


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #


async def _seed_user(db: AsyncSession, *, username_prefix: str = "us33") -> str:
    """A unique ``users`` row (the shared test DB keeps rows across tests).

    The username carries uuid7's RANDOM tail: two ids minted in one millisecond
    share their leading 20 hex digits, and ``users.username`` is unique, so the
    front of the id would collide exactly when two tests run close together.
    """
    user_id = str(uuid7())
    token = user_id.replace("-", "")
    db.add(
        User(
            id=user_id,
            username=f"{username_prefix}-{token[-12:]}",
            email=f"{token}@example.com",
        )
    )
    await db.commit()
    return user_id


async def _seed_profile(db: AsyncSession, user_id: str, **columns: int) -> None:
    """Put a committed economy row in place so an answer has something to score."""
    profile = UserGameProfile(user_id=user_id, **columns)
    db.add(profile)
    await db.commit()


async def _row(db: AsyncSession, user_id: str) -> tuple | None:
    """The persisted row over raw SQL — the identity map must not answer for us."""
    return (await db.execute(_ROW_SQL, {"u": user_id})).first()


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
async def db(async_engine) -> AsyncSession:
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest.fixture
async def client(async_engine):
    """The real app with BOTH of /answer's sessions pointed at the test database.

    ``answer_rate_limit_session`` is overridden alongside ``get_db`` — the pair is
    the point of Rev 3 R-2, and overriding both against one engine keeps the
    limiter's and the answer's isolation real (two sessions, two transactions)
    while staying inside the test database.

    ``leaderboard_rate_limit_session`` is also overridden (Security Review 2026-10-29)
    because the public leaderboard endpoint now has per-IP rate limiting for flood
    protection, and tests need to use the same session override as the other endpoints.
    """
    from src.main import app

    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[answer_rate_limit_session] = _session
    # Import here to avoid circular dependency
    from src.routers.game import leaderboard_rate_limit_session
    app.dependency_overrides[leaderboard_rate_limit_session] = _session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@contextmanager
def _statement_log(async_engine) -> Iterator[list[str]]:
    """Every SQL statement the engine receives, in order."""
    log: list[str] = []

    def _capture(sync_conn, cursor, statement, *args, **kwargs) -> None:
        log.append(" ".join(statement.split()))

    event.listen(async_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        yield log
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _capture)


def _mentions(log: list[str], table: str) -> list[str]:
    return [entry for entry in log if table in entry.lower()]


async def _fill_answer_rate_limit(
    async_engine, user_id: str, *, count: int = ANSWER_RATE_LIMIT
) -> None:
    """Consume the caller's answer budget on the limiter's own session (no HTTP)."""
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        service = RateLimitService(session)
        for _ in range(count):
            allowed = await service.check_without_cleanup(
                rate_limit_key(user_id),
                ANSWER_RATE_LIMIT,
                ANSWER_RATE_WINDOW_SECONDS,
            )
            assert allowed is True


# --------------------------------------------------------------------------- #
# A. The frozen contract: golden fixture + per-mode reachability
# --------------------------------------------------------------------------- #


async def test_golden_fixture_reproduces_byte_for_byte(client, db, async_engine):
    """US-33 AC-1: the plan's normative example, every field, no extra keys.

    HB-8 at post-increment streak 3 (D-3): award 40 = 32 x 5/4, total 1280 + 40 =
    1320 = cum(6), so level 6 (LV-07 boundary inclusive), ``xp_in_level`` 0 and
    ``xp_required_for_next_level`` = req(6) = 760 — and ``newly_unlocked`` empty
    although this WAS a level-up, because the D-2 seed has no unlock at L6 (R-9).
    """
    user_id = await _seed_user(db)
    await _seed_profile(db, user_id, **GOLDEN_SEED)

    response = await client.post(
        ANSWER_URL, json=GOLDEN_REQUEST, headers=_headers(user_id)
    )

    assert response.status_code == 200, response.text
    assert response.json() == GOLDEN_RESPONSE


async def test_golden_fixture_numbers_agree_with_the_pure_layer(
    client, db, async_engine
):
    """The same fixture, cross-checked against the modules instead of the plan.

    The golden test above is the contract; this one is the drift detector: if
    ``xp_engine`` / ``level_service`` ever change under US-33, the endpoint and the
    pure layer must still tell the same story (the pure modules are authoritative
    — 1071 green tests — so a disagreement here is a US-33 wiring bug).
    """
    user_id = await _seed_user(db)
    await _seed_profile(db, user_id, **GOLDEN_SEED)

    response = await client.post(
        ANSWER_URL, json=GOLDEN_REQUEST, headers=_headers(user_id)
    )
    payload = response.json()

    expected_total = GOLDEN_SEED["total_xp"] + 40
    assert expected_total == cumulative_xp_for_level(6) == 1320
    assert level_from_xp(expected_total) == 6
    assert xp_progress(expected_total) == (0, 760)
    assert (payload["total_xp"], payload["level"]) == (expected_total, 6)
    assert (
        payload["xp_in_level"],
        payload["xp_required_for_next_level"],
    ) == xp_progress(expected_total)


async def test_answer_then_profile_and_me_agree_field_for_field(
    client, db, async_engine
):
    """M2 (Phase 2b): the write → read contract, one bearer, three endpoints.

    The golden fixture's answer, then the two authenticated reads of the same user —
    three requests, three transactions, so nothing here can pass on a shared object.
    ``/profile`` has to be ``/answer`` minus the two answer-only fields; ``/me`` has to
    match both on the six economy numbers it carries. That is H1 on the wire: the four
    report paths share the D-1 derivation of ``level`` from ``total_xp``, never a row
    one of them cached (``tests/test_game.py`` pins the same claim on a divergent
    stored level).
    """
    user_id = await _seed_user(db)
    await _seed_profile(db, user_id, **GOLDEN_SEED)
    headers = _headers(user_id)

    answer = (
        await client.post(ANSWER_URL, json=GOLDEN_REQUEST, headers=headers)
    ).json()
    profile = (await client.get(PROFILE_URL, headers=headers)).json()
    me = (await client.get(ME_URL, headers=headers)).json()

    assert answer == GOLDEN_RESPONSE  # the fixture this contract hangs off
    assert (answer["total_xp"], answer["level"]) == (1320, 6)

    answer_view = {k: v for k, v in answer.items() if k not in ANSWER_ONLY_FIELDS}
    profile_view = {k: v for k, v in profile.items() if k not in PROFILE_ONLY_FIELDS}
    assert profile_view == answer_view
    assert {k: me[k] for k in ME_ECONOMY_FIELDS} == {
        k: answer[k] for k in ME_ECONOMY_FIELDS
    }

    # The ladder follows the derived level, and the row now stores the same number the
    # three reads reported — the writer and the readers agree by construction (H1).
    assert profile["unlocks"] == [
        {"mode": rule.mode, "bit_width": rule.bit_width, "min_level": rule.min_level}
        for rule in unlocks_for_level(6)
    ]
    row = await _row(db, user_id)
    assert (row.total_xp, row.level) == (1320, 6)


@pytest.mark.parametrize(
    ("mode", "bit_width", "streak", "expected_delta"),
    SECTION_4_6_CORRECT,
)
async def test_section_4_6_correct_cases_are_reachable_end_to_end(
    client, db, async_engine, mode, bit_width, streak, expected_delta
):
    """US-33 AC: at least one §4.6 representative per mode, through the endpoint.

    Seeded at level 13 so every combo is unlocked (the D-2 caps are 13 / 4), with
    ``current_streak = streak - 1`` because the tier is looked up on the
    POST-increment value (D-3/OQ-5). Expected deltas are hand-multiplied §4.4/§4.5
    literals.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=cumulative_xp_for_level(13) + 4,
        level=13,
        current_streak=streak - 1,
        best_streak=streak - 1,
        correct_count=100,
        incorrect_count=0,
    )

    response = await client.post(
        ANSWER_URL,
        json={"mode": mode, "bit_width": bit_width, "is_correct": True},
        headers=_headers(user_id),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["xp_delta"] == expected_delta, (mode, bit_width, streak)
    assert payload["total_xp"] == cumulative_xp_for_level(13) + 4 + expected_delta
    assert payload["current_streak"] == streak
    assert payload["correct_count"] == 101
    assert payload["incorrect_count"] == 0


@pytest.mark.parametrize(
    ("mode", "bit_width", "expected_penalty"),
    SECTION_4_6_INCORRECT,
)
async def test_section_4_6_penalties_are_reachable_end_to_end(
    client, db, async_engine, mode, bit_width, expected_penalty
):
    """Every mode's §4.7 penalty, with margin above the OQ-4 floor.

    The seed sits at ``cum(13) + 12000`` — comfortably inside level 13, so the full
    penalty applies (LV-09a) instead of being truncated by the floor, and the
    streak resets to 0 while ``best_streak`` survives (D-5).
    """
    user_id = await _seed_user(db)
    seed_total = cumulative_xp_for_level(13) + 12_000
    await _seed_profile(
        db,
        user_id,
        total_xp=seed_total,
        level=level_from_xp(seed_total),
        current_streak=6,
        best_streak=9,
        correct_count=3,
        incorrect_count=4,
    )

    response = await client.post(
        ANSWER_URL,
        json={"mode": mode, "bit_width": bit_width, "is_correct": False},
        headers=_headers(user_id),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["xp_delta"] == -expected_penalty, (mode, bit_width)
    assert payload["total_xp"] == seed_total - expected_penalty
    assert payload["current_streak"] == 0  # D-5: reset, never negative
    assert payload["best_streak"] == 9  # high-water mark untouched
    assert (payload["correct_count"], payload["incorrect_count"]) == (3, 5)
    assert payload["newly_unlocked"] == []


async def test_response_carries_exactly_the_frozen_field_set(client, db, async_engine):
    """US-26 froze the contract: ten keys, no more, no fewer (extra="forbid")."""
    user_id = await _seed_user(db)

    response = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": True},
        headers=_headers(user_id),
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == set(GOLDEN_RESPONSE)


async def test_first_ever_answer_creates_the_row_lazily(client, db, async_engine):
    """US-28 AC-3 through the endpoint: no row, one answer, one row afterwards."""
    user_id = await _seed_user(db)
    assert await _row(db, user_id) is None

    response = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_dec", "bit_width": 4, "is_correct": True},
        headers=_headers(user_id),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert (payload["current_streak"], payload["best_streak"]) == (1, 1)
    assert payload["total_xp"] == 48  # §4.2 base 24 x §4.3 mult 2 = core 48, tier x1
    assert payload["xp_delta"] == 48
    row = await _row(db, user_id)
    assert row is not None
    assert (row.total_xp, row.level) == (48, 1)  # still L1: cum(2) = 100


#: The one table the answer path is allowed to write besides the economy row: the
#: limiter's own bookkeeping, which R-2 puts on its own session and its own table.
#: Named here (not derived from ``src/``) so the exemption cannot quietly widen.
WRITE_EXEMPT_TABLES: tuple[str, ...] = ("rate_limit_attempt",)


async def test_every_write_of_the_answer_path_targets_the_economy_table(
    client, db, async_engine
):
    """D-6's endpoint half, restated for the post-US-34 schema: one write target.

    The co-existence this used to assert (``/answer`` leaves the legacy table alone)
    is structural now — US-34 deleted that table and its model, so the only way this
    claim can ever fail again is a writer that acquires a SECOND target. The
    statement stream is what catches that: every INSERT or UPDATE the answer path
    emits, the limiter's bookkeeping aside, lands on ``user_game_profile``. Reads
    (``users`` for the identity check) are deliberately unconstrained — this is a
    write-side invariant, and it is pinned on the SQL Postgres receives rather than
    on the ORM calls the handler happens to make.

    ``bit_width=4`` because the unlock gate (D-2) runs first: this user has no XP, so
    anything wider would be a 403 and the stream would prove nothing.
    """
    user_id = await _seed_user(db)

    with _statement_log(async_engine) as log:
        for is_correct in (True, False):
            response = await client.post(
                ANSWER_URL,
                json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": is_correct},
                headers=_headers(user_id),
            )
            assert response.status_code == 200, response.text

    writes = [
        entry
        for entry in log
        if re.match(r"^(INSERT|UPDATE)\b", entry, flags=re.IGNORECASE)
        and not any(needle in entry.lower() for needle in WRITE_EXEMPT_TABLES)
    ]
    assert writes, log  # two answers must have written something
    for entry in writes:
        assert "user_game_profile" in entry.lower(), entry


# --------------------------------------------------------------------------- #
# B. N10-N14: every rejection leaves the row byte-identical, updated_at included
# --------------------------------------------------------------------------- #


async def _assert_row_untouched(db, user_id: str, before: tuple) -> None:
    """The strongest cheap claim on a rejection path: nothing at all changed.

    ``updated_at`` is part of the compared tuple on purpose — the ORM stamps it via
    the column's ``onupdate`` (US-26), so a row that was even *touched* by a
    refused answer would show a new timestamp even if every number looked right.
    """
    after = await _row(db, user_id)
    if after != before:
        seeded = dict(zip(_COLUMNS, before, strict=True))
        now = dict(zip(_COLUMNS, after, strict=True)) if after is not None else None
        pytest.fail(f"a rejection path moved the row: before={seeded}, after={now}")


async def test_n10_invalid_body_leaves_the_row_byte_identical(client, db, async_engine):
    """N10: a 422 is refused before the transaction opens — row untouched."""
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=3,
        best_streak=3,
        correct_count=3,
        incorrect_count=1,
    )
    before = await _row(db, user_id)

    response = await client.post(
        ANSWER_URL,
        json={
            "mode": "unicode",
            "bit_width": 4,
            "is_correct": True,
        },  # not a combo (D-7)
        headers=_headers(user_id),
    )

    assert response.status_code == 422
    await _assert_row_untouched(db, user_id, before)


async def test_n11_rate_limited_answer_leaves_the_row_byte_identical(
    client, db, async_engine
):
    """N11 / US-32 AC-3: the 429 path never opens a row lock and writes nothing."""
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=3,
        best_streak=3,
        correct_count=3,
        incorrect_count=1,
    )
    before = await _row(db, user_id)
    await _fill_answer_rate_limit(async_engine, user_id)

    response = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True},
        headers=_headers(user_id),
    )

    assert response.status_code == 429
    assert response.headers.get("retry-after") == str(ANSWER_RATE_WINDOW_SECONDS)
    await _assert_row_untouched(db, user_id, before)


async def test_n12_locked_combo_answer_leaves_the_row_byte_identical(
    client, db, async_engine
):
    """N12: the 403 fires inside the transaction, on the locked row — still no write."""
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=812,
        level=5,
        current_streak=0,
        best_streak=0,
        correct_count=1,
        incorrect_count=0,
    )
    before = await _row(db, user_id)

    response = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 16, "is_correct": True},  # needs L8
        headers=_headers(user_id),
    )

    assert response.status_code == 403
    await _assert_row_untouched(db, user_id, before)


async def test_n13_unauthenticated_answer_leaves_the_row_byte_identical(
    client, db, async_engine
):
    """N13: 401 before any SQL at all (see also N17/N18)."""
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=2,
        best_streak=2,
        correct_count=2,
        incorrect_count=0,
    )
    before = await _row(db, user_id)

    response = await client.post(
        ANSWER_URL, json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True}
    )

    assert response.status_code == 401
    await _assert_row_untouched(db, user_id, before)


async def test_n14_unknown_user_leaves_no_row_and_404s(client, db, async_engine):
    """N14: a token whose user is gone 404s instead of tripping the FK (US-28)."""
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=1,
        best_streak=1,
        correct_count=1,
        incorrect_count=0,
    )
    before = await _row(db, user_id)
    ghost = str(uuid7())  # a valid token, no users row -> the FK would reject it

    response = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True},
        headers=_headers(ghost),
    )

    assert response.status_code == 404, response.text
    assert await _row(db, ghost) is None  # no lazily created orphan
    assert await _row(db, user_id) == before  # and nobody else's row moved


# --------------------------------------------------------------------------- #
# C. N17-N23: auth costs ZERO SQL, the lock serializes, the gate is PRE-answer
# --------------------------------------------------------------------------- #

# A syntactically valid UUID with no ``users`` row behind it: the token decodes,
# so these cases reach the auth boundary rather than the 404 lookup.
_GHOST = "01960000-0000-7000-8000-000000000000"


def _expired_access_token(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        get_jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


AUTH_FAILURES: tuple[tuple[str, dict[str, str], str], ...] = (
    ("no header at all", {}, "Missing or invalid Authorization header."),
    (
        "a non-Bearer scheme",
        {"Authorization": f"Token {create_access_token(_GHOST)}"},
        "Missing or invalid Authorization header.",
    ),
    (
        "a malformed token",
        {"Authorization": "Bearer not-a-jwt"},
        "Invalid token.",
    ),
    (
        "an expired access token",
        {"Authorization": f"Bearer {_expired_access_token(_GHOST)}"},
        "Token has expired.",
    ),
    (
        "a refresh token (wrong type)",
        {"Authorization": f"Bearer {create_refresh_token(_GHOST)}"},
        "Invalid token type.",
    ),
)


@pytest.mark.parametrize(
    ("case", "headers", "detail"),
    AUTH_FAILURES,
    ids=[name for name, _, _ in AUTH_FAILURES],
)
async def test_n17_n18_auth_failure_is_401_with_zero_sql(
    client, async_engine, case, headers, detail
):
    """N17/N18: authentication happens before anything touches the database.

    The claim is stronger than a status code: not one statement may reach the
    engine, because a query in front of the 401 would let an unauthenticated
    caller probe row existence, burn a pooled connection, and — since the answer
    path holds two of them (Rev 3 R-2) — turn a rejected request into pool
    pressure.
    """
    with _statement_log(async_engine) as log:
        response = await client.post(
            ANSWER_URL,
            json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True},
            headers=headers,
        )

    assert response.status_code == 401, (case, response.text)
    assert response.json()["detail"] == detail
    assert log == [], f"SQL ran before the 401: {log}"


async def test_n19_concurrent_answers_serialize_on_the_row_lock(
    client, db, async_engine
):
    """N19: two simultaneous submissions of one user lose nothing (US-28 AC-1).

    Both answers are the same legal combo on the same streak-0 seed, so the
    outcome is order-independent — but only serialization makes it *exact*: a
    lost update would leave ``correct_count`` at 1 while two 200s were handed out.
    Mirrors the concurrency proof in ``tests/test_game_service.py`` one layer up,
    through HTTP.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=0,
        level=1,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )

    async def _post() -> int:
        response = await client.post(
            ANSWER_URL,
            json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": True},
            headers=_headers(user_id),
        )
        assert response.status_code == 200, response.text
        return response.json()["current_streak"]

    streaks = sorted(await asyncio.gather(_post(), _post()))

    assert streaks == [1, 2]  # the second waiter saw the first one's commit
    row = await _row(db, user_id)
    assert (row.correct_count, row.current_streak, row.best_streak) == (2, 2, 2)
    assert row.total_xp == 32  # 16 + 16, both at tier x1 (§4.5 streaks 1 and 2)


async def test_n20_a_replayed_answer_is_scored_again_by_design(
    client, db, async_engine
):
    """N20 (replay): there is no idempotency key, so a resend scores again.

    Deliberate, and the reason the rate limiter is a security control rather than
    a courtesy: the server cannot tell a retry from a second submission, so the
    120/60 s budget (US-32) is what bounds how much a replaying client can farm.
    The name of this test is the documentation of that decision — if a later story
    adds an idempotency key, this test is the one that must change on purpose.
    """
    user_id = await _seed_user(db)
    body = {"mode": "bin_to_dec", "bit_width": 4, "is_correct": True}

    first = await client.post(ANSWER_URL, json=body, headers=_headers(user_id))
    replay = await client.post(ANSWER_URL, json=body, headers=_headers(user_id))

    assert first.status_code == replay.status_code == 200
    payloads = (first.json(), replay.json())
    # Same request, scored twice: the award is identical (bin_to_dec 4 = core 128,
    # and §4.5 tiers x1 cover streaks 1 AND 2), while the state it reports has
    # advanced one step. That asymmetry is exactly what an idempotency key would
    # remove — and what the 120/60 s budget bounds instead.
    assert {payload["xp_delta"] for payload in payloads} == {128}
    assert [payload["current_streak"] for payload in payloads] == [1, 2]
    assert [payload["correct_count"] for payload in payloads] == [1, 2]
    row = await _row(db, user_id)
    assert (row.total_xp, row.correct_count) == (256, 2)


async def test_n21_newly_unlocked_is_the_delta_across_the_levels_crossed(
    client, db, async_engine
):
    """N21: ``newly_unlocked(previous_level, level)`` — never the full ladder.

    Seeded at L2 (``total_xp`` 248, one multiple-of-4 step under ``cum(3)`` = 252)
    with a streak-10 answer worth 320 (bin_to_dec 4 = core 128 x 5/2), the single
    answer lands on 568 = L4. The payload must therefore be exactly the seed rows
    whose ``min_level`` is 3 or 4 — the unicode-24 step and the whole 8-bit shelf —
    and must not repeat anything L1/L2 already had.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=248,
        level=2,
        current_streak=10,
        best_streak=10,
        correct_count=40,
        incorrect_count=1,
    )

    response = await client.post(
        ANSWER_URL,
        json={"mode": "bin_to_dec", "bit_width": 4, "is_correct": True},
        headers=_headers(user_id),
    )

    payload = response.json()
    assert response.status_code == 200, response.text
    assert (payload["total_xp"], payload["level"]) == (568, 4)

    already_open = {(rule.mode, rule.bit_width) for rule in unlocks_for_level(2)}
    expected = [
        {"mode": rule.mode, "bit_width": rule.bit_width, "min_level": rule.min_level}
        for rule in unlocks_for_level(4)
        if (rule.mode, rule.bit_width) not in already_open
    ]
    assert payload["newly_unlocked"] == expected
    assert expected, "the L2 -> L4 step must actually open something"
    assert all(entry["min_level"] in (3, 4) for entry in payload["newly_unlocked"])
    assert not already_open & {
        (entry["mode"], entry["bit_width"]) for entry in payload["newly_unlocked"]
    }


async def test_n22_403_detail_names_the_required_level(client, db, async_engine):
    """N22: the 403 says WHICH level unlocks it, taken from the D-2 seed.

    The same user first scores the combo that opens AT L4 (unicode 32, so the gate
    is ``>=`` and not ``>``) and is then refused the L13 combo, with the detail
    naming 13 — the number ``unlock_service.required_level_for`` holds, never a
    value the router invented.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=0,
        best_streak=0,
        correct_count=1,
        incorrect_count=0,
    )

    open_at_four = await client.post(
        ANSWER_URL,
        json={"mode": "unicode", "bit_width": 32, "is_correct": True},
        headers=_headers(user_id),
    )
    assert open_at_four.status_code == 200, open_at_four.text

    locked = await client.post(
        ANSWER_URL,
        json={"mode": "hex_to_dec", "bit_width": 32, "is_correct": True},
        headers=_headers(user_id),
    )

    assert locked.status_code == 403
    assert required_level_for("hex_to_dec", 32) == 13
    assert "Content locked until level 13" in locked.json()["detail"]


async def test_n23_the_gate_reads_the_locked_PRE_answer_level(client, db, async_engine):
    """N23 / finding F-2: an answer may never unlock itself.

    The seed is 3216 XP at L7 — 4 XP below ``cum(8)`` = 3220 — and the answer is
    worth 768 (bin_to_dec 16 = core 512 x 3/2), i.e. it WOULD have reached L8, the
    level that combo requires. Reading the level after the XP is applied would
    score it; the gate reads the ``FOR UPDATE`` locked pre-answer level, so it is a
    403 and the row is untouched.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=3216,
        level=7,
        current_streak=4,
        best_streak=4,
        correct_count=9,
        incorrect_count=2,
    )
    before = await _row(db, user_id)

    response = await client.post(
        ANSWER_URL,
        json={"mode": "bin_to_dec", "bit_width": 16, "is_correct": True},
        headers=_headers(user_id),
    )

    assert response.status_code == 403, response.text
    assert "Content locked until level 8" in response.json()["detail"]
    await _assert_row_untouched(db, user_id, before)


# --------------------------------------------------------------------------- #
# D. N24-N27: the economy edges are reachable end-to-end
# --------------------------------------------------------------------------- #


async def test_n24_the_oq4_floor_makes_xp_delta_zero(client, db, async_engine):
    """N24 / LV-09b: a penalty at the floor is absorbed, delta exactly 0.

    ``total_xp`` sits ON ``cum(5)`` = 812, so ``clamped_total_xp`` floors the
    128-point penalty (bin_to_dec 8 = core 256 / 2) at 812 itself: the level holds,
    ``xp_in_level`` stays 0, and the streak/counters still move (the answer
    happened — it just costs nothing).
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=812,
        level=5,
        current_streak=6,
        best_streak=6,
        correct_count=6,
        incorrect_count=0,
    )

    response = await client.post(
        ANSWER_URL,
        json={"mode": "bin_to_dec", "bit_width": 8, "is_correct": False},
        headers=_headers(user_id),
    )

    payload = response.json()
    assert response.status_code == 200, response.text
    assert payload["xp_delta"] == 0
    assert payload["total_xp"] == 812
    assert payload["level"] == 5  # LV-08: demotion impossible
    assert payload["xp_in_level"] == 0
    assert payload["xp_required_for_next_level"] == 508  # cum(6) - cum(5) = 1320 - 812
    assert (payload["current_streak"], payload["incorrect_count"]) == (0, 1)
    assert payload["newly_unlocked"] == []


@pytest.mark.parametrize(
    ("mode", "bit_width"),
    [("hex_to_bin", 16), ("hex_to_dec", 8), ("bin_to_dec", 4), ("unicode", 24)],
)
async def test_n25_every_applied_delta_is_a_multiple_of_four(
    client, db, async_engine, mode, bit_width
):
    """N25 / D-8: totals and deltas stay in 4z through a correct/incorrect pair.

    One combo per mode, answered right (award) and wrong (penalty, possibly
    truncated by the floor) from a level-13 seed, so the unlock gate is open for
    all of them. A float anywhere in US-33's seam would show up here as a
    non-integer or a non-multiple-of-4 value.
    """
    user_id = await _seed_user(db)
    seed = cumulative_xp_for_level(13) + 4
    await _seed_profile(
        db,
        user_id,
        total_xp=seed,
        level=13,
        current_streak=1,
        best_streak=1,
        correct_count=1,
        incorrect_count=1,
    )

    running = seed
    for is_correct in (True, False):
        response = await client.post(
            ANSWER_URL,
            json={"mode": mode, "bit_width": bit_width, "is_correct": is_correct},
            headers=_headers(user_id),
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload["xp_delta"], int)
        assert not isinstance(payload["xp_delta"], bool)
        assert payload["xp_delta"] % 4 == 0, payload
        assert payload["total_xp"] % 4 == 0, payload
        assert payload["total_xp"] == running + payload["xp_delta"]
        running = payload["total_xp"]
        if is_correct:
            assert payload["xp_delta"] > 0
        else:
            assert payload["xp_delta"] <= 0  # the floor can absorb it (N24)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_correct", 1),
        ("is_correct", 0),
        ("is_correct", "true"),
        ("is_correct", 1.0),
        ("is_correct", None),
        ("bit_width", 8.0),
        ("bit_width", True),
        ("bit_width", "8"),
    ],
)
async def test_n26_strict_typing_is_enforced_on_the_wire(
    client, db, async_engine, field, value
):
    """N26: no coercion of ``is_correct`` or ``bit_width`` on the XP write path.

    ``True == 1`` and ``1.0 == 1`` in Python, so a coercing schema would silently
    score a malformed request; ``AnswerRequest`` pins both fields ``strict=True``
    (US-26), and the row must not move on any of these.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=2,
        best_streak=2,
        correct_count=2,
        incorrect_count=0,
    )
    before = await _row(db, user_id)
    body = {"mode": "hex_to_bin", "bit_width": 8, "is_correct": True}
    body[field] = value

    response = await client.post(ANSWER_URL, json=body, headers=_headers(user_id))

    assert response.status_code == 422, (field, value, response.text)
    await _assert_row_untouched(db, user_id, before)


async def test_n27_the_level_never_decreases_across_a_penalty_run(
    client, db, async_engine
):
    """N27 / LV-08: ten wrong answers in a row cannot demote anybody.

    Started 1000 XP above ``cum(13)`` = 25752 with the heaviest penalty the
    registry allows (bin_to_dec 32 = core 1024 / 2 = 512), so the ten answers walk
    all three LV-09 regimes in one sequence — a full penalty while the margin lasts,
    a penalty truncated to the remaining margin, then pure floor absorption — and
    the reported level is 13 throughout. The clamp, not a sticky column, is what
    makes that true (D-1/D-5).
    """
    user_id = await _seed_user(db)
    seed = cumulative_xp_for_level(13) + 1_000
    await _seed_profile(
        db,
        user_id,
        total_xp=seed,
        level=13,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )

    levels, deltas = [], []
    for _ in range(10):
        response = await client.post(
            ANSWER_URL,
            json={"mode": "bin_to_dec", "bit_width": 32, "is_correct": False},
            headers=_headers(user_id),
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        levels.append(payload["level"])
        deltas.append(payload["xp_delta"])

    floor = cumulative_xp_for_level(13)
    assert levels == [13] * 10
    assert deltas[:2] == [-512, -(1_000 - 512)]  # full, then truncated to the margin
    assert set(deltas[2:]) == {0}  # at the floor from here on (LV-09b)
    assert all(delta % 4 == 0 for delta in deltas)  # D-8 survives the clamp
    row = await _row(db, user_id)
    assert (row.total_xp, row.level, row.incorrect_count) == (floor, 13, 10)


# --------------------------------------------------------------------------- #
# E. Rev 3 R-2 / US-32 AC-2..AC-4: the limiter is a separate session, ahead of
#    the answer transaction, and pays for no cleanup on the request path
# --------------------------------------------------------------------------- #


async def test_the_limiter_runs_on_its_own_session_ahead_of_the_transaction(
    async_engine, db
):
    """R-2 Option A, proved structurally: two sessions, limiter first.

    Three claims in one run, because the interesting part is their RELATIONSHIP:
    the limiter and the answer transaction do not share a session (so the
    limiter's ``COMMIT`` cannot release the ``FOR UPDATE`` lock — US-28 AC-2), and
    the limiter's ``INSERT`` precedes the first ``FOR UPDATE`` (so a 429 is
    decided before the row is ever touched).
    """
    from src.main import app

    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    opened: dict[str, list[AsyncSession]] = {"answer": [], "limiter": []}

    def _recording_override(bucket: str):
        async def _override():
            async with factory() as session:
                opened[bucket].append(session)
                yield session

        return _override

    app.dependency_overrides[get_db] = _recording_override("answer")
    app.dependency_overrides[answer_rate_limit_session] = _recording_override("limiter")
    user_id = await _seed_user(db)
    try:
        with _statement_log(async_engine) as log:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    ANSWER_URL,
                    json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": True},
                    headers=_headers(user_id),
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert len(opened["answer"]) == 1
    assert len(opened["limiter"]) == 1
    assert opened["answer"][0] is not opened["limiter"][0]

    def _index(predicate) -> int:
        # A list comprehension rather than next(...): an empty match would raise
        # StopIteration inside this coroutine, which asyncio turns into an opaque
        # "coroutine raised StopIteration" instead of showing the statement log.
        matches = [i for i, entry in enumerate(log) if predicate(entry)]
        assert matches, f"no statement matched the predicate: {log}"
        return matches[0]

    limiter_insert = _index(
        lambda entry: entry.upper().startswith("INSERT INTO RATE_LIMIT_ATTEMPT")
    )
    row_lock = _index(lambda entry: "FOR UPDATE" in entry.upper())
    assert limiter_insert < row_lock, log
    # One write statement for one answer: the single COMMIT of US-28 AC-2 has
    # exactly one UPDATE to carry, and it is the profile row's.
    updates = [entry for entry in log if entry.upper().startswith("UPDATE")]
    assert len(updates) == 1, log
    assert "user_game_profile" in updates[0]


async def test_429_is_decided_before_the_row_lock_is_taken(client, db, async_engine):
    """US-32 AC-3: a throttled answer touches the rate-limit table and nothing else.

    ``FOR UPDATE`` on the profile would mean a 429 that still costs a lock holder
    a wait, and an ``INSERT``/``UPDATE`` on the profile would mean a rejected
    answer that scored — neither may exist on this path.
    """
    user_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=476,
        level=4,
        current_streak=2,
        best_streak=2,
        correct_count=2,
        incorrect_count=0,
    )
    await _fill_answer_rate_limit(async_engine, user_id)

    with _statement_log(async_engine) as log:
        response = await client.post(
            ANSWER_URL,
            json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": True},
            headers=_headers(user_id),
        )

    assert response.status_code == 429
    assert not [entry for entry in log if "FOR UPDATE" in entry.upper()], log
    assert not _mentions(log, "user_game_profile"), log
    assert not _mentions(log, "users"), log
    assert _mentions(log, "rate_limit_attempt"), log


async def test_no_delete_runs_on_the_answer_path(client, db, async_engine):
    """US-32 AC-4: the request path counts and records, never purges.

    The per-call ``DELETE`` of expired rows was the whole point of moving cleanup
    off the hot endpoint (``check_without_cleanup``); a future refactor that
    reintroduces it would put a delete scan back on the project's hottest path, so
    the claim is asserted on the wire rather than only in the service unit tests.
    """
    user_id = await _seed_user(db)

    with _statement_log(async_engine) as log:
        for is_correct in (True, False):
            response = await client.post(
                ANSWER_URL,
                json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": is_correct},
                headers=_headers(user_id),
            )
            assert response.status_code == 200, response.text

    assert not [
        entry for entry in log if entry.upper().lstrip().startswith("DELETE")
    ], log


async def test_the_budget_is_the_registry_constant_per_user(client, db, async_engine):
    """US-32 AC-2: 120 answers per 60 s (``game_config``), then 429 — per user.

    Driven to 119 on the service layer, so the endpoint only has to answer the
    boundary itself: the 120th allowed, the 121st refused. A second user proves
    the key is scoped to the identity from the bearer, not to the endpoint.
    """
    assert (ANSWER_RATE_LIMIT, ANSWER_RATE_WINDOW_SECONDS) == (120, 60)
    user_id = await _seed_user(db)
    other_id = await _seed_user(db)
    await _seed_profile(
        db,
        user_id,
        total_xp=0,
        level=1,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )
    await _fill_answer_rate_limit(async_engine, user_id, count=ANSWER_RATE_LIMIT - 1)
    body = {"mode": "hex_to_bin", "bit_width": 4, "is_correct": True}

    last_allowed = await client.post(ANSWER_URL, json=body, headers=_headers(user_id))
    one_too_many = await client.post(ANSWER_URL, json=body, headers=_headers(user_id))
    neighbour = await client.post(ANSWER_URL, json=body, headers=_headers(other_id))

    assert last_allowed.status_code == 200, last_allowed.text
    assert last_allowed.json()["correct_count"] == 1
    assert one_too_many.status_code == 429
    assert one_too_many.headers["retry-after"] == "60"
    assert neighbour.status_code == 200, neighbour.text
    # The refused answer scored nothing: one allowed answer, one row, one XP step.
    row = await _row(db, user_id)
    assert row[:6] == (16, 1, 1, 1, 1, 0)


# --------------------------------------------------------------------------- #
# F. US-32 AC-5: the 4 KB body cap is enforced BEFORE the body is parsed
# --------------------------------------------------------------------------- #


def _padded_body(*, size: int) -> bytes:
    """A valid answer body padded with insignificant whitespace to ``size`` bytes.

    Padding (rather than a long field value) keeps the example a *legal* request,
    so the 200 side of the boundary proves the cap is inclusive and not merely a
    rejection of big JSON.
    """
    body = json.dumps(
        {"mode": "hex_to_bin", "bit_width": 4, "is_correct": True}
    ).encode()
    if len(body) > size:
        raise ValueError(f"smallest legal body is {len(body)} bytes > {size}")
    return body + b" " * (size - len(body))


async def test_body_over_the_cap_is_413_before_any_parsing(client, db, async_engine):
    """AC-5: > 4 KB is refused with 413, and nothing behind it runs.

    The three no-side-effect claims: no rate-limit row is spent (a caller who
    cannot afford the body must not pay for it either), no profile row is created
    or moved, and the profile table is never queried — the refusal happens in the
    ASGI layer, before FastAPI reads the body, before auth, before everything.
    """
    user_id = await _seed_user(db)
    oversized = (
        b'{"mode": "hex_to_bin", "bit_width": 4, "is_correct": true'
        + b" " * 5000
        + b"}"
    )
    assert len(oversized) > ANSWER_BODY_LIMIT_BYTES

    with _statement_log(async_engine) as log:
        response = await client.post(
            ANSWER_URL,
            content=oversized,
            headers={**_headers(user_id), "content-type": "application/json"},
        )

    assert response.status_code == 413, response.text
    assert "4096" in response.json()["detail"]
    assert not _mentions(log, "rate_limit_attempt"), log
    assert not _mentions(log, "user_game_profile"), log
    assert await _row(db, user_id) is None


async def test_body_exactly_at_the_cap_is_accepted(client, db, async_engine):
    """The cap is a ceiling, not a target: exactly 4096 bytes still scores."""
    user_id = await _seed_user(db)

    response = await client.post(
        ANSWER_URL,
        content=_padded_body(size=ANSWER_BODY_LIMIT_BYTES),
        headers={**_headers(user_id), "content-type": "application/json"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["xp_delta"] == 16


async def test_body_one_byte_over_the_cap_is_refused(client, db, async_engine):
    """4097 bytes is a 413 — the boundary is the limit itself, not limit+2."""
    user_id = await _seed_user(db)

    response = await client.post(
        ANSWER_URL,
        content=_padded_body(size=ANSWER_BODY_LIMIT_BYTES + 1),
        headers={**_headers(user_id), "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert await _row(db, user_id) is None


async def test_n5_huge_integer_literal_cannot_hang_the_server(client, db, async_engine):
    """N5: a 5000-digit ``bit_width`` is refused, cheaply and in bounded time.

    Two defences meet here. The 4 KB cap fires first (a 5000-digit literal cannot
    fit in 4096 bytes), so the integer never reaches a parser at all — which is why
    the status is 413 rather than the 400/422 a digit-limit refusal used to give:
    the cap SUBSUMES the CPython 4300-digit conversion limit instead of racing it.
    What this test owns is the invariant underneath both: no 5xx, no hang, and no
    write, bounded by an explicit deadline so a regression that blocks the loop
    fails loudly instead of timing the suite out.
    """
    user_id = await _seed_user(db)
    huge = b"9" * 5000
    body = b'{"mode": "hex_to_bin", "bit_width": ' + huge + b', "is_correct": true}'

    response = await asyncio.wait_for(
        client.post(
            ANSWER_URL,
            content=body,
            headers={**_headers(user_id), "content-type": "application/json"},
        ),
        timeout=5,
    )

    # The exact status, not a set of tolerable ones (Wave 2a review F4): the cap
    # SUBSUMES the digit limit, so 413 is the answer, and an "in (400, 413, 422)"
    # would still pass if the middleware were removed and CPython's own limit
    # produced the 400 again. No 5xx is implied by a concrete code.
    assert response.status_code == 413, response.text
    assert await _row(db, user_id) is None


async def test_a_streamed_body_without_content_length_is_still_capped(
    client, db, async_engine
):
    """The cap counts bytes, not only the ``Content-Length`` header.

    Chunked transfer encoding declares no length at all, so a request that streams
    6 KB in 1 KB pieces is exactly the shape a header-only check would wave through.
    The middleware's running total catches it on the chunk that crosses 4096.
    """
    user_id = await _seed_user(db)

    async def _chunks():
        for _ in range(6):
            yield b'{"a": "' + b"x" * 990 + b'"}'

    response = await client.post(
        ANSWER_URL,
        content=_chunks(),
        headers={**_headers(user_id), "content-type": "application/json"},
    )

    assert response.status_code == 413, response.text
    assert await _row(db, user_id) is None


# --------------------------------------------------------------------------- #
# F2. The branches no HTTP client can reach: the gate's caller contract, and the
#     two tolerances of the body cap. Raw ASGI, no database, no client.
# --------------------------------------------------------------------------- #


def test_the_gate_refuses_a_combo_outside_the_seed() -> None:
    """A combo with no D-2 seed row is a violated gate ORDER, not a client 403.

    ``("hex_to_bin", 24)`` is 422'd by US-32's whitelist long before the gate runs,
    and :func:`~src.services.unlock_service.is_unlocked` is total on the combo axis
    (it answers ``False``), so reaching this branch means the router reordered its
    gates. Rendering that as "Content locked until level N" would publish a level
    that does not exist in the ladder — a lie about the economy on the error path —
    so it is a ``RuntimeError`` instead.
    """
    from src.services.answer_service import unlock_gate_detail

    with pytest.raises(RuntimeError, match="unvalidated combo"):
        unlock_gate_detail("hex_to_bin", 24, 1)


async def _run_cap(
    *, headers: list[tuple[bytes, bytes]], messages: list[dict]
) -> tuple[int | None, list[dict]]:
    """Drive the body cap by hand; return (refusal status, messages the app saw).

    A real HTTP client cannot express what these two branches key on — an
    unparsable ``Content-Length`` and a mid-body ``http.disconnect`` — so the ASGI
    exchange is written out here. ``None`` status means the middleware handed the
    request on, which is the whole point of both branches.
    """
    from src.middleware.body_size import AnswerBodySizeLimitMiddleware

    seen: list[dict] = []
    sent: list[dict] = []
    queue = list(messages)

    async def app(scope, receive, send):  # a stub, not a real ASGIApp
        seen.append(await receive())

    async def send(message):
        sent.append(message)

    async def receive():
        return queue.pop(0)

    middleware = AnswerBodySizeLimitMiddleware(
        app, path=ANSWER_BODY_SIZE_PATH, limit=ANSWER_BODY_LIMIT_BYTES
    )
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": ANSWER_BODY_SIZE_PATH,
            "headers": headers,
        },
        receive,
        send,
    )
    status = next(
        (
            message["status"]
            for message in sent
            if message["type"] == "http.response.start"
        ),
        None,
    )
    return status, seen


@pytest.mark.parametrize("declared", [b"abc", b"-5", b""])
async def test_an_unusable_content_length_header_is_tolerated(
    declared: bytes,
) -> None:
    """A header that cannot be believed is treated as nothing declared.

    The alternative — a refusal of its own for an unreadable header — would invent a
    status code on the way to nowhere and give a client a second, easier DoS surface
    than the body itself. The byte count still enforces the ceiling, which the next
    test proves on the same header.
    """
    body = b'{"mode": "hex_to_bin", "bit_width": 4, "is_correct": true}'

    status, seen = await _run_cap(
        headers=[(b"content-length", declared)],
        messages=[{"type": "http.request", "body": body, "more_body": False}],
    )

    assert status is None, seen
    # The replay is one complete message: nothing downstream can tell a cap ran.
    assert seen == [{"type": "http.request", "body": body, "more_body": False}]


async def test_a_lying_content_length_is_still_capped_on_the_byte_count() -> None:
    """Declared 2, streamed 5 KB: the running total, not the header, decides."""
    status, seen = await _run_cap(
        headers=[(b"content-length", b"2")],
        messages=[
            {"type": "http.request", "body": b"x" * 3000, "more_body": True},
            {"type": "http.request", "body": b"x" * 3000, "more_body": False},
        ],
    )

    assert status == 413, seen
    assert seen == []  # the app never ran, so no parse and no DB checkout


async def test_a_client_that_disconnects_mid_body_is_not_answered() -> None:
    """No response to a gone client: writing one would raise on a closed transport."""
    status, seen = await _run_cap(
        headers=[],
        messages=[
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.disconnect"},
        ],
    )

    assert status is None, seen
    assert seen == []


@pytest.mark.parametrize(
    ("message", "expected_body"),
    [
        pytest.param(
            {"type": "http.request", "body": b'{"a": 1}', "more_body": False},
            b'{"a": 1}',
            id="both-keys-present",
        ),
        pytest.param(
            {"type": "http.request", "body": b'{"a": 1}'},
            b'{"a": 1}',
            id="more_body-absent",
        ),
        pytest.param(
            {"type": "http.request"},
            b"",
            id="both-keys-absent",
        ),
    ],
)
async def test_the_optional_asgi_message_keys_may_be_absent(
    message: dict, expected_body: bytes
) -> None:
    """F3 (Wave 2a review): ``body`` and ``more_body`` are OPTIONAL, so read them so.

    The ASGI HTTP specification marks both default to ``b""`` / ``False``. Reading
    ``more_body`` with ``message["more_body"]`` worked against uvicorn, which always
    sends it, and would have raised ``KeyError`` inside the buffering loop against a
    server that does not — and a raise out of the middleware's ``receive`` is exactly
    the 400 "error parsing the body" this loop exists to avoid (the same reason it
    buffers instead of raising on oversize). A conformant-but-minimal server therefore
    has to reach the app with the body it declared, which is what the replay asserts.
    """
    status, seen = await _run_cap(headers=[], messages=[message])

    assert status is None, seen  # a well-formed small body is never refused
    assert seen == [
        {"type": "http.request", "body": expected_body, "more_body": False}
    ], seen


# --------------------------------------------------------------------------- #
# G. The inventory: one new route, nothing else disturbed
# --------------------------------------------------------------------------- #


GAME_ROUTE_INVENTORY: frozenset[tuple[str, tuple[str, ...]]] = frozenset(
    {
        ("/api/game/answer", ("POST",)),
        ("/api/game/profile", ("GET",)),
        ("/api/game/leaderboard", ("GET",)),
    }
)


def test_the_economy_trio_is_the_whole_game_router():
    """US-33/US-34: /answer is mounted, and nothing else answers on /api/game.

    US-33 added the write path beside the two readers US-35/US-36 shipped; US-34 took
    the legacy writers out. The inventory is therefore an EQUALITY, not a subset
    claim — a fourth route here would be a legacy resurrection or an unaccounted
    endpoint, and both fail the same grep.

    The path literal is asserted against the middleware's own constant too: a drift
    between the two would leave the endpoint silently uncapped, and both are module
    constants for exactly this reason.
    """
    from src.main import app
    from src.routers.game import ANSWER_PATH
    from src.routers.game import router as game_router

    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/game")
    }
    assert paths == GAME_ROUTE_INVENTORY, paths
    # The drift guard: the router's own path plus its prefix IS the middleware's
    # protected path, so the cap cannot be pointed at a route that no longer exists.
    assert f"{game_router.prefix}{ANSWER_PATH}" == ANSWER_BODY_SIZE_PATH
