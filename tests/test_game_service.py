"""US-28: streak tracking service — lifecycle, seam contract, transaction rules.

Every test here runs against the real PostgreSQL test database (conftest's
``async_engine``), never the SQLite style of ``tests/test_game.py``: AC-1 and
AC-2 are *row-lock* guarantees, and ``SELECT ... FOR UPDATE`` is a silent no-op
on SQLite, so a green suite there would prove nothing.

Covered acceptance criteria (``docs/sprint4-plan.md`` US-28):

* AC-1 — ``SELECT ... FOR UPDATE`` on the profile row; concurrent submissions
  of the same user serialize (``test_concurrent_answers_serialize_...``,
  ``test_concurrent_first_answers_create_exactly_one_profile_row``).
* AC-2 — exactly one commit per answer, at the very end; nothing inside the
  transaction may commit or roll back (``test_exactly_one_commit_per_answer``,
  ``test_the_seam_runs_before_the_single_commit``, ``test_async_seam_is_rejected``).
* AC-3 — the first-ever answer creates the profile row lazily.
* AC-4 — reset on incorrect, best-streak preservation, lazy creation,
  serialization under concurrency.

The XP/level economy is NOT computed here (US-29/US-30) — it arrives through the
single narrow ``economy`` seam, which these tests drive with local fakes. The
real wiring (``xp_engine`` + ``level_service`` + ``unlock_service``) is US-33's
job; see ``test_seam_decision_is_persisted_and_reported`` for the contract it
must satisfy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.auth import User
from src.models.game import UserGameProfile, UserStats
from src.services.game_service import (
    AnswerOutcome,
    EconomyDecision,
    ProfileSnapshot,
    StreakContext,
    record_answer,
)
from uuid_utils import uuid7

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _seed_user(session: AsyncSession) -> str:
    """A unique ``users`` row (the shared test DB keeps rows across tests).

    The full uuid hex is used for username/email: uuid7's leading 48 bits are the
    millisecond timestamp, so consecutive ids inside one millisecond share a
    prefix and would collide on the UNIQUE columns.
    """
    user_id = str(uuid7())
    token = user_id.replace("-", "")
    session.add(
        User(
            id=user_id,
            username=f"us28-{token[:20]}",
            email=f"{token}@example.com",
        )
    )
    await session.commit()
    return user_id


_ROW_SQL = text(
    "SELECT total_xp, level, current_streak, best_streak, correct_count, "
    "incorrect_count, updated_at FROM user_game_profile WHERE user_id = :u"
)


async def _row(session: AsyncSession, user_id: str):
    """The persisted row, read over raw SQL so the identity map cannot answer."""
    return (await session.execute(_ROW_SQL, {"u": user_id})).one()


async def _row_count(session: AsyncSession, user_id: str) -> int:
    return (
        await session.execute(
            text("SELECT COUNT(*) FROM user_game_profile WHERE user_id = :u"),
            {"u": user_id},
        )
    ).scalar_one()


def _recording_seam(
    calls: list[StreakContext],
    decide: Callable[[StreakContext], EconomyDecision] | None = None,
) -> Callable[[StreakContext], EconomyDecision]:
    """A seam that records every context it is handed, then delegates."""

    def _seam(ctx: StreakContext) -> EconomyDecision:
        calls.append(ctx)
        if decide is not None:
            return decide(ctx)
        return EconomyDecision(
            xp_delta=0, total_xp=ctx.before.total_xp, level=ctx.before.level
        )

    return _seam


def _no_economy(ctx: StreakContext) -> EconomyDecision:
    """The explicit "this answer moves no XP" seam.

    US-28 expressed that with the ``economy=None`` default; M-2 (US-33 security
    finding) removed the default, because a caller that forgot the seam would
    record a streak and silently skip the whole economy. The intent of the old
    default survives here, as a value the caller has to hand over on purpose.
    """
    return EconomyDecision(
        xp_delta=0, total_xp=ctx.before.total_xp, level=ctx.before.level
    )


def _grant(xp: int, level: int) -> Callable[[StreakContext], EconomyDecision]:
    """A seam that awards ``xp`` onto the pre-answer total and sets ``level``."""

    def _seam(ctx: StreakContext) -> EconomyDecision:
        return EconomyDecision(
            xp_delta=xp, total_xp=ctx.before.total_xp + xp, level=level
        )

    return _seam


# --------------------------------------------------------------------------- #
# A. AC-3 / AC-4: lazy creation and the streak lifecycle
# --------------------------------------------------------------------------- #


async def test_first_correct_answer_creates_the_profile_lazily(session):
    """AC-3/AC-4: no row exists yet -> the answer creates it, streak 1."""
    user_id = await _seed_user(session)

    outcome = await record_answer(
        session, user_id, is_correct=True, economy=_no_economy
    )

    assert outcome.created_profile is True
    assert (outcome.current_streak, outcome.best_streak) == (1, 1)
    assert (outcome.correct_count, outcome.incorrect_count) == (1, 0)
    assert (outcome.total_xp, outcome.level) == (0, 1)
    assert outcome.xp_delta == 0

    row = await _row(session, user_id)
    assert (row.current_streak, row.best_streak, row.correct_count) == (1, 1, 1)
    assert (row.total_xp, row.level) == (0, 1)
    assert row.updated_at is not None


async def test_first_ever_incorrect_answer_creates_the_profile_lazily(session):
    """AC-3: the very first answer of a user may be an incorrect one."""
    user_id = await _seed_user(session)

    outcome = await record_answer(
        session, user_id, is_correct=False, economy=_no_economy
    )

    assert outcome.created_profile is True
    assert (outcome.current_streak, outcome.best_streak) == (0, 0)  # D-5: no -1
    assert (outcome.correct_count, outcome.incorrect_count) == (0, 1)
    assert (outcome.total_xp, outcome.level) == (0, 1)

    assert await _row_count(session, user_id) == 1


@pytest.mark.parametrize("answers", [(True,) * 1, (True,) * 3, (True, False, True)])
async def test_exactly_one_profile_row_per_user(session, answers):
    """One economy row per user, however many answers arrive (US-26 D-1)."""
    user_id = await _seed_user(session)

    for is_correct in answers:
        await record_answer(
            session, user_id, is_correct=is_correct, economy=_no_economy
        )

    assert await _row_count(session, user_id) == 1


async def test_current_streak_counts_consecutive_correct_answers(session):
    """AC-4: correct -> current_streak += 1, best follows it up."""
    user_id = await _seed_user(session)

    streaks = []
    for _ in range(3):
        outcome = await record_answer(
            session, user_id, is_correct=True, economy=_no_economy
        )
        streaks.append((outcome.current_streak, outcome.best_streak))

    assert streaks == [(1, 1), (2, 2), (3, 3)]


async def test_incorrect_resets_current_streak_and_preserves_best(session):
    """AC-4 / D-5: incorrect zeroes the streak and leaves best untouched."""
    user_id = await _seed_user(session)
    for _ in range(4):
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)

    reset = await record_answer(session, user_id, is_correct=False, economy=_no_economy)

    assert (reset.current_streak, reset.best_streak) == (0, 4)
    assert (reset.correct_count, reset.incorrect_count) == (4, 1)


@pytest.mark.parametrize("repeat", [1, 3])
async def test_incorrect_never_drives_the_streak_negative(session, repeat):
    """D-5: there is no negative-streak concept; 0 is the floor for both."""
    user_id = await _seed_user(session)

    for _ in range(repeat):
        outcome = await record_answer(
            session, user_id, is_correct=False, economy=_no_economy
        )

    assert outcome.current_streak == 0
    assert outcome.best_streak == 0
    assert outcome.incorrect_count == repeat


async def test_best_streak_is_a_high_water_mark(session):
    """AC-4: best_streak only ever rises and is never below current_streak."""
    user_id = await _seed_user(session)

    seen = []
    for is_correct in (True, True, False, True, True, True, False, True):
        outcome = await record_answer(
            session, user_id, is_correct=is_correct, economy=_no_economy
        )
        seen.append((outcome.current_streak, outcome.best_streak))

    assert seen == [
        (1, 1),
        (2, 2),
        (0, 2),
        (1, 2),
        (2, 2),
        (3, 3),
        (0, 3),
        (1, 3),
    ]
    bests = [best for _, best in seen]
    assert bests == sorted(bests)  # monotonic: never reset


async def test_counters_track_every_answer(session):
    """correct_count / incorrect_count are lifetime counters (US-36 tiebreak)."""
    user_id = await _seed_user(session)

    for is_correct in (True, False, True, True, False):
        outcome = await record_answer(
            session, user_id, is_correct=is_correct, economy=_no_economy
        )

    assert (outcome.correct_count, outcome.incorrect_count) == (3, 2)


async def test_updated_at_refreshes_on_every_answer(session):
    """US-26: updated_at is a maintenance timestamp, not a creation stamp."""
    user_id = await _seed_user(session)

    await record_answer(session, user_id, is_correct=True, economy=_no_economy)
    first = (await _row(session, user_id)).updated_at

    await asyncio.sleep(0.001)
    await record_answer(session, user_id, is_correct=True, economy=_no_economy)
    second = (await _row(session, user_id)).updated_at

    assert second > first


# --------------------------------------------------------------------------- #
# B. The narrow economy seam (US-33 wires the real engine into it)
# --------------------------------------------------------------------------- #


async def test_seam_receives_the_post_increment_streak(session):
    """OQ-5: the multiplier consumes the streak *including* this answer."""
    user_id = await _seed_user(session)
    calls: list[StreakContext] = []

    for _ in range(3):
        await record_answer(
            session, user_id, is_correct=True, economy=_recording_seam(calls)
        )

    assert [ctx.current_streak for ctx in calls] == [1, 2, 3]
    assert [ctx.best_streak for ctx in calls] == [1, 2, 3]
    assert [ctx.is_correct for ctx in calls] == [True] * 3


async def test_seam_receives_zero_streak_on_incorrect(session):
    """D-5: a penalty is never streak-scaled, so the seam sees a reset streak."""
    user_id = await _seed_user(session)
    calls: list[StreakContext] = []
    for _ in range(2):
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)

    await record_answer(
        session, user_id, is_correct=False, economy=_recording_seam(calls)
    )

    assert len(calls) == 1
    assert calls[0].current_streak == 0
    assert calls[0].best_streak == 2  # high-water mark survives the reset


async def test_seam_context_carries_the_pre_answer_state_and_flags(session):
    """The seam may read the pre-answer total (OQ-4 clamp floor) and counters."""
    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(100, 2))
    calls: list[StreakContext] = []

    await record_answer(
        session, user_id, is_correct=False, economy=_recording_seam(calls)
    )

    ctx = calls[0]
    assert ctx.user_id == user_id
    assert ctx.created_profile is False
    assert ctx.before == ProfileSnapshot(
        total_xp=100,
        level=2,
        current_streak=1,
        best_streak=1,
        correct_count=1,
        incorrect_count=0,
    )
    assert (ctx.correct_count, ctx.incorrect_count) == (1, 1)


async def test_seam_none_leaves_xp_and_level_untouched(session):
    """US-28 alone moves no XP: the economy is a different story's business.

    The nodeid keeps the word ``none`` because this is US-28's case, unchanged in
    intent — but M-2 removed the ``economy=None`` **default** that used to express
    it, so the no-op is spelled :data:`_no_economy` now. The point of the signature
    change is exactly that a caller can no longer arrive here by forgetting an
    argument; :func:`test_economy_is_a_required_keyword_argument` holds that half.
    """
    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(40, 1))

    outcome = await record_answer(
        session, user_id, is_correct=True, economy=_no_economy
    )

    assert outcome.xp_delta == 0
    assert (outcome.total_xp, outcome.level) == (40, 1)
    assert (outcome.previous_total_xp, outcome.previous_level) == (40, 1)
    assert (outcome.current_streak, outcome.correct_count) == (2, 2)


async def test_seam_decision_is_persisted_and_reported(session):
    """The seam's decision is what lands on the row, plus the before-values."""
    user_id = await _seed_user(session)

    outcome = await record_answer(
        session, user_id, is_correct=True, economy=_grant(40, 2)
    )

    assert outcome.xp_delta == 40
    assert (outcome.total_xp, outcome.level) == (40, 2)
    assert (outcome.previous_total_xp, outcome.previous_level) == (0, 1)

    row = await _row(session, user_id)
    assert (row.total_xp, row.level) == (40, 2)


