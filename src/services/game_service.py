"""US-28: the streak lifecycle of ``user_game_profile``, inside one transaction.

This module owns exactly three things, and they are the three hard parts of the
answer path (``docs/sprint4-plan.md`` US-28 AC-1..AC-4):

1. **Row locking (AC-1).** Every mutation of a user's economy row happens behind
   ``SELECT ... FOR UPDATE`` on that row, so two concurrent submissions of the
   same user serialize on the database rather than on application logic.
2. **One commit per answer (AC-2, Rev 3 R-2).** :func:`record_answer` issues a
   single ``COMMIT`` as its last act, and nothing it calls may commit or roll
   back — an early commit would release the row lock and silently void AC-1.
   That is why the economy seam is a **plain synchronous callable**: an
   ``async def`` seam could ``await session.commit()`` from inside the
   transaction (the exact trap ``RateLimitService.check()`` walks into, BE-7).
   Rate limiting therefore stays outside this module, on its own short-lived
   session, and lands with US-32/US-33.
3. **Streak arithmetic (AC-4, D-5).** A correct answer increments
   ``current_streak`` and lifts ``best_streak`` to the higher of the two; an
   incorrect answer resets ``current_streak`` to 0 and leaves ``best_streak``
   untouched. There is no negative-streak concept, and ``0`` is the floor for
   both columns.

**What this module deliberately does NOT do.** It computes no XP and no unlocks:
the XP formula is US-29, the level curve and the OQ-4 clamp are US-30, unlocks
are US-31, and the endpoint that assembles the wire response is US-33. All of it
arrives through the single narrow :data:`EconomySeam` parameter, which is handed
the pre-answer state plus the **post-increment** streak (OQ-5 — the multiplier
always sees the streak *including* the answer being scored) and returns the
``(xp_delta, total_xp, level)`` triple to persist. The seam is a **required**
argument (US-33 security finding M-2): an optional ``None`` default let a caller
that forgot it record the streak and move no XP *silently*, which on the answer
endpoint is a silent economy outage. An answer that must not touch XP — every
US-28-only test, and nothing in production — passes an explicit no-op seam.
:class:`AnswerOutcome` carries the before-values as well, which is precisely what
US-33 needs to call ``unlock_service.newly_unlocked(previous_level, level)``.

The seam's **return value** is checked for awaitability too (M-1, next to the
existing ``iscoroutinefunction`` check on the callable): a plain ``def`` that
hands back a coroutine object is the same AC-2 trap wearing a disguise, so
:func:`record_answer` rejects it at the call boundary before anything is written.

``mode`` and ``bit_width`` are deliberately absent from :class:`StreakContext`:
they are wire fields, and validating them is US-32's job. US-33 binds them by
closing over the already-validated ``AnswerRequest`` (sketch, not an API
contract — the engine's real signature is US-29's):

    economy=lambda ctx: score(request.mode, request.bit_width,
                              ctx.current_streak, ctx.is_correct)

Keeping them out of the context is what lets this module stay independent of the
request schema instead of re-checking what the router already checked.

The unlock gate of US-33 (security finding F-2) is the one read this module offers
*outside* the seam: :func:`pre_answer_level` hands the router the ``FOR UPDATE``
locked, pre-answer level, so a combo the answer itself would unlock still 403s.
It is a locked read only — it never mutates and never commits, which is what lets
the 403 path leave the row byte-identical.

The lazy row creation mirrors the legacy ``UserStats`` pattern in
``src/routers/game.py:72-79`` (untouched until US-34): one row per user, created
by the first answer that needs it, never eagerly at registration.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.game import UserGameProfile

__all__ = [
    "AnswerOutcome",
    "EconomyDecision",
    "EconomySeam",
    "ProfileSnapshot",
    "StreakContext",
    "pre_answer_level",
    "record_answer",
]

# D-1 / OQ-3 — "a player starts at level 1 with 0 XP". The level the unlock gate
# assumes for a user whose profile row does not exist yet (US-33 AC-3 lazy
# creation): there is no stored level to read, and 1 is the column default.
_FIRST_LEVEL = 1


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """An immutable copy of the six mutable economy columns of one profile row.

    ``before`` is what the seam clamps against (OQ-4: the floor is ``cum(level)``,
    a geometric sum that no SQL CHECK can express), so it is a value object rather
    than the live ORM entity — a seam must not be able to write to the row.
    """

    total_xp: int
    level: int
    current_streak: int
    best_streak: int
    correct_count: int
    incorrect_count: int


@dataclass(frozen=True, slots=True)
class StreakContext:
    """Everything the economy seam may read for one answer.

    ``current_streak`` / ``best_streak`` / ``correct_count`` / ``incorrect_count``
    are the **post-answer** values (OQ-5), while ``before`` holds the pre-answer
    totals. ``user_id`` and ``created_profile`` are passed through so a seam can
    log or branch on the lazy-creation case without another query.
    """

    user_id: str
    created_profile: bool
    is_correct: bool
    before: ProfileSnapshot
    current_streak: int
    best_streak: int
    correct_count: int
    incorrect_count: int


@dataclass(frozen=True, slots=True)
class EconomyDecision:
    """The seam's answer: what to persist for this answer.

    ``xp_delta`` is the **signed, post-clamp** delta (plan §4.1): the positive
    award on a correct answer, the negated penalty on an incorrect one, and ``0``
    when the OQ-4 floor blocks a penalty (LV-09b — clients must not assume a
    nonzero delta on ``is_correct: false``). ``total_xp``/``level`` are the new
    absolute values.

    The three fields must agree: ``total_xp == before.total_xp + xp_delta``.
    :func:`record_answer` validates that, so a mis-wired seam fails on the
    first answer of the sprint instead of corrupting the economy quietly.
    """

    xp_delta: int
    total_xp: int
    level: int


type EconomySeam = Callable[[StreakContext], EconomyDecision]
"""The ONE narrow seam between the streak transaction (US-28) and the XP economy.

