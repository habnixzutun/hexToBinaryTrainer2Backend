"""Sprint 4 / US-30: pure level progression.

Four public callables over the D-1 curve, no state, no I/O:

* :func:`cumulative_xp_for_level` — D-1 row 2, ``cum(level)``: the XP needed to
  *reach* a level. ``cum(1) == 0`` (a player starts at level 1 with 0 XP).
* :func:`level_from_xp` — §4.8 LV-05/LV-07, the highest ``L`` with
  ``cum(L) <= total_xp``; the boundary is inclusive at the top.
* :func:`xp_progress` — §4.8 LV-10, the ``(xp_in_level,
  xp_required_for_next_level)`` pair US-33 puts on the wire and US-38 renders.
* :func:`clamped_total_xp` — the D-1/D-5 floor seam of Rev 4:
  ``max(cum(level), total_xp - penalty)``.

The clamp lives here as a pure function instead of inline in the answer
transaction, so LV-08/LV-09/LV-10 and UN-07 are testable without a database and
US-33 has exactly one place to call. It is the *only* mechanism that makes
demotion impossible: **no** ``max_level_reached`` column, no sticky state —
because the floor is ``cum(level)`` and ``cum`` is non-decreasing, the level a
total maps to can never fall (LV-08, UN-07).

D-8 arithmetic: every span of the D-1 curve is a multiple of 4, so every
``cum(level)`` is too, and the clamp keeps both the new total and ``xp_delta`` in
4z for a reachable total. The clamp itself applies the plan formula as plain
integer subtraction and ``max`` — no rounding and no snapping to the grid, so a
legacy total outside 4z loses exactly its penalty instead of being silently
moved (LV-09 boundary case: ``clamped_total_xp(99, 64) == 35``).

Levels are **unlimited** (D-1): the L13 / L4 caps of D-2 bind unlocks only, and
:func:`level_from_xp` keeps climbing past them for prestige and leaderboard
purposes (US-36).

Purity, enforced statically by XP-TP9: stdlib plus the pure layer
(:mod:`src.services.xp_engine`, which owns the D-1 curve) only — no database, no
ORM, no FastAPI, no pydantic, no settings, no relative imports.
"""

from __future__ import annotations

from typing import Final

from src.services.xp_engine import xp_required_for_level

__all__ = [
    "clamped_total_xp",
    "cumulative_xp_for_level",
    "level_from_xp",
    "xp_progress",
]

# D-1 — "Player starts at level 1 with 0 XP": levels start at 1, and
# ``cum(1) == 0`` is the floor of a fresh profile (LV-09 d).
_FIRST_LEVEL: Final[int] = 1
_FIRST_LEVEL_CUM: Final[int] = 0  # D-1 row 2: cum(1)


def cumulative_xp_for_level(level: int) -> int:
    """D-1 row 2 / LV-01 — ``cum(level)``, the XP needed to **reach** ``level``.

    ``cum(1) == 0`` and ``cum(L) == sum(req(1..L-1))`` over the US-29 spans, so
    the two rows of the D-1 table are one arithmetic object and cannot drift
    (LV-01). The plan's pacing figures fall straight out of this: ``cum(4) == 476``
    (8-bit unlock), ``cum(8) == 3220`` (16-bit) and ``cum(13) == 25752`` (32-bit),
    all multiples of 4 (D-8).

    Args:
        level: the level whose cumulative threshold is wanted, >= 1.

    Returns:
        The cumulative XP as a plain ``int`` — 0, 100, 252, 476, 812, ... for
        levels 1..5.

    Raises:
        ValueError: if ``level`` < 1. ``cum`` is undefined below level 1, and
            returning the level-1 value instead would hand a caller a bogus XP
            floor for a level that does not exist.
    """
    if level < _FIRST_LEVEL:
        raise ValueError(
            f"level must be >= {_FIRST_LEVEL} (D-1: levels start at 1), got {level}"
        )
    return sum(xp_required_for_level(span) for span in range(_FIRST_LEVEL, level))


