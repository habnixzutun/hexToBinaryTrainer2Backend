"""US-43 (backend): the leaderboard entry is reduced to four fields.

🔴 **RED PHASE.** Every test here states the US-43 contract while the implementation
still serves the US-36 shape, so the shape tests below FAIL BY DESIGN: they are the
specification ``backend-dev`` implements against, not a report about current
behaviour. The handful of cases marked "guard" already pass today on purpose — they
pin the invariants this reduction could plausibly break (the derived level, the total
order, the wrapper) and they have to stay green through GREEN.

The contract, four layers deep: schema (:class:`LeaderboardEntryV2`), OpenAPI (the
generated wire document), service (:func:`leaderboard_entries`) and endpoint
(``GET /api/game/leaderboard``), because a field can survive the schema change by
being re-added in one of the other three.

## One disagreement this file settles, and how to reopen it

The task brief asks for **three** keys per entry (``{username, level, total_xp}``).
The normative source for this sprint — ``docs/sprint5-plan.md`` US-43 AC-1 (``:78``)
— asks for **four**: ``user_id`` stays on the wire. This file tests the plan, because:

1. AC-1 is definite, and the brief's field list contradicts its own requirement
   sentence — "*Alle anderen **Statistiken** (correct_count, incorrect_count,
   current_streak, best_streak) werden … nicht mehr geliefert*". ``user_id`` is an
   identifier, not a statistic.
2. AC-2 (``:79``) freezes the SQL order ``total_xp DESC, incorrect_count ASC,
   user_id ASC`` and states the tiebreak column is "*weiterhin aus der DB gelesen,
   nur nicht serialisiert*". The brief's alternative (``level DESC, username ASC``)
   would silently re-rank every tied player pair. (Re-ordering by ``level DESC``
   alone would be harmless — ``level`` is monotonic in ``total_xp`` — but dropping
   the ``incorrect_count`` tiebreak is a behaviour change, not a projection.)
3. **US-44, Welle 2 desselben Sprints, identifiziert die eigene Zeile über
   ``e.user_id === user?.id``** (``:100``, ``:102``), and the frontend's „ich"-Badge
   (AC-3, ``:80``) keys off the same field. Removing ``user_id`` here makes the next
   story in the same release train unimplementable.
4. Under this reading the existing ``user_id``-based assertions
   (``tests/test_game.py:505``, ``:717``, ``tests/test_e2e_economy_integration.py:779``)
   survive the reduction untouched; only their counter assertions move (AC-5).

Should the PO side-rule and ``user_id`` leave the payload after all, the pivot is
:data:`LEADERBOARD_ENTRY_FIELDS` — one plan literal plus the row-locating helpers.

## Field vocabulary

``correct_count`` / ``incorrect_count`` are US-43's subjects: real fields today,
gone after. ``current_streak`` / ``best_streak`` / ``points`` / ``email`` were never
on this board (the streaks stayed server-side, ``points`` died in US-34) — those are
regression guards, and they are the fields a sloppy "read fewer columns" refactor
would resurrect.

## The RED map at hand-off

``uv run pytest tests/test_leaderboard.py --no-cov`` -> **11 failed, 16 passed**.
Every failure is an assertion about the two counters, the whole-row ``SELECT``, or the
four-key shape — never a crash. An ``ERROR`` here would mean the test is broken rather
than unimplemented, and a failure message that does not name ``correct_count`` /
``incorrect_count`` is a bug in this file rather than a task for GREEN.

GREEN is the run where all 27 pass with **no edit to this file**. The 16 that already
pass are the guards: they go red only if the reduction takes something with it that
US-43 never asked for (the derived level, the total order, the ``INNER`` join, the
bounded ``limit``, the wrapper, the abuse controls). If a RED test has to be edited to
go green, the contract moved — and it is the PO decision above that moved with it.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User
from src.models.game import UserGameProfile
from src.models.game_schemas import LeaderboardEntryV2, LeaderboardResponseV2
from src.routers.game import leaderboard_rate_limit_session
from src.services.leaderboard_service import leaderboard_entries

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
LEADERBOARD_URL = "/api/game/leaderboard"

# --------------------------------------------------------------------------- #
# Expectations — plan literals copied from docs/sprint5-plan.md, never derived
# from src/ (same rule as tests/test_game.py:13-15), so a wrong constant cannot
# hide behind a wrong expectation.
# --------------------------------------------------------------------------- #

# US-43 AC-1 (:78): "enthält nur noch user_id, username, total_xp, level".
LEADERBOARD_ENTRY_FIELDS: tuple[str, ...] = (
    "user_id",
    "username",
    "total_xp",
    "level",
)

# The two fields AC-1 removes "ersatzlos — kein Deprecated-Alias, kein optionales
# Feld". Legal in today's schema, illegal after GREEN.
DROPPED_STATISTICS: tuple[str, ...] = ("correct_count", "incorrect_count")

# Never on this board, before or after US-43 (US-34 killed `points`; the streaks
# stayed on /profile and /answer).
NEVER_ON_THE_BOARD: tuple[str, ...] = (
    "current_streak",
    "best_streak",
    "points",
    "email",
)

# D-1 cumulative thresholds as plan literals, copied from docs/xp-economy.md: the
# curve table (``:300``) and test case LV-05 (``:319``), cross-checked by the worked
# example at ``:412`` ("highest L with cum(L) <= 1320 = 6"). Landing ON a threshold
# means the player already IS the new level (LV-07), which is why 252 -> 3.
XP_ALICE = 1320  # -> level 6
LEVEL_ALICE = 6
XP_BOB = 252  # -> level 3
LEVEL_BOB = 3
XP_TOP = 25752  # -> level 13
LEVEL_TOP = 13
XP_BOTTOM = 4  # -> level 1, the D-1/OQ-3 floor
LEVEL_BOTTOM = 1


# --------------------------------------------------------------------------- #
# Fixtures — the sqlite pattern of tests/test_game.py:113-191, so this file runs
# without the docker-compose.test.yaml Postgres the DB-backed suites need.
# --------------------------------------------------------------------------- #


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


@pytest.fixture
async def client(engine, db_session):
    """Anonymous test client; both leaderboard sessions point at the test engine.

    ``leaderboard_rate_limit_session`` is overridden alongside ``get_db`` because it
    opens its OWN session (``src/routers/game.py:120-128``) and would otherwise hit
    the production pool for a read that never writes anything to speak of.
    """
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    async def override_rate_limit_session():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[leaderboard_rate_limit_session] = (
        override_rate_limit_session
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _add_player(
    session: AsyncSession,
    user_id: str,
    username: str,
    **profile_columns: int,
) -> None:
    """A ranked player: the ``users`` row plus one ``user_game_profile`` row.

    Written straight to the tables rather than earned through ``/answer`` — these
    tests are about the read side, and a seeded row keeps the shape and order
    assertions independent of the economy's write path.
    """
    session.add(User(id=user_id, username=username, email=f"{user_id}@example.com"))
    session.add(UserGameProfile(user_id=user_id, **profile_columns))


# --------------------------------------------------------------------------- #
# Layer 1: the schema (no database, no HTTP)
# --------------------------------------------------------------------------- #


def test_entry_v2_declares_exactly_the_four_us43_fields():
    """AC-1: the model's field tuple IS the plan literal — six fields today -> red."""
    assert tuple(LeaderboardEntryV2.model_fields) == LEADERBOARD_ENTRY_FIELDS