US-33 supplies it, composing US-29's ``xp_engine``, US-30's ``level_service``
(``clamped_total_xp`` / ``level_from_xp``) and the OQ-4 clamp into a single pure
function. Sync by contract, and validated as such at the call boundary.
"""


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    """The persisted result of one recorded answer, ready for US-33's response.

    ``xp_delta``/``total_xp``/``level``/``*_streak``/``*_count`` are post-answer;
    ``previous_total_xp``/``previous_level`` are pre-answer. ``newly_unlocked`` is
    NOT here: it is a pure function of the two levels and belongs to US-31's
    ``unlock_service`` — see the module docstring.
    """

    user_id: str
    created_profile: bool
    is_correct: bool
    xp_delta: int
    total_xp: int
    level: int
    previous_total_xp: int
    previous_level: int
    current_streak: int
    best_streak: int
    correct_count: int
    incorrect_count: int

    @property
    def streak_for_multiplier(self) -> int:
        """The streak the XP multiplier must consume (OQ-5: post-increment)."""
        return self.current_streak


async def _lock_profile(session: AsyncSession, user_id: str) -> UserGameProfile | None:
    """``SELECT ... FOR UPDATE`` on the economy row (AC-1).

    Returns ``None`` when the user has no row yet. The lock is held until the
    enclosing transaction ends — which is why the caller must never commit
    before :func:`record_answer` says so.
    """
    result = await session.execute(
        select(UserGameProfile)
        .where(UserGameProfile.user_id == user_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _lock_or_create_profile(
    session: AsyncSession, user_id: str
) -> tuple[UserGameProfile, bool]:
    """Return ``(locked row, created_now)`` — the lazy creation of AC-3.

    The insert runs inside a ``SAVEPOINT``: two first-ever answers of the same
    user race on the primary key, the loser gets an :class:`IntegrityError`, and
    rolling back to the savepoint leaves the *answer* transaction intact (AC-2 —
    a savepoint is not a commit, so the row lock is never released early). The
    loser then re-reads the winner's committed row under ``FOR UPDATE`` and
    continues from it, which is what makes the concurrent case a serialization
    rather than a lost update.

    Raises:
        IntegrityError: if the insert fails for a reason other than the race —
            i.e. the ``users`` row is gone and the foreign key rejects it. US-33
            answers that with a 404 before ever reaching this service.
    """
    profile = await _lock_profile(session, user_id)
    if profile is not None:
        return profile, False

    try:
        async with session.begin_nested():
            profile = UserGameProfile(user_id=user_id)
            session.add(profile)
            await session.flush()
    except IntegrityError:
        raced = await _lock_profile(session, user_id)
        if raced is None:
            raise  # not the PK race: the FK target vanished
        return raced, False
    return profile, True


async def pre_answer_level(session: AsyncSession, user_id: str) -> int:
    """The unlock gate's input (US-33, security finding F-2): the locked level.

    A ``SELECT ... FOR UPDATE`` on the profile row that returns its **pre-answer**
    ``level`` — and ``1`` (D-1/OQ-3, the column default) when the user has no row
    yet, because the lazy creation of AC-3 has not happened at gate time. The
    router evaluates ``unlock_service.is_unlocked(mode, bit_width, level)`` on it,
    so a combination the answer itself would have unlocked is still refused (403).

    This is deliberately a plain locked **read**, not a seam: it must not be able
    to write, commit or roll back, so the 403 path leaves the row byte-identical
    (US-33 N12) and the caller's answer transaction stays intact.

    Auditor note (Wave 2a review M-1) — the caller contract, stated because the
    return type cannot: ``_FIRST_LEVEL`` answers **two different situations**, a user
    whose ``users`` row exists but who has not answered yet, and an identity with no
    ``users`` row at all. Both are indistinguishable here and both answer ``1``, so a
    caller that reached this function first has to have established existence —
    ``submit_answer`` does exactly that, one statement earlier, and answers 404 (see
    :func:`src.services.answer_service.user_exists`). Over HTTP there is therefore no
    path where a phantom identity is handed a level, and no attacker-controlled cost:
    the ghost's request ends at that 404 with one ``INSERT`` (its own rate-limit row)
    and one ``SELECT`` behind it, never a profile write. The distinction is pinned by
    ``test_pre_answer_level_is_one_for_a_user_without_a_row`` in
    ``tests/test_game_service.py``, and the HTTP half by
    ``test_n14_unknown_user_leaves_no_row_and_404s`` in
    ``tests/test_answer_endpoint.py``.
    A future caller that skips the existence check inherits level 1, not an error —
    that is the trade-off this note exists to make visible, and the reason the
    existing 404 gate is not folded into this function.

    Raises:
        ValueError: empty ``user_id`` — same rule as :func:`record_answer`, so a
            missing identity never reaches the database.
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    profile = await _lock_profile(session, user_id)
    return _FIRST_LEVEL if profile is None else profile.level