async def test_blocked_penalty_reports_a_zero_delta(session):
    """LV-09b shape: at the OQ-4 floor the clamp yields xp_delta 0, not a loss."""
    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(4, 1))

    def _floor(ctx: StreakContext) -> EconomyDecision:
        return EconomyDecision(xp_delta=0, total_xp=ctx.before.total_xp, level=1)

    outcome = await record_answer(session, user_id, is_correct=False, economy=_floor)

    assert outcome.is_correct is False
    assert outcome.xp_delta == 0
    assert outcome.total_xp == 4
    assert outcome.incorrect_count == 1


def test_streak_for_multiplier_alias_matches_current_streak():
    """The OQ-5 alias is the same value, not a second source of truth."""
    outcome = AnswerOutcome(
        user_id="u",
        created_profile=False,
        is_correct=True,
        xp_delta=0,
        total_xp=0,
        level=1,
        previous_total_xp=0,
        previous_level=1,
        current_streak=5,
        best_streak=9,
        correct_count=5,
        incorrect_count=0,
    )

    assert outcome.streak_for_multiplier == outcome.current_streak


# --------------------------------------------------------------------------- #
# C. Error paths of the seam (fail before anything is written)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("xp_delta", "total_xp", "level", "message"),
    [
        (40, 100, 1, "total_xp"),  # delta and total disagree
        (-4, 4, 1, "total_xp"),  # sign/total mismatch
        (-4, -4, 1, "negative"),  # self-consistent but below the OQ-4 floor
        (40, 40, 0, "level"),  # level 0 does not exist (D-1/OQ-3)
        (True, True, 1, "int"),  # bool is not an amount of XP
    ],
)
async def test_inconsistent_seam_decision_is_rejected(
    session, xp_delta, total_xp, level, message
):
    """A malformed decision raises before the row is touched."""
    user_id = await _seed_user(session)

    def _bad(ctx: StreakContext) -> EconomyDecision:
        return EconomyDecision(xp_delta=xp_delta, total_xp=total_xp, level=level)

    with pytest.raises(ValueError) as excinfo:
        await record_answer(session, user_id, is_correct=True, economy=_bad)

    assert message in str(excinfo.value).lower()
    await session.rollback()
    assert await _row_count(session, user_id) == 0  # nothing committed