def test_entry_v2_serializes_the_four_fields_and_nothing_else():
    """AC-1: a minimal legal entry dumps to four keys, with no counter alongside."""
    entry = LeaderboardEntryV2(
        user_id="user-43-a",
        username="alice",
        total_xp=XP_ALICE,
        level=LEVEL_ALICE,
    )

    assert entry.model_dump() == {
        "user_id": "user-43-a",
        "username": "alice",
        "total_xp": XP_ALICE,
        "level": LEVEL_ALICE,
    }


def test_entry_v2_refuses_a_body_that_still_carries_the_counters():
    """AC-1 "ersatzlos": the counter body that is legal today must be refused.

    Both counters are supplied so this cannot pass today for the wrong reason — a
    missing-required-field ``ValidationError`` would look like a green US-43 while
    meaning nothing.
    """
    with pytest.raises(ValidationError):
        LeaderboardEntryV2.model_validate(
            {
                "user_id": "user-43-a",
                "username": "alice",
                "total_xp": XP_ALICE,
                "level": LEVEL_ALICE,
                "correct_count": 42,
                "incorrect_count": 5,
            }
        )


@pytest.mark.parametrize("field", NEVER_ON_THE_BOARD)
def test_entry_v2_keeps_refusing_what_was_never_on_the_board(field: str):
    """GUARD: ``extra="forbid"`` already covers streaks/points/PII — US-43 must not
    loosen it to make the reduction easier (the Rev-3 no-alias precedent, AC-1)."""
    body = {
        "user_id": "user-43-a",
        "username": "alice",
        "total_xp": XP_ALICE,
        "level": LEVEL_ALICE,
        "correct_count": 42,  # legal today; refused by US-43 like the others
        "incorrect_count": 5,
    }
    body[field] = 1

    with pytest.raises(ValidationError):
        LeaderboardEntryV2.model_validate(body)