def _snapshot(profile: UserGameProfile) -> ProfileSnapshot:
    """Freeze the current column values of a locked row."""
    return ProfileSnapshot(
        total_xp=profile.total_xp,
        level=profile.level,
        current_streak=profile.current_streak,
        best_streak=profile.best_streak,
        correct_count=profile.correct_count,
        incorrect_count=profile.incorrect_count,
    )


def _validate_amount(value: Any, field: str) -> int:
    """Reject anything that is not a plain ``int`` (``bool`` included).

    ``True`` is an ``int`` in Python and would silently mean *one point of XP*,
    so the type check comes first (D-8: XP is an integer, never a float).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"economy seam returned {value!r} for {field}; expected an int "
            "(XP is an integer multiple of 4 — no bool, no float)"
        )
    return value


def _require_sync_decision(decision: EconomyDecision, economy: EconomySeam) -> None:
    """M-1 (US-33 security finding): the seam's RETURN value must not be awaitable.

    ``inspect.iscoroutinefunction`` on the callable cannot see this: a plain ``def``
    that returns ``an_async_function(ctx)`` hands back a coroutine object, and
    anything that later awaits it is back inside the answer transaction — the exact
    AC-2 / Rev 3 R-2 trap (a commit between the ``FOR UPDATE`` read and the write).
    Rejecting at the call boundary means the row is never touched, and closing a
    coroutine object keeps the interpreter from emitting a "never awaited" warning
    for a decision that was refused on purpose.
    """
    if inspect.isawaitable(decision):
        if inspect.iscoroutine(decision):
            decision.close()
        raise TypeError(
            f"economy seam {getattr(economy, '__name__', type(economy).__name__)} "
            f"returned an awaitable ({type(decision).__name__}) instead of an "
            "EconomyDecision: awaiting it inside the answer transaction could "
            "release the FOR UPDATE row lock early (US-28 AC-2 / Rev 3 R-2, "
            "US-33 finding M-1)"
        )


def _validate_decision(before: ProfileSnapshot, decision: EconomyDecision) -> None:
    """Fail fast on a decision the row must not accept.

    Every check here mirrors an invariant the schema cannot express: the SQL
    CHECKs stop ``total_xp < 0`` / ``level < 1``, but only this validation stops a
    seam whose ``xp_delta`` disagrees with its ``total_xp`` (which is exactly how
    a clamp would be dropped by accident).
    """
    xp_delta = _validate_amount(decision.xp_delta, "xp_delta")
    total_xp = _validate_amount(decision.total_xp, "total_xp")
    level = _validate_amount(decision.level, "level")

    expected = before.total_xp + xp_delta
    if total_xp != expected:
        raise ValueError(
            f"economy seam is inconsistent: total_xp {total_xp} != "
            f"before.total_xp {before.total_xp} + xp_delta {xp_delta} "
            f"(expected {expected})"
        )
    if level < 1:
        raise ValueError(
            f"economy seam returned level {level}; level 0 does not exist "
            "(D-1/OQ-3: a player starts at level 1 with 0 XP)"
        )
    if total_xp < 0:
        raise ValueError(
            f"economy seam returned total_xp {total_xp}; the OQ-4 floor is "
            "cum(level) and the clamped total must never go negative"
        )


async def record_answer(
    session: AsyncSession,
    user_id: str,
    *,
    is_correct: bool,
    economy: EconomySeam,
) -> AnswerOutcome:
    """Record one answer: lock the row, apply the streak, commit once.

    Args:
        session: the answer transaction's session. The caller owns it and must
            NOT commit — :func:`record_answer` commits exactly once (AC-2).
        user_id: authenticated user; must already exist (US-33 404s otherwise).
        is_correct: the answer verdict, strictly a ``bool``.
        economy: the :data:`EconomySeam`, **required** (US-33 finding M-2). It used
            to default to ``None``, which let a caller that forgot it record the
            streak and move no XP without any signal; an answer that is meant to
            leave the economy alone has to say so with an explicit no-op seam
            (``EconomyDecision(0, ctx.before.total_xp, ctx.before.level)``).

    Returns:
        The frozen :class:`AnswerOutcome` for the wire response.

    Raises:
        ValueError: empty ``user_id``, or a seam decision that disagrees with
            itself (before anything is written — a rejected answer commits
            nothing).
        TypeError: non-``bool`` ``is_correct``, a seam that is not a *sync*
            callable, or a seam that returned an awaitable (M-1).
        IntegrityError: the ``users`` row disappeared mid-request.
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(is_correct, bool):
        raise TypeError(
            f"is_correct must be a bool, got {type(is_correct).__name__}: "
            "a truthy non-bool must not silently decide the streak"
        )
    if not callable(economy):
        raise TypeError(
            f"economy must be a sync callable, got {type(economy).__name__}: "
            "the seam is required (US-33 M-2) — pass an explicit no-op seam "
            "to record an answer that moves no XP"
        )
    if inspect.iscoroutinefunction(economy):
        raise TypeError(
            "economy must be a *sync* callable: an async seam could await "
            "session.commit() inside the answer transaction and release the "
            "FOR UPDATE row lock early (US-28 AC-2 / Rev 3 R-2)"
        )

    profile, created = await _lock_or_create_profile(session, user_id)
    before = _snapshot(profile)

    # AC-4 / D-5 — the streak lifecycle, on copies of the locked row's values.
    if is_correct:
        current_streak = before.current_streak + 1
        best_streak = max(before.best_streak, current_streak)
    else:
        current_streak = 0
        best_streak = before.best_streak
    correct_count = before.correct_count + (1 if is_correct else 0)
    incorrect_count = before.incorrect_count + (0 if is_correct else 1)

    context = StreakContext(
        user_id=user_id,
        created_profile=created,
        is_correct=is_correct,
        before=before,
        current_streak=current_streak,
        best_streak=best_streak,
        correct_count=correct_count,
        incorrect_count=incorrect_count,
    )
    decision = economy(context)
    _require_sync_decision(decision, economy)
    _validate_decision(before, decision)

    profile.total_xp = decision.total_xp
    profile.level = decision.level
    profile.current_streak = current_streak
    profile.best_streak = best_streak
    profile.correct_count = correct_count
    profile.incorrect_count = incorrect_count
    # `updated_at` is NOT assigned here on purpose: the ORM column declares an
    # `onupdate` (US-26), so the flush stamps it as part of this same UPDATE —
    # one clock, one statement, and no second writer of the timestamp.

    await session.commit()  # AC-2: the one and only COMMIT of this answer

    return AnswerOutcome(
        user_id=user_id,
        created_profile=created,
        is_correct=is_correct,
        xp_delta=decision.xp_delta,
        total_xp=profile.total_xp,
        level=profile.level,
        previous_total_xp=before.total_xp,
        previous_level=before.level,
        current_streak=profile.current_streak,
        best_streak=profile.best_streak,
        correct_count=profile.correct_count,
        incorrect_count=profile.incorrect_count,
    )