async def test_rejected_seam_decision_rolls_back_nothing_and_writes_nothing(
    session,
):
    """An existing row keeps its exact state when the decision is rejected."""
    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(120, 3))
    before = await _row(session, user_id)

    with pytest.raises(ValueError):
        await record_answer(
            session,
            user_id,
            is_correct=False,
            economy=lambda ctx: EconomyDecision(xp_delta=-999, total_xp=1, level=1),
        )
    await session.rollback()

    after = await _row(session, user_id)
    assert tuple(after) == tuple(before)


@pytest.mark.parametrize("economy", [42, "grant", object()])
async def test_non_callable_seam_is_rejected(session, economy):
    """The seam must be a callable — a typo fails loudly, not silently."""
    user_id = await _seed_user(session)

    with pytest.raises(TypeError):
        await record_answer(session, user_id, is_correct=True, economy=economy)


async def test_async_seam_is_rejected(session):
    """AC-2, structurally: an ``async def`` seam could await ``session.commit()``
    and release the row lock mid-answer, so the seam is sync-only."""
    user_id = await _seed_user(session)

    async def _committing_seam(ctx: StreakContext) -> EconomyDecision:
        return EconomyDecision(
            xp_delta=0, total_xp=ctx.before.total_xp, level=ctx.before.level
        )

    with pytest.raises(TypeError):
        await record_answer(session, user_id, is_correct=True, economy=_committing_seam)