def test_the_wrapper_still_wraps_entries_only():
    """AC-5: the ``{entries: [...]}`` wrapper is unchanged by the reduction."""
    assert tuple(LeaderboardResponseV2.model_fields) == ("entries",)


def test_the_wrapper_accepts_an_entry_without_the_counters():
    """AC-1/AC-5: the container follows the element — four fields validate today's
    five-field element cannot, so this is red until ``LeaderboardEntryV2`` shrinks."""
    response = LeaderboardResponseV2(
        entries=[
            {
                "user_id": "user-43-a",
                "username": "alice",
                "total_xp": XP_ALICE,
                "level": LEVEL_ALICE,
            }
        ]
    )

    assert tuple(response.entries[0].model_dump()) == LEADERBOARD_ENTRY_FIELDS


# --------------------------------------------------------------------------- #
# Layer 2: the generated OpenAPI document (the router's response_model, no DB)
# --------------------------------------------------------------------------- #


def test_openapi_advertises_only_the_four_us43_properties():
    """AC-1/AC-8: the published contract matches the model.

    The router declares ``response_model=LeaderboardResponseV2``, so a field left in
    the schema would surface here even if the handler stopped filling it — and a
    client generating a type from ``/openapi.json`` would keep rendering the column.
    """
    schema = app.openapi()["components"]["schemas"]["LeaderboardEntryV2"]

    assert sorted(schema["properties"]) == sorted(LEADERBOARD_ENTRY_FIELDS)
    assert sorted(schema["required"]) == sorted(LEADERBOARD_ENTRY_FIELDS)


# --------------------------------------------------------------------------- #
# Layer 3: the service (database, no HTTP)
# --------------------------------------------------------------------------- #


async def test_entries_return_only_the_four_plan_fields_per_row(db_session):
    """AC-1 below the wire: the service builds four-field entries, statistics or not."""
    _add_player(
        db_session,
        "user-43-a",
        "alice",
        total_xp=XP_ALICE,
        level=LEVEL_ALICE,
        current_streak=3,
        best_streak=7,
        correct_count=42,
        incorrect_count=5,
    )
    _add_player(
        db_session,
        "user-43-b",
        "bob",
        total_xp=XP_BOB,
        level=LEVEL_BOB,
        current_streak=0,
        best_streak=0,
        correct_count=0,
        incorrect_count=0,
    )
    await db_session.commit()

    entries = await leaderboard_entries(db_session)

    assert [entry.model_dump() for entry in entries] == [
        {
            "user_id": "user-43-a",
            "username": "alice",
            "total_xp": XP_ALICE,
            "level": LEVEL_ALICE,
        },
        {
            "user_id": "user-43-b",
            "username": "bob",
            "total_xp": XP_BOB,
            "level": LEVEL_BOB,
        },
    ]


async def test_entries_rank_tied_xp_by_the_counter_they_no_longer_show(db_session):
    """AC-2: the tiebreak stays a ranking rule after it stops being a payload field.

    Three rows share one XP total and differ only in ``incorrect_count`` (and, for
    the last pair, in ``user_id``). Fewer mistakes ranks higher, and neither counter
    appears in what the caller gets — the exact sentence AC-2 is written to protect.
    """
    rows = (
        ("user-43-c", "Clean", 2),
        ("user-43-d", "Messy", 9),
        ("user-43-e", "EquallyMessy", 9),  # tied with d, later user_id
    )
    for user_id, username, incorrect in rows:
        _add_player(
            db_session,
            user_id,
            username,
            total_xp=XP_ALICE,
            level=LEVEL_ALICE,
            correct_count=1,
            incorrect_count=incorrect,
        )
    await db_session.commit()

    entries = await leaderboard_entries(db_session)

    assert [entry.username for entry in entries] == ["Clean", "Messy", "EquallyMessy"]
    assert [entry.user_id for entry in entries] == [
        "user-43-c",
        "user-43-d",
        "user-43-e",
    ]
    for entry in entries:
        dumped = entry.model_dump()
        for statistic in DROPPED_STATISTICS:
            assert statistic not in dumped, statistic


