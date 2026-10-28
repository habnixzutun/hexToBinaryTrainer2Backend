"""US-33: the economy seam and the wire assembly of ``POST /api/game/answer``.

The router owns the **order** of the gates (that order is the security model, and
it belongs next to the HTTP statuses it maps to). This module owns the two things
that are arithmetic rather than transport:

* :func:`economy_seam` — the **synchronous** callable US-28's transaction expects
  (``EconomySeam``), composing the three pure layers of Sprint 4:
  ``xp_engine`` (award / penalty), ``level_service`` (the OQ-4 clamp and the level
  curve) and nothing else. It is a closure over an already-validated
  ``(mode, bit_width)`` because ``mode``/``bit_width`` are wire fields and
  ``StreakContext`` deliberately does not carry them (US-28's docstring); binding
  them here is what keeps ``game_service`` independent of the request schema.
* :func:`answer_response` — the frozen 200 body of US-26's ``AnswerResponse``,
  including the ``newly_unlocked`` delta, which is computed from
  ``(previous_level, level)`` and from nothing else (US-31's
  ``newly_unlocked``), so an answer can neither repeat an old unlock nor invent one.

The gate predicate is here too (:func:`unlock_gate_detail`), as pure arithmetic:
"locked at the pre-answer level" is a question about the D-2 seed, not about HTTP,
so it is testable without a response object — and the 403 detail string is defined
once, which is what lets ``tests/test_unlock_service.py``'s rule and this endpoint's
message never drift.

:Danger: this module must stay free of ``await`` in the seam. An ``async`` seam
could commit inside the answer transaction and release the ``FOR UPDATE`` row lock
early (US-28 AC-2), which is the exact trap :func:`game_service.record_answer`
now rejects twice — on the callable (``iscoroutinefunction``) and on its return
value (awaitable, finding M-1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import User
from src.models.game_schemas import AnswerResponse, UnlockEntry
from src.services.game_config import GameMode, UnlockRule
from src.services.game_service import (
    AnswerOutcome,
    EconomyDecision,
    EconomySeam,
    StreakContext,
)
from src.services.level_service import clamped_total_xp, level_from_xp, xp_progress
from src.services.unlock_service import is_unlocked, newly_unlocked, required_level_for
from src.services.xp_engine import xp_for_correct, xp_penalty_for_incorrect

__all__ = [
    "answer_response",
    "economy_seam",
    "rate_limit_key",
    "unlock_gate_detail",
    "user_exists",
]

# The rate-limit key namespace of the answer path. Scoped per authenticated
# identity (never per IP, never per body): the limiter exists to bound what one
# account can farm, and a body-supplied identifier would make it bypassable —
# which is the same reason ``AnswerRequest`` has no ``user_id`` field (N8).
RATE_LIMIT_KEY_PREFIX: Final[str] = "answer"


def rate_limit_key(user_id: str) -> str:
    """The limiter key for one caller's answers (US-32, Rev 3 R-2).

    Args:
        user_id: the identity from the bearer token, never from the body.

    Returns:
        The key as stored in ``rate_limit_attempt.key``.
    """
    return f"{RATE_LIMIT_KEY_PREFIX}:{user_id}"


def economy_seam(mode: GameMode, bit_width: int) -> EconomySeam:
    """Compose US-29/US-30 into the sync seam ``record_answer`` requires.

    Args:
        mode: the validated play mode (closed ``Literal``, US-26).
        bit_width: the validated width **in bits** for that mode (OQ-7, D-7).

    Returns:
        A **plain ``def``** callable of ``StreakContext`` to ``EconomyDecision``.

    The sign convention is D-8/R-9's: ``xp_delta`` is the signed, **post-clamp**
    ``total_xp_new - total_xp_old``. On a correct answer that is the award itself,
    looked up on the POST-increment streak (D-3/OQ-5 — ``ctx.current_streak``
    already includes this answer). On an incorrect answer ``xp_penalty_for_incorrect``
    returns the positive magnitude (D-5: half the tier-x1.0 gain, never
    streak-scaled), :func:`clamped_total_xp` applies the OQ-4 floor at
    ``cum(level)``, and the delta is the difference — so it is negative in the
    normal case (LV-09a/c) and exactly ``0`` when the floor absorbs the penalty
    (LV-09b). The level is recomputed from the new total by :func:`level_from_xp`
    (D-1), never carried over, which is what makes LV-08 ("demotion impossible")
    a property of the arithmetic instead of a column.

    Integer only: no ``float``, no rounding, no snapping (D-8). Both branches keep
    the total in 4z for a reachable total, and the invariant
    ``total_xp == before.total_xp + xp_delta`` that ``record_answer`` validates
    holds by construction in both.

    Accepted risk (Wave 2a security review T-01/T-02; Sprint 4 PO decision
    2026-10-27, ``docs/sprint4-plan.md`` out-of-scope list and risk table):
    ``ctx.is_correct`` is a **client-asserted verdict with no server-side oracle**.
    This seam — and therefore the whole endpoint — computes faithfully from a
    premise the caller chose, so nothing here can tell a real answer from a scripted
    one. The 120-answers-per-60-seconds limit upstream is a **DoS control, not an XP
    ceiling**: it bounds how fast one identity can farm, not how much it can farm in
    total. The hardening that would close this (a server-issued challenge nonce, or
    HMAC-signed answers) is parked for US-40 to file in ``docs/backlog.md``, together
    with the answer-history event table that would make a farming investigation
    possible at all. ``tests/test_answer_endpoint.py::
    test_t01_is_correct_is_client_asserted_with_no_oracle`` pins the behaviour so the
    deferral stays a decision and never becomes a surprise.
    """

    def _score(ctx: StreakContext) -> EconomyDecision:
        before = ctx.before.total_xp
        if ctx.is_correct:
            award = xp_for_correct(mode, bit_width, ctx.current_streak)
            new_total = before + award
            xp_delta = award
        else:
            penalty = xp_penalty_for_incorrect(mode, bit_width)
            new_total = clamped_total_xp(before, penalty)
            xp_delta = new_total - before
        return EconomyDecision(
            xp_delta=xp_delta,
            total_xp=new_total,
            level=level_from_xp(new_total),
        )

    return _score


def _entries(rules: Sequence[UnlockRule]) -> list[UnlockEntry]:
    """Project US-31's frozen ``UnlockRule`` rows onto the wire shape (R-9).

    The element shape ``{mode, bit_width, min_level}`` is US-26's ``UnlockEntry``,
    shared with US-35's ``/profile`` so the client parses one type in two places.
    The rows themselves are the registry's immutable objects, so nothing here can
    write back into the D-2 seed.
    """
    return [
        UnlockEntry(mode=rule.mode, bit_width=rule.bit_width, min_level=rule.min_level)
        for rule in rules
    ]


def answer_response(outcome: AnswerOutcome) -> AnswerResponse:
    """Build the frozen 200 body from one persisted answer.

    Args:
        outcome: what :func:`src.services.game_service.record_answer` committed.
            Read-only: nothing here writes, so a response-building bug can never
            corrupt the economy it is reporting on.

    Returns:
        The ten-field ``AnswerResponse``. ``xp_in_level`` and
        ``xp_required_for_next_level`` (OQ-8: the **span**, not a cumulative
        threshold) come from a single call to :func:`xp_progress` on the new total,
        so they cannot disagree with ``level``; ``newly_unlocked`` comes from
        ``(previous_level, level)`` only (US-31).
    """
    xp_in_level, xp_required_for_next_level = xp_progress(outcome.total_xp)
    return AnswerResponse(
        xp_delta=outcome.xp_delta,
        total_xp=outcome.total_xp,
        level=outcome.level,
        xp_in_level=xp_in_level,
        xp_required_for_next_level=xp_required_for_next_level,
        current_streak=outcome.current_streak,
        best_streak=outcome.best_streak,
        correct_count=outcome.correct_count,
        incorrect_count=outcome.incorrect_count,
        newly_unlocked=_entries(newly_unlocked(outcome.previous_level, outcome.level)),
    )


def unlock_gate_detail(
    mode: GameMode, bit_width: int, pre_answer_level: int
) -> str | None:
    """The 403 detail for a locked combo, or ``None`` when the caller may answer.

    Args:
        mode: validated play mode.
        bit_width: validated width (bits, OQ-7).
        pre_answer_level: the level read under ``FOR UPDATE`` **before** this
            answer was applied (finding F-2). Passing the post-answer level here
            would let an answer unlock itself, which is the exact ordering bug this
            gate exists to prevent.

    Returns:
        ``None`` if the combo is open at ``pre_answer_level``, else the detail
        string naming the required level ("Content locked until level X", §4.9
        UN-05). The router turns that into the 403; this module never raises an
        ``HTTPException``, so the rule stays unit-testable without a response.

    Raises:
        RuntimeError: if the combo is not in the D-2 seed at all. Such a request is
            a 422 upstream (US-32's per-mode whitelist), so arriving here means the
            gate order was violated — a programming error that must not be dressed
            up as a client-visible 403 with a made-up level.
    """
    if is_unlocked(mode, bit_width, pre_answer_level):
        return None
    try:
        required = required_level_for(mode, bit_width)
    except ValueError as exc:  # §4.9 UN-06: no seed row for this combo
        raise RuntimeError(
            f"unlock gate reached with an unvalidated combo ({mode!r}, {bit_width}); "
            "US-32's body validation must run before the gate"
        ) from exc
    return f"Content locked until level {required}."


async def user_exists(session: AsyncSession, user_id: str) -> bool:
    """Whether the bearer's subject still has a ``users`` row (US-33's 404).

    A token outlives its user (logout elsewhere, account deletion), and
    ``record_answer`` creates a profile lazily — which the foreign key would reject
    as a 500. Asking here, after the rate limiter and before the row lock, turns
    that into a 404 with no write. The query selects only the primary key, so no
    ``User`` lands in the identity map of the transaction that is about to lock a
    different table.

    Args:
        session: the answer transaction's own session (this read is part of that
            transaction, which is why it takes a session instead of opening one).
        user_id: the identity from the bearer token.

    Returns:
        ``True`` when the user exists.
    """
    result = await session.execute(select(User.id).where(User.id == user_id))
    return result.first() is not None