@pytest.mark.parametrize("is_correct", [1, 0, "true", None])
async def test_non_bool_is_correct_is_rejected(session, is_correct):
    """The answer flag is a bool: 0/"true" must not silently mean *incorrect*."""
    user_id = await _seed_user(session)

    with pytest.raises(TypeError):
        await record_answer(
            session, user_id, is_correct=is_correct, economy=_no_economy
        )


async def test_empty_user_id_is_rejected(session):
    """A missing identity never reaches the database (FK error would be loud)."""
    with pytest.raises(ValueError):
        await record_answer(session, "", is_correct=True, economy=_no_economy)


# --------------------------------------------------------------------------- #
# D. AC-1 / AC-2: locking and the single commit
# --------------------------------------------------------------------------- #


class _CommitLog:
    """Ordered recorder of the real ``COMMIT`` stream and of seam calls.

    The listener is the *engine-level* ``commit`` event, not the Session's
    ``after_commit``: releasing a SAVEPOINT fires ``after_commit`` as well
    (verified against the test database), so a Session listener would count the
    lazy-creation savepoint as a second commit and the assertion would be wrong
    for the right reason.
    """

    def __init__(self) -> None:
        self.log: list[str] = []

    def _commit(self, *_args) -> None:
        self.log.append("commit")

    def seam(self, *_args) -> None:
        self.log.append("seam")