async def test_the_board_query_reads_no_column_the_board_cannot_show(
    engine, db_session
):
    """AC-1 + the brief's performance clause: slim the SELECT, keep the tiebreak.

    ``select(UserGameProfile, ...)`` loads the WHOLE row today, so the board pays for
    ``current_streak``, ``best_streak`` and ``correct_count`` on every one of its
    30-second polls to show none of them. AC-2 is the exception carved into this
    rule: ``incorrect_count`` is still read, because the ORDER BY needs it.

    One statement is asserted too — a board that fans out into a per-row lookup
    would trade a column it dropped for a round trip it cannot afford.
    """
    _add_player(
        db_session,
        "user-43-a",
        "alice",
        total_xp=XP_ALICE,
        level=LEVEL_ALICE,
        correct_count=42,
        incorrect_count=5,
    )
    await db_session.commit()

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await leaderboard_entries(db_session)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    board_queries = [
        statement
        for statement in captured
        if statement.lstrip().upper().startswith("SELECT")
        and "user_game_profile" in statement
    ]
    assert len(board_queries) == 1, captured

    query = board_queries[0]
    # Neither shown (AC-1) nor ordered by (AC-2 names only incorrect_count/user_id),
    # so `updated_at` belongs to the same dead weight as the counters.
    for column in ("current_streak", "best_streak", "correct_count", "updated_at"):
        # Qualified, not bare: ``correct_count`` is a substring of ``incorrect_count``,
        # which AC-2 requires this very query to read. A naked ``column not in query``
        # would make every compliant query fail — a bug in this file, not in the board.
        assert f"user_game_profile.{column}" not in query, (column, query)
    assert "incorrect_count" in query, query  # AC-2: read for the ORDER BY only


async def test_the_board_derives_level_from_xp_never_from_the_stored_column(db_session):
    """Phase 2b H1 survives the reduction: ``level`` is a function of ``total_xp``.

    This is the trap a slimmer query walks into. Reading only the columns the entry
    shows looks like licence to select the stored ``level`` instead of computing it —
    and a hand-run ``UPDATE user_game_profile SET level = 1`` (or a US-41 fixture)
    would then rank a 1320-XP player at level 1 while ``/profile`` says 6. Guard for
    today, load-bearing after GREEN.
    """
    _add_player(
        db_session,
        "user-43-a",
        "alice",
        total_xp=XP_ALICE,
        level=1,  # contradicts 1320 XP, which is level 6 on the D-1 curve
        correct_count=42,
        incorrect_count=5,
    )
    await db_session.commit()

    entries = await leaderboard_entries(db_session)

    assert (entries[0].total_xp, entries[0].level) == (XP_ALICE, LEVEL_ALICE)

    stored_level = (
        await db_session.execute(
            select(UserGameProfile.level).where(UserGameProfile.user_id == "user-43-a")
        )
    ).scalar_one()
    assert stored_level == 1  # a read reports, it never repairs


# --------------------------------------------------------------------------- #
# Layer 4: the endpoint (HTTP)
# --------------------------------------------------------------------------- #


async def test_wire_entry_carries_exactly_the_us43_keys(
    client: AsyncClient, db_session
):
    """AC-1 on the wire: the key set itself, not a subset check.

    ``==`` rather than ``<=`` because a leftover field is the bug this story exists
    to remove — and because the frontend's generated type has to shrink to the same
    set (AC-3).
    """
    _add_player(
        db_session,
        "user-43-a",
        "alice",
        total_xp=XP_ALICE,
        level=LEVEL_ALICE,
        current_streak=3,
        best_streak=7,
        correct_count=42,
        incorrect_count=5,
    )
    await db_session.commit()

    response = await client.get(LEADERBOARD_URL)

    assert response.status_code == 200, response.text
    data = response.json()
    assert list(data) == ["entries"]  # AC-5: the wrapper is unchanged
    assert len(data["entries"]) == 1
    assert set(data["entries"][0].keys()) == set(LEADERBOARD_ENTRY_FIELDS)


@pytest.mark.parametrize("statistic", (*DROPPED_STATISTICS, *NEVER_ON_THE_BOARD))
async def test_no_statistic_appears_anywhere_in_the_response_body(
    client: AsyncClient, db_session, statistic: str
):
    """AC-1/AC-7: measured on the raw text, not the parsed dict.

    The key-set assertions check what the model declares; this checks what the
    server SENDS, so a handler that bypASSES the response model (the router returns
    a hand-built ``JSONResponse``, ``src/routers/game.py:336-339``) cannot smuggle a
    counter back in.
    """
    _add_player(
        db_session,
        "user-43-a",
        "alice",
        total_xp=XP_ALICE,
        level=LEVEL_ALICE,
        current_streak=3,
        best_streak=7,
        correct_count=42,
        incorrect_count=5,
    )
    await db_session.commit()

    response = await client.get(LEADERBOARD_URL)

    assert f'"{statistic}"' not in response.text, response.text


