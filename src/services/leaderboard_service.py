"""US-36: the public leaderboard over ``user_game_profile`` (v2).

The rewrite of the Sprint-2 board. Three things changed and one deliberately did
not:

* **Source** — ``user_game_profile``, not the legacy counters table. XP is the
  economy's currency now (D-1); the legacy score field is gone from the entry
  shape, with no deprecated alias (Rev 3: an alias would have to be removed
  again in US-34).
* **Ordering** — ``total_xp DESC``, then ``incorrect_count ASC``, then
  ``user_id ASC``. The first tiebreak rewards the cleaner record, and the third
  makes the whole thing a TOTAL order: ``user_id`` is the primary key, so no two
  rows can tie and a client polling every 30 s never sees the same board reshuffle
  between two requests (the "stable ordering" of US-36).
* **Bound** — ``limit`` is validated 1..100 with a default of 50, closing the
  unbounded-query smell BE-6 logged against the legacy handler.

* **Visibility did NOT change: this endpoint stays PUBLIC, with no authentication
  at all** (PO decision 2026-10-27, Rev 3 R-3 / OQ-9). Rev 2 had made it
  auth-required "for parity with the other game endpoints"; that would have broken
  a documented public contract (``README`` "Public — no authentication required")
  and left the frontend's un-try/caught 30-second poll spinning forever. The
  rationale for public-ness is the entry shape: ``username`` plus game numbers, no
  email, no PII, nothing user-controlled — a showcase, not a data leak. The abuse
  control here is the bounded ``limit``, not auth, so the bound is enforced twice:
  at the boundary (the router's ``Query`` constraints, giving a 422) and behind it
  in this service, so a future non-HTTP caller cannot ask for the whole table.

The level on each row is derived from ``total_xp`` by the D-1 curve, exactly as
:mod:`~src.services.profile_service` derives it for the row's owner and as
:mod:`~src.services.game_service` derives it on the write side (Phase 2b H1) — so the
board, a player's own ``/profile``, ``/me`` and their next ``/answer`` response can
never disagree about what level 1320 XP is, not even for a row whose stored ``level``
column says something else.

Users without a profile row are absent from the board by design (an ``INNER JOIN``
on ``users``): the lazy row of US-28 AC-3 is created by the first answer, and
"has not played yet" is not a rank. This service reads the economy table only —
which is what made the D-6 co-existence window safe, and what leaves nothing to
migrate now that US-34 has dropped the legacy one.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import User
from src.models.game import UserGameProfile
from src.models.game_schemas import LeaderboardEntryV2
from src.services.level_service import level_from_xp

__all__ = [
    "LEADERBOARD_DEFAULT_LIMIT",
    "LEADERBOARD_MAX_LIMIT",
    "LEADERBOARD_MIN_LIMIT",
    "leaderboard_entries",
]

# US-36: the bound that stands in for authentication as this endpoint's abuse
# control (R-3/OQ-9). One source of truth, referenced by the router's ``Query``
# constraints and re-checked here, so the two cannot drift.
LEADERBOARD_MIN_LIMIT: Final[int] = 1
LEADERBOARD_MAX_LIMIT: Final[int] = 100
LEADERBOARD_DEFAULT_LIMIT: Final[int] = 50


async def leaderboard_entries(
    session: AsyncSession,
    limit: int = LEADERBOARD_DEFAULT_LIMIT,
) -> list[LeaderboardEntryV2]:
    """The top ``limit`` players by XP, in the board's total order.

    Args:
        session: the request's own session (``get_db``); a plain read, no lock.
        limit: how many entries to return, 1..100. Rejected outside that range
            rather than clamped: a caller that asked for 101 wants to know it
            asked wrong, and a silent clamp would make the bound invisible.

    Returns:
        One :class:`LeaderboardEntryV2` per ranked player, in order, with no
        legacy score field and no PII field (US-36 AC-4). An empty list when
        nobody has answered yet — the wrapper is the router's, and it stays
        ``{entries: []}``.

    Raises:
        ValueError: ``limit`` outside 1..100 (the service-side half of the bound).
    """
    if not LEADERBOARD_MIN_LIMIT <= limit <= LEADERBOARD_MAX_LIMIT:
        raise ValueError(
            f"limit must be between {LEADERBOARD_MIN_LIMIT} and "
            f"{LEADERBOARD_MAX_LIMIT}, got {limit}"
        )

    result = await session.execute(
        select(UserGameProfile, User.username)
        .join(User, User.id == UserGameProfile.user_id)
        .order_by(
            desc(UserGameProfile.total_xp),
            asc(UserGameProfile.incorrect_count),
            asc(UserGameProfile.user_id),
        )
        .limit(limit)
    )

    return [
        LeaderboardEntryV2(
            user_id=profile.user_id,
            username=username,
            total_xp=profile.total_xp,
            level=level_from_xp(profile.total_xp),
            correct_count=profile.correct_count,
            incorrect_count=profile.incorrect_count,
        )
        for profile, username in result.all()
    ]