@asynccontextmanager
async def _commit_log(async_engine):
    recorder = _CommitLog()
    event.listen(async_engine.sync_engine, "commit", recorder._commit)
    try:
        yield recorder
    finally:
        event.remove(async_engine.sync_engine, "commit", recorder._commit)


@pytest.mark.parametrize("seed_answer", [False, True])
async def test_exactly_one_commit_per_answer(session, async_engine, seed_answer):
    """AC-2: one HTTP answer = one COMMIT — including the lazy-create path.

    The listener counts real transaction commits, so the savepoint a lazy
    creation opens (``begin_nested``) does *not* show up here.
    """
    user_id = await _seed_user(session)
    if seed_answer:
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)

    async with _commit_log(async_engine) as commits:
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)

    assert commits.log == ["commit"]


async def test_the_seam_runs_before_the_single_commit(session, async_engine):
    """AC-2: nothing commits ahead of the answer, so the seam still holds the
    ``FOR UPDATE`` row lock — an early commit would appear before ``seam``."""
    user_id = await _seed_user(session)
    await record_answer(
        session, user_id, is_correct=True, economy=_no_economy
    )  # row exists

    async with _commit_log(async_engine) as commits:

        def _seam(ctx: StreakContext) -> EconomyDecision:
            commits.seam()
            return EconomyDecision(
                xp_delta=0,
                total_xp=ctx.before.total_xp,
                level=ctx.before.level,
            )

        await record_answer(session, user_id, is_correct=True, economy=_seam)

    assert commits.log == ["seam", "commit"]


async def test_select_for_update_reaches_the_wire(session, async_engine):
    """AC-1: the profile read is an actual ``FOR UPDATE`` in the emitted SQL.

    SQLite would accept the same ORM call and lock nothing, so the claim is
    pinned on the statement text Postgres receives.
    """
    user_id = await _seed_user(session)
    await record_answer(
        session, user_id, is_correct=True, economy=_no_economy
    )  # lazy-create first

    statements: list[str] = []

    def _capture(sync_conn, cursor, statement, *args, **kwargs) -> None:
        statements.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _capture)

    profile_selects = [
        stmt
        for stmt in statements
        if "user_game_profile" in stmt and stmt.lstrip().upper().startswith("SELECT")
    ]
    assert profile_selects, statements
    assert any("FOR UPDATE" in stmt.upper() for stmt in profile_selects)