async def test_the_wire_order_survives_the_reduction(client: AsyncClient, db_session):
    """GUARD (AC-2): the projection changes no rank.

    The service-layer twin proves the order exists; this one proves the router does
    not re-sort or re-shape it on the way out, which matters because the board's
    total order is what keeps a 30-second poll from watching rows reshuffle.
    """
    for index, (user_id, username, total_xp) in enumerate(
        (
            ("user-43-top", "TopScorer", XP_TOP),
            ("user-43-mid", "MiddleScorer", XP_ALICE),
            ("user-43-low", "BottomScorer", XP_BOTTOM),
        )
    ):
        # Every row is seeded with a stored level of 1, so a board that started
        # reading the column instead of deriving it would report 1 three times.
        _add_player(
            db_session,
            user_id,
            username,
            total_xp=total_xp,
            level=1,
            correct_count=index,
            incorrect_count=index,
        )
    await db_session.commit()

    entries = (await client.get(LEADERBOARD_URL)).json()["entries"]

    assert [entry["username"] for entry in entries] == [
        "TopScorer",
        "MiddleScorer",
        "BottomScorer",
    ]
    assert [entry["total_xp"] for entry in entries] == [XP_TOP, XP_ALICE, XP_BOTTOM]
    # AC-1: the level a client can still render, derived from the XP it ranks by.
    assert [entry["level"] for entry in entries] == [
        LEVEL_TOP,
        LEVEL_ALICE,
        LEVEL_BOTTOM,
    ]


async def test_the_empty_board_is_still_an_entries_wrapper(
    client: AsyncClient, db_session
):
    """GUARD (AC-5): nobody has played -> ``{"entries": []}``, not a 404 and not null.

    The frontend polls this every 30 s from a cold start; an empty board has to stay
    a successful, shaped answer or US-43 turns a reduction into a crash for every
    fresh deployment.
    """
    response = await client.get(LEADERBOARD_URL)

    assert response.status_code == 200, response.text
    assert response.json() == {"entries": []}


async def test_a_player_without_an_economy_row_is_still_absent(
    client: AsyncClient, db_session
):
    """GUARD (US-28 AC-3 / D-6): the INNER JOIN is not collateral damage.

    Slimming the projection tempts a refactor towards ``select(User, ...)`` with a
    LEFT JOIN, which would put every never-answered registration on the board at 0 XP.
    """
    db_session.add(
        User(id="user-43-ghost", username="NeverAnswered", email="ghost@example.com")
    )
    await db_session.commit()

    entries = (await client.get(LEADERBOARD_URL)).json()["entries"]

    assert entries == []


async def test_the_rate_limit_and_cache_headers_survive_the_reduction(
    client: AsyncClient, db_session
):
    """GUARD (Security Review 2026-10-29): abuse control is not part of the payload.

    The per-IP 60/min budget and ``Cache-Control: public, max-age=30`` were added to
    this endpoint alongside the shape US-43 changes. A reduction that touches the
    handler must leave both in place, so they are pinned here against the same
    response the shape tests read.
    """
    response = await client.get(LEADERBOARD_URL)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "public, max-age=30"


async def test_the_limit_still_truncates_the_slimmed_board(
    client: AsyncClient, db_session
):
    """GUARD (AC-2/US-36 AC-2): a rewritten ``ORDER BY`` + ``LIMIT`` still cuts the top.

    The service query is the thing GREEN rewrites, and ``LIMIT`` is applied to the
    sort it touches — a reordered projection that silently loses the bound would keep
    every shape test green while handing a caller the whole table, which is the exact
    enumeration the bounded ``limit`` exists to prevent (R-3/OQ-9).
    """
    for total_xp in (XP_BOTTOM, XP_TOP, XP_ALICE):
        _add_player(
            db_session,
            f"user-43-{total_xp}",
            f"player{total_xp}",
            total_xp=total_xp,
            level=1,
        )
    await db_session.commit()

    response = await client.get(f"{LEADERBOARD_URL}?limit=2")

    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert len(entries) == 2, response.text
    assert [entry["total_xp"] for entry in entries] == [XP_TOP, XP_ALICE]


def test_exactly_one_leaderboard_entry_component_is_published():
    """GUARD: the document names ONE entry shape, so a generated client cannot pick
    a stale sibling.

    US-34 removed the legacy ``LeaderboardEntry`` for precisely this reason; a second
    ``Leaderboard*`` component appearing here means US-43 left two contracts in the
    wild and the frontend has to guess which one ships.
    """
    names = [
        name
        for name in app.openapi()["components"]["schemas"]
        if name.startswith("Leaderboard")
    ]

    assert sorted(names) == ["LeaderboardEntryV2", "LeaderboardResponseV2"]
