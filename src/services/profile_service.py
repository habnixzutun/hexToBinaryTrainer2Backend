"""US-35 / US-37: the economy's read side — one stored row, two views of it.

Both authenticated reads of the Sprint-4 economy are served from this module, so
they cannot tell two different stories about the same row:

* ``GET /api/game/profile`` (US-35) returns the whole state plus the D-2 unlock
  ladder — :func:`profile_response`.
* ``GET /api/user/me`` (US-37) returns identity fields plus the four economy
  numbers — it reads the same :class:`EconomySnapshot`, which is what makes the
  "agree field-for-field" AC of US-37 a property of the code rather than a
  coincidence two handlers happen to preserve.

Two decisions live here and are worth stating, because both are invisible in the
signatures:

**The level is derived, never read.** The D-1 curve makes ``level`` a *function* of
``total_xp``, so every caller recomputes it with
:func:`~src.services.level_service.level_from_xp`, and every number that hangs off it
— ``xp_in_level``, ``xp_required_for_next_level`` (OQ-8: the **span**, not a
cumulative threshold) and the ``unlocks`` filter — comes from that one function call.
The stored ``user_game_profile.level`` column is a **denormalization** the answer
transaction still writes (US-30) and nothing on any report path reads: level is derived
from ``total_xp`` everywhere, so ``/answer``, ``/profile``, ``/me`` and the leaderboard
**by construction** tell the same story — even if the stored column diverges (manual
SQL, a US-41 fixture, a future writer).
``tests/test_game.py::test_a_divergent_stored_level_is_reported_as_derived`` pins it.

**A read never writes.** The profile row is created lazily by the first
``POST /api/game/answer`` (US-28 AC-3). A missing row therefore answers the zero
state of D-1/OQ-3 — level 1 at 0 XP, the full four-entry L1 ladder — and inserts
nothing. A GET that created the row would put a write, and a row lock, on the
hottest read path and break that contract.

D-8 arithmetic: nothing here computes XP. Every figure is either a stored
``int`` column or a call into the pure layers (US-30/US-31), so no float can enter
through this module and no rounding seam exists on the read path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.game import UserGameProfile
from src.models.game_schemas import ProfileResponse, UnlockEntry
from src.services.game_config import UnlockRule
from src.services.level_service import level_from_xp, xp_progress
from src.services.unlock_service import unlocks_for_level

__all__ = [
    "EconomySnapshot",
    "load_snapshot",
    "profile_response",
    "unlock_entries",
]


@dataclass(frozen=True, slots=True)
class EconomySnapshot:
    """The five stored economy columns of one profile row, as an immutable copy.

    ``level`` is deliberately NOT a field: it is :func:`level_from_xp` of
    ``total_xp`` (module docstring), exposed as a property so no caller can pass a
    stored level into a response and so the two views of a player can never carry
    different levels for one total.
    """

    total_xp: int
    current_streak: int
    best_streak: int
    correct_count: int
    incorrect_count: int

    @property
    def level(self) -> int:
        """D-1: the level a total maps to — never below 1, never stale."""
        return level_from_xp(self.total_xp)


# D-1 / OQ-3 — "a player starts at level 1 with 0 XP". The answer to "there is no
# row yet" is this snapshot, not an error and not an insert: level_from_xp(0) == 1
# and unlocks_for_level(1) is the four-entry L1 ladder, so the zero state falls out
# of the same arithmetic every other player is served by.
_ZERO_STATE: Final[EconomySnapshot] = EconomySnapshot(
    total_xp=0,
    current_streak=0,
    best_streak=0,
    correct_count=0,
    incorrect_count=0,
)


async def load_snapshot(session: AsyncSession, user_id: str) -> EconomySnapshot:
    """Read one player's economy row — a plain ``SELECT``, no lock and no write.

    Args:
        session: the request's own session (``get_db``); this read is part of that
            transaction and commits nothing.
        user_id: the authenticated identity from the bearer token, never from the
            query or the body (US-33 N8).

    Returns:
        The stored state, or :data:`_ZERO_STATE` when the user has not answered yet
        (US-35: "missing profile -> zeros + level 1 + full unlocks for L1").

    Raises:
        ValueError: empty ``user_id`` — the same non-empty-identity rule
            :func:`src.services.game_service.record_answer` enforces on the write
            side, so a missing identity never reaches the database here either.
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")

    result = await session.execute(
        select(UserGameProfile).where(UserGameProfile.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return _ZERO_STATE

    return EconomySnapshot(
        total_xp=row.total_xp,
        current_streak=row.current_streak,
        best_streak=row.best_streak,
        correct_count=row.correct_count,
        incorrect_count=row.incorrect_count,
    )


def unlock_entries(rules: tuple[UnlockRule, ...]) -> list[UnlockEntry]:
    """Project US-31's frozen registry rows onto the wire shape (R-9).

    The element type is US-26's :class:`UnlockEntry` — the SAME model behind
    ``AnswerResponse.newly_unlocked`` (US-33) and ``ProfileResponse.unlocks``
    (US-35), which is what Rev 3 R-9 pins so US-31's producer and US-38's toast
    parse one shape. Seed order is preserved, so a client renders the ladder
    without re-sorting it.
    """
    return [
        UnlockEntry(mode=rule.mode, bit_width=rule.bit_width, min_level=rule.min_level)
        for rule in rules
    ]


def profile_response(snapshot: EconomySnapshot) -> ProfileResponse:
    """Build the US-35 200 body from one snapshot.

    Args:
        snapshot: what :func:`load_snapshot` read. Read-only — nothing here writes,
            so a response-building bug can never corrupt the economy it reports on.

    Returns:
        The nine-field ``ProfileResponse``. The progress pair comes from a single
        :func:`~src.services.level_service.xp_progress` call on the same total the
        level is derived from, so ``level``, ``xp_in_level`` and
        ``xp_required_for_next_level`` agree by construction, and ``unlocks`` is
        the FULL ladder for that level (§4.9 UN-02).
    """
    level = snapshot.level
    xp_in_level, xp_required_for_next_level = xp_progress(snapshot.total_xp)

    return ProfileResponse(
        total_xp=snapshot.total_xp,
        level=level,
        xp_in_level=xp_in_level,
        xp_required_for_next_level=xp_required_for_next_level,
        current_streak=snapshot.current_streak,
        best_streak=snapshot.best_streak,
        correct_count=snapshot.correct_count,
        incorrect_count=snapshot.incorrect_count,
        unlocks=unlock_entries(unlocks_for_level(level)),
    )