async def test_concurrent_answers_serialize_and_the_second_sees_the_committed_streak(
    session, async_engine
):
    """AC-1 + AC-2, the dedicated test of the plan.

    A first answer holds the row lock inside its transaction; the second answer
    must block rather than read a pre-commit snapshot. When the first commits,
    the second continues *from its committed streak* — 7 + 1, never 0 + 1.
    """
    user_id = await _seed_user(session)
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as holder:
        # The holder plays the role of the first answer's transaction: it writes
        # the row and keeps the lock until it commits.
        await holder.execute(
            text(
                "INSERT INTO user_game_profile (user_id, current_streak, "
                "best_streak, correct_count) VALUES (:u, 7, 7, 7)"
            ),
            {"u": user_id},
        )

        task = asyncio.create_task(
            record_answer(session, user_id, is_correct=True, economy=_no_economy)
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # blocked on the row lock, not on a bug in the test

        await holder.commit()  # the first answer lands
        outcome = await asyncio.wait_for(task, timeout=10)

    assert outcome.created_profile is False
    assert outcome.current_streak == 8
    assert outcome.best_streak == 8
    assert outcome.correct_count == 8

    row = await _row(session, user_id)
    assert (row.current_streak, row.correct_count) == (8, 8)


async def test_concurrent_first_answers_create_exactly_one_profile_row(
    session, async_engine
):
    """AC-3 under a race: two first-ever answers, one row, no lost update.

    Both find no profile and both try to create it; the savepoint + retry path
    turns the unique-violation loser into a reader of the winner's row.
    """
    user_id = await _seed_user(session)
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as first, factory() as second:
        outcomes = await asyncio.gather(
            *(
                record_answer(s, user_id, is_correct=True, economy=_no_economy)
                for s in (first, second)
            )
        )

    assert sorted(outcome.created_profile for outcome in outcomes) == [False, True]
    assert await _row_count(session, user_id) == 1
    row = await _row(session, user_id)
    assert (row.current_streak, row.best_streak, row.correct_count) == (2, 2, 2)


async def test_pk_race_loser_rereads_the_winners_row(
    session, async_engine, monkeypatch
):
    """The savepoint-retry path, pinned *deterministically*.

    The gather test above is timing-dependent: if the loser's first ``FOR
    UPDATE`` read already sees the winner's committed row it never reaches the
    ``IntegrityError`` handler, and if it does not, whether the blocked INSERT
    lands before or after the winner commits depends on the scheduler. Here the
    winner's row exists and is committed up front, and exactly the first lock
    read is blinded — so the loser is forced down insert → IntegrityError →
    re-lock → ``return raced, False``, and continues from the winner's row
    instead of losing the answer or creating a second row.
    """
    from src.services import game_service

    user_id = await _seed_user(session)
    await record_answer(
        session, user_id, is_correct=True, economy=_no_economy
    )  # winner: row committed

    real_lock = game_service._lock_profile
    lock_calls = {"n": 0}

    async def _blind_first_read(sess, uid):
        lock_calls["n"] += 1
        if lock_calls["n"] == 1:
            return None  # the race: the winner is invisible to this snapshot
        return await real_lock(sess, uid)

    monkeypatch.setattr(game_service, "_lock_profile", _blind_first_read)

    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as loser:
        outcome = await record_answer(
            loser, user_id, is_correct=False, economy=_no_economy
        )

    assert lock_calls["n"] == 2  # blinded first read + the retry re-read
    assert outcome.created_profile is False
    # The loser scored its answer on the WINNER's row: streak reset, best kept.
    assert (outcome.current_streak, outcome.best_streak) == (0, 1)
    assert (outcome.correct_count, outcome.incorrect_count) == (1, 1)
    assert await _row_count(session, user_id) == 1
    row = await _row(session, user_id)
    assert (row.current_streak, row.best_streak, row.incorrect_count) == (0, 1, 1)


async def test_missing_user_raises_and_leaves_no_profile(session):
    """The FK to ``users.id`` is the backstop for a deleted user (US-33 404s
    before ever reaching the service)."""
    from sqlalchemy.exc import IntegrityError

    user_id = str(uuid7())

    with pytest.raises(IntegrityError):
        await record_answer(session, user_id, is_correct=True, economy=_no_economy)

    await session.rollback()
    assert await _row_count(session, user_id) == 0


# --------------------------------------------------------------------------- #
# E. D-6 coexistence: the legacy table stays out of it
# --------------------------------------------------------------------------- #


async def test_record_answer_never_touches_user_stats(session):
    """D-6: XP lives in user_game_profile; user_stats is untouched until US-34."""
    user_id = await _seed_user(session)

    for is_correct in (True, True, False):
        await record_answer(
            session, user_id, is_correct=is_correct, economy=_no_economy
        )

    legacy = (
        await session.execute(select(UserStats).where(UserStats.user_id == user_id))
    ).scalar_one_or_none()
    assert legacy is None

    profile = (
        await session.execute(
            select(UserGameProfile).where(UserGameProfile.user_id == user_id)
        )
    ).scalar_one()
    assert (profile.current_streak, profile.best_streak) == (0, 2)


# --------------------------------------------------------------------------- #
# F. US-33 security findings M-1 / M-2: the seam is REQUIRED, and its RETURN
#    value is checked for awaitability at the call boundary too.
# --------------------------------------------------------------------------- #


def _awaitable_seam_factory(
    session_holder: list[AsyncSession],
) -> Callable[[StreakContext], EconomyDecision]:
    """A *sync* callable that hands back an un-awaited coroutine (M-1).

    ``inspect.iscoroutinefunction`` on the callable is ``False`` here, so the
    existing guard cannot see this: the awaitable only appears in the RETURN
    value, which is exactly the hole M-1 closes. The coroutine would also leak a
    "never awaited" RuntimeWarning if the service did not close it.
    """

    async def _deferred(ctx: StreakContext) -> EconomyDecision:
        # Would be reachable only if something awaited us — i.e. only if the
        # guard is missing. It records the session so the test can prove the
        # body never ran.
        session_holder.append(None)  # pragma: no cover - guarded away
        return EconomyDecision(
            xp_delta=0, total_xp=ctx.before.total_xp, level=ctx.before.level
        )

    def _seam(ctx: StreakContext) -> EconomyDecision:
        return _deferred(ctx)  # a coroutine object, not a decision

    return _seam


async def test_economy_is_a_required_keyword_argument(session):
    """M-2: the silent-``None`` trap is gone — omitting the seam is a TypeError.

    US-28 shipped ``economy: EconomySeam | None = None``, so a caller that simply
    forgot the seam recorded the streak and moved no XP *silently* — on the answer
    endpoint that is a silent economy outage. The parameter is required now, and
    a no-XP answer must say so with an explicit seam.
    """
    user_id = await _seed_user(session)

    with pytest.raises(TypeError) as excinfo:
        await record_answer(session, user_id, is_correct=True)

    assert "economy" in str(excinfo.value)
    await session.rollback()
    assert await _row_count(session, user_id) == 0  # nothing committed


@pytest.mark.parametrize("economy", [None, False, 0])
async def test_explicit_none_seam_is_rejected(session, economy):
    """M-2: ``None`` is not a seam any more — not implicitly, not explicitly."""
    user_id = await _seed_user(session)

    with pytest.raises(TypeError) as excinfo:
        await record_answer(session, user_id, is_correct=True, economy=economy)

    assert "economy" in str(excinfo.value)


async def test_awaitable_returning_seam_is_rejected_and_commits_nothing(
    session, async_engine
):
    """M-1: an awaitable RETURN is caught at the call boundary, before any write.

    The callable is sync (so the pre-existing ``iscoroutinefunction`` guard is
    blind to it) but returns a coroutine object. Awaiting it inside the answer
    transaction would be the exact AC-2 trap — a seam that can ``await
    session.commit()`` — so the service rejects it and the row keeps its
    byte-identical state with zero COMMITs.
    """
    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(120, 3))
    before = await _row(session, user_id)
    ran: list = []

    async with _commit_log(async_engine) as commits:
        with pytest.raises(TypeError) as excinfo:
            await record_answer(
                session,
                user_id,
                is_correct=True,
                economy=_awaitable_seam_factory(ran),
            )

    assert "awaitable" in str(excinfo.value).lower()
    assert commits.log == []  # the rejection commits nothing
    assert ran == []  # the coroutine body never executed
    await session.rollback()
    assert tuple(await _row(session, user_id)) == tuple(before)