def _level_and_floor(total_xp: int) -> tuple[int, int]:
    """One walk of the D-1 curve: ``(level, cum(level))`` for a given total.

    Shared by :func:`level_from_xp`, :func:`xp_progress` and
    :func:`clamped_total_xp` so the three views of a total can never disagree.
    The spans are accumulated incrementally, which costs a single pass over the
    curve instead of a fresh sum per candidate level.

    Args:
        total_xp: the player's XP total.

    Returns:
        The level the total sits in (LV-05: the highest ``L`` with
        ``cum(L) <= total_xp``) and that level's ``cum`` floor.
    """
    level = _FIRST_LEVEL
    level_floor = _FIRST_LEVEL_CUM  # cum(1) == 0 — D-1
    while True:
        next_floor = level_floor + xp_required_for_level(level)  # cum(level + 1)
        if next_floor > total_xp:
            return level, level_floor
        level_floor = next_floor
        level += 1


def level_from_xp(total_xp: int) -> int:
    """§4.8 LV-05 / LV-07 — the level a total maps to.

    The highest ``L`` with ``cum(L) <= total_xp``: exactly at a threshold the
    player already **is** the new level (inclusive upward, LV-07), and the last
    reachable XP of a level is ``cum(L+1) - 4``. Levels are unlimited (D-1).

    Args:
        total_xp: the player's XP total.

    Returns:
        The level as a plain ``int`` >= 1 — ``0 -> 1``, ``99 -> 1``,
        ``100 -> 2``, ``252 -> 3``, ``476 -> 4``, ``25752 -> 13`` (§4.8 LV-05).
    """
    level, _unused_floor = _level_and_floor(total_xp)
    return level


def xp_progress(total_xp: int) -> tuple[int, int]:
    """§4.8 LV-10 / US-30 — ``(xp_in_level, xp_required_for_next_level)``.

    The wire pair of US-33 (field name frozen in Rev 4, D-1: the second element
    is the **span** to the next level, not a cumulative threshold) and the value
    US-38 renders as a progress bar. ``xp_in_level`` is never negative — not at a
    floor, not directly after a clamped penalty (LV-10, which follows from the
    ``cum(level)`` floor of :func:`clamped_total_xp`).

    Args:
        total_xp: the player's XP total.

    Returns:
        A two-element tuple of plain ``int``\\ s: ``total_xp - cum(level)`` and
        ``req(level)``. ``(0) -> (0, 100)``, ``(100) -> (0, 152)``,
        ``(251) -> (151, 152)``.
    """
    level, level_floor = _level_and_floor(total_xp)
    return (total_xp - level_floor, xp_required_for_level(level))


def clamped_total_xp(total_xp: int, penalty: int) -> int:
    """D-1 / D-5 (Rev 4) — the XP floor clamp: ``max(cum(level), total_xp - penalty)``.

    The single-sourced implementation of the "kein Level-Verlust" decision. The
    four LV-09 cases it has to satisfy:

    * (a) comfortable margin — the whole penalty is taken
      (``clamped_total_xp(1312, 64) == 1248``);
    * (b) exactly on ``cum(level)`` — zero delta, the player is momentarily
      immune (``clamped_total_xp(812, 512) == 812``);
    * (c) just above the floor — the penalty is truncated to the margin
      (``clamped_total_xp(816, 64) == 812``, applied delta ``-4`` not ``-64``);
    * (d) level 1 — ``cum(1) == 0``, so a fresh profile floors at 0 exactly as
      the legacy rule did.

    LV-08 (demotion impossible) is the consequence: the returned total still maps
    to the input's level for every §4.7 penalty.

    Args:
        total_xp: the player's XP total **before** the penalty.
        penalty: the positive magnitude from
            :func:`src.services.xp_engine.xp_penalty_for_incorrect` (D-8 sign
            convention — this function subtracts, the caller never negates).

    Returns:
        The new total as a plain ``int``. The **applied** delta the router reports
        is ``result - total_xp`` (D-5: it may be smaller in magnitude than
        ``penalty``), and it is a multiple of 4 for every reachable total (D-8).
    """
    _level, level_floor = _level_and_floor(total_xp)
    return max(total_xp - penalty, level_floor)
