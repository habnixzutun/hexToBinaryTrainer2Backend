"""Sprint 4 / US-31: the pure unlock ladder.

Four public callables over the frozen ``UNLOCK_SEED`` of US-25, no state, no
I/O:

* :func:`unlocks_for_level` — §4.9 UN-02, the full set of combos open at a
  level: the lock-list view US-35 ships to the client.
* :func:`newly_unlocked` — §4.9 UN-03, the delta between two levels: the
  payload US-33 announces on a level-up. A **total** function on the range
  axis: an equal or inverted range answers ``()`` instead of raising, so the
  router needs no guard clause.
* :func:`is_unlocked` — §4.9 UN-05/UN-06, the 403 predicate. A **total**
  predicate on the combo axis: an invalid ``(mode, bit_width)`` — a width the
  mode does not speak (D-7, e.g. ``("unicode", 4)``) or an unregistered mode —
  answers ``False`` rather than raising, because a client bug must never be
  scored or silently accepted.
* :func:`required_level_for` — §4.9 UN-05, the level the 403 detail names
  ("Content locked until level X"). The **partial** counterpart of the
  predicate: a combo that does not exist raises ``ValueError``, so US-32
  renders that case as a 422 upstream and never as a 403 with a made-up level.

Unlocks are a pure function of the level (D-2): no unlock-state table, no
sticky column, no per-user unlock object. Because the XP floor is
``cum(level)`` (D-1/D-5), the level can never drop, so an unlock can never be
revoked (§4.9 UN-07 via LV-08) — the guarantee is arithmetic, not stored
state.

Every rule this module answers with is a registry row: the required level per
combo is **computed from ``UNLOCK_SEED`` at import time**, never re-declared,
so the seed stays the single source of truth (D-2) and the golden tests of
§4.9 pin both sides against the plan table independently.

D-8 arithmetic: levels and ``min_level`` are plain ints compared with ``<=``;
no division, no decimal and no rounding seam exists in this module at all.

Purity, enforced statically by XP-TP9: stdlib plus ``src.services.game_config``
only — no database, no ORM, no FastAPI, no pydantic, no settings, no relative
imports, no I/O at import time.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from src.services.game_config import UNLOCK_SEED, GameMode, UnlockRule

__all__ = [
    "is_unlocked",
    "newly_unlocked",
    "required_level_for",
    "unlocks_for_level",
]

# --------------------------------------------------------------------------
# Ladder derived from the frozen seed (D-2)
# --------------------------------------------------------------------------

# D-1 — "Player starts at level 1 with 0 XP": every level-taking entry point
# below rejects a level under 1. Answering with the L1 set instead would
# silently unlock content for what can only be a caller bug.
_FIRST_LEVEL: Final[int] = 1

# D-2 — the ladder as computed from ``UNLOCK_SEED`` at import time (never
# hand-copied, so the registry stays the single source of truth): the required
# level per valid ``(mode, bit_width)`` combo. A combo absent from this mapping
# does not exist at all (§4.9 UN-06 / D-7 — e.g. ``("unicode", 4)`` has no seed
# row on purpose). Read-only, like every table in the pure layer.
_REQUIRED_LEVEL_BY_COMBO: Final[Mapping[tuple[GameMode, int], int]] = MappingProxyType(
    {(rule.mode, rule.bit_width): rule.min_level for rule in UNLOCK_SEED}
)


def _validate_level(level: int) -> None:
    """D-1 domain rule shared by every level-taking entry point.

    Raises:
        ValueError: if ``level`` < 1 — levels start at 1, and a level below is
            a caller bug, never a client-input case.
    """
    if level < _FIRST_LEVEL:
        raise ValueError(
            f"level must be >= {_FIRST_LEVEL} (D-1: levels start at 1), got {level}"
        )


# --------------------------------------------------------------------------
# Public API (US-31)
# --------------------------------------------------------------------------


def unlocks_for_level(level: int) -> tuple[UnlockRule, ...]:
    """§4.9 UN-02 — every combo open at ``level``, as frozen registry rows.

    The seed filter ``min_level <= level`` (D-2): L1 opens exactly the four
    slot-1 rows (unicode's slot 1 is 8 BITS, not 4 — D-7), L13 opens all
    sixteen, and above L13 nothing new opens at all: the caps (13 conversions,
    4 unicode) are PO-binding (UN-01) and past them XP climbs only, for
    prestige and leaderboard purposes (US-36).

    The filter also makes the set monotone (UN-04): ``unlocks(L)`` is a subset
    of ``unlocks(L + 1)`` for every L, so no content can ever close again —
    which is what lets D-2 drop the unlock-state table.

    Args:
        level: the player's level, >= 1 (D-1).

    Returns:
        A tuple of the registry's frozen :class:`UnlockRule` rows in seed
        order — the same immutable objects the seed holds, so no caller can
        mutate shared economy state.

    Raises:
        ValueError: if ``level`` < 1 (D-1; see :func:`_validate_level`).
    """
    _validate_level(level)
    return tuple(rule for rule in UNLOCK_SEED if rule.min_level <= level)


def newly_unlocked(level_before: int, level_after: int) -> tuple[UnlockRule, ...]:
    """§4.9 UN-03 — the rules that opened between two levels (US-33's payload).

    Exactly ``unlocks_for_level(level_after)`` minus
    ``unlocks_for_level(level_before)``, in seed order: no already-open rule
    and no revoked rule. **Total** on the range axis: a demotion cannot occur
    (LV-08), so an equal or inverted range is not a revoke list — it answers
    ``()`` — and US-33 can call the function on every answer, level-up or not,
    without a guard clause.

    Args:
        level_before: the level before the answer transaction, >= 1 (D-1).
        level_after: the level after the answer transaction, >= 1 (D-1).

    Returns:
        The delta as a tuple of frozen ``UnlockRule`` rows; ``()`` when the
        level did not rise or rose without crossing a ``min_level`` (the
        common level-up case, e.g. L5 -> L7, whose empty toast US-38 must
        survive).

    Raises:
        ValueError: if either level is below 1 (D-1; see
            :func:`_validate_level`).
    """
    _validate_level(level_before)
    _validate_level(level_after)
    if level_after <= level_before:
        return ()
    return tuple(
        rule for rule in UNLOCK_SEED if level_before < rule.min_level <= level_after
    )


def is_unlocked(mode: GameMode, bit_width: int, level: int) -> bool:
    """§4.9 UN-05/UN-06 — whether a combo is open at ``level`` (the 403 seam).

    Locked strictly below the combo's ``min_level``, open at and above it, and
    exactly membership in :func:`unlocks_for_level` — the 403 path and the
    lock-list path cannot disagree. **Total** on the combo axis: an invalid
    ``(mode, bit_width)`` answers ``False`` rather than raising (UN-06), so a
    client bug is never scored and never silently accepted; US-32 rejects such
    a combo with a 422 before a locked 403 could even be considered.

    Args:
        mode: a game mode; an unregistered one makes the combo invalid.
        bit_width: bits in every mode (D-7) — ``("unicode", 4)`` is not a
            combo at all and answers ``False`` at every level.
        level: the player's level, >= 1 (D-1).

    Returns:
        A plain ``bool`` — ``True`` only for a valid combo whose ``min_level``
        is <= ``level`` (a truthy int would break the router's 403 branch).

    Raises:
        ValueError: if ``level`` < 1 (D-1 domain rule — a caller bug, not a
            client-input case; the combo axis stays total).
    """
    _validate_level(level)
    required = _REQUIRED_LEVEL_BY_COMBO.get((mode, bit_width))
    if required is None:
        return False  # UN-06: an invalid combo is never unlocked, never raises
    return level >= required


def required_level_for(mode: GameMode, bit_width: int) -> int:
    """§4.9 UN-05 — the level the 403 detail names ("locked until level X").

    The seed's ``min_level`` for the combo (D-2), read from the import-time
    mapping so the 403 payload and the ladder can never drift. **Partial**:
    a combo that does not exist raises, so US-32 renders that case as a 422
    and it can never become a 403 with a made-up required level (UN-06).

    Args:
        mode: one of :data:`~src.services.game_config.MODES`.
        bit_width: bits, per-mode valid set (D-7).

    Returns:
        The required level as a plain ``int`` — 1/4/8/13 for the three
        conversion modes, 1/2/3/4 for unicode (the D-2 ladder).

    Raises:
        ValueError: if ``(mode, bit_width)`` has no seed row — an
            unregistered mode or a width the mode does not speak (D-7),
            e.g. ``("unicode", 4)``.
    """
    try:
        return _REQUIRED_LEVEL_BY_COMBO[(mode, bit_width)]
    except KeyError:
        raise ValueError(
            f"no unlock rule for ({mode!r}, {bit_width}): not a valid combo "
            f"(§4.9 UN-06 / D-7)"
        ) from None