# --------------------------------------------------------------------------- #
# G. US-33 F-2: the router-owned locked pre-answer read (the unlock gate's input)
# --------------------------------------------------------------------------- #


async def test_pre_answer_level_is_one_for_a_user_without_a_row(session):
    """No profile row yet -> the gate reads the D-1/OQ-3 default (level 1).

    A first-ever answer must not be gated by a missing row: the lazy creation of
    AC-3 has not happened at gate time, and the column default is level 1.
    """
    from src.services.game_service import pre_answer_level

    user_id = await _seed_user(session)

    assert await pre_answer_level(session, user_id) == 1


async def test_pre_answer_level_refuses_a_missing_identity(session):
    """An empty ``user_id`` is a caller bug, refused before any SQL (AC-1 symmetry).

    Same rule as :func:`record_answer`: the gate read is inside the answer
    transaction, so a blank identity reaching the database would lock nothing and
    answer ``1`` — a level the caller would then treat as a real pre-answer state.
    """
    from src.services.game_service import pre_answer_level

    with pytest.raises(ValueError, match="non-empty"):
        await pre_answer_level(session, "")


async def test_pre_answer_level_reads_the_locked_row_without_mutating_it(
    session, async_engine
):
    """The gate read returns the persisted level and writes nothing (no COMMIT)."""
    from src.services.game_service import pre_answer_level

    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(476, 4))
    before = await _row(session, user_id)

    async with _commit_log(async_engine) as commits:
        statements: list[str] = []

        def _capture(sync_conn, cursor, statement, *args, **kwargs) -> None:
            statements.append(statement)

        event.listen(async_engine.sync_engine, "before_cursor_execute", _capture)
        try:
            level = await pre_answer_level(session, user_id)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", _capture)

    assert level == 4
    assert commits.log == []  # a gate read is not a transaction of its own
    profile_selects = [
        stmt
        for stmt in statements
        if "user_game_profile" in stmt and stmt.lstrip().upper().startswith("SELECT")
    ]
    assert profile_selects, statements  # F-2: the read is FOR UPDATE
    assert any("FOR UPDATE" in stmt.upper() for stmt in profile_selects)

    await session.rollback()
    assert tuple(await _row(session, user_id)) == tuple(before)


async def test_pre_answer_level_blocks_a_concurrent_writer(session, async_engine):
    """F-2: the gate holds the row lock, so a racing answer cannot slip its level in.

    The gate must read the level the *answer transaction* will score against. If
    the read were a plain SELECT, a concurrent answer could commit a level-up
    between the gate and the write and unlock content the gate never approved.
    """
    from src.services.game_service import pre_answer_level

    user_id = await _seed_user(session)
    await record_answer(session, user_id, is_correct=True, economy=_grant(476, 4))
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as holder, factory() as rival:
        gate = await pre_answer_level(holder, user_id)  # takes FOR UPDATE
        assert gate == 4

        task = asyncio.create_task(
            record_answer(rival, user_id, is_correct=True, economy=_grant(40, 5))
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # blocked on the lock the gate holds

        await holder.rollback()  # the gate rejects the answer: nothing was written
        outcome = await asyncio.wait_for(task, timeout=10)

    # The seed answer was correct too, so the rival continues from streak 1 -> 2:
    # it waited for the gate's lock instead of reading a pre-commit snapshot.
    assert outcome.level == 5
    assert outcome.previous_level == 4
    assert (outcome.current_streak, outcome.correct_count) == (2, 2)
