"""Sprint 4 / US-29: the exact-integer XP calculation engine (pure layer).

Four public callables, no state, no I/O:

* :func:`xp_for_correct` — §4.1 master formula ``base_xp * mode_mult *
  streak_mult``, evaluated on the **post-increment** streak (D-3, Rev 4), so the
  3rd consecutive correct answer already earns the 3-4 tier.
* :func:`xp_penalty_for_incorrect` — §4.7 ``base_xp * mode_mult * 1/2`` as a
  **positive magnitude** (D-8 sign convention: the router negates it into the
  wire ``xp_delta`` of US-33). Includes the mode multiplier, which makes the
  reward:risk ratio a uniform 2:1 in every mode and width (D-5), and never takes
  a streak argument (D-5: the tier never enters the penalty).
* :func:`streak_multiplier` — §4.5 / D-3 the tier curve as an exact
  ``Fraction``; the only non-integer return value in the engine.
* :func:`xp_required_for_level` — D-1 the level **span** ``n -> n+1`` (not a
  cumulative threshold), the curve US-30 sums into ``cum(level)``.

Every table the engine reads comes from ``src.services.game_config`` (US-25),
the single source of truth for §4.2 (base XP), §4.3 (mode multipliers), §4.5
(streak tiers), §4.7 (penalty factor) and D-1 (the curve constants). Nothing is
re-declared here, so the registry and the engine cannot drift; the golden tests
in ``tests/test_xp_engine.py`` assert both sides against the plan tables.

D-8 arithmetic: no decimal literal, no builtin half-to-even rounding helper, no
truncation. Awards and penalties are integer quotients of an exact rational pair
— every §4.4 core ``base_xp * mode_mult`` is a multiple of 16, so the tier
denominators (1, 2, 4) and the penalty denominator divide it without remainder
and **no rounding branch exists anywhere in the award or penalty path**. The one
place the plan mandates rounding is the D-1 level curve, and there the
round-half-**up** integer primitive ``(2 * num + den) // (2 * den)`` is used,
never the builtin (which is half-to-even and would return 148 for the raw 150 of
n=2 instead of the plan's 152 — the curve's only exact tie).

Purity, enforced statically by XP-TP9: stdlib plus ``src.services.game_config``
only — no database, no ORM, no FastAPI, no pydantic, no settings, no relative
imports, no I/O at import time.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

from src.services.game_config import (
    BASE_XP,
    MODE_MULTIPLIERS,
    MODES,
    PENALTY_FACTOR,
    VALID_WIDTHS_BY_MODE,
    GameMode,
    streak_tier_for,
)

__all__ = [
    "streak_multiplier",
    "xp_for_correct",
    "xp_penalty_for_incorrect",
    "xp_required_for_level",
]

# --------------------------------------------------------------------------
# D-1 level curve constants
# --------------------------------------------------------------------------

# D-1 — ``xp_required_for_level(n) = round_to_nearest_multiple_of_4(
# 100 * 1.5^(n-1))``. The 1.5 growth is carried as the exact rational pair
# 3/2 so the curve is computed with integers only (D-8).
_CURVE_BASE_XP: Final[int] = 100  # D-1: req(1), the level-1->2 span
_CURVE_GROWTH_NUMERATOR: Final[int] = 3  # D-1: 1.5 = 3/2
_CURVE_GROWTH_DENOMINATOR: Final[int] = 2  # D-1: 1.5 = 3/2

# D-8 — the multiples-of-4 grid every award, penalty and span lives on.
_XP_GRID: Final[int] = 4

# D-1 — "Player starts at level 1 with 0 XP", so the span is defined for
# n >= 1 only; anything below is a caller bug and raises (XP-G20 sibling).
_FIRST_LEVEL: Final[int] = 1


# --------------------------------------------------------------------------
# Internal exact-integer primitives
# --------------------------------------------------------------------------


def _effective_core(mode: GameMode, bit_width: int) -> int:
    """§4.4 — the streak-independent core ``base_xp * mode_mult``.

    This is the tier-x1.0 award and, halved (D-5), the penalty magnitude, so the
    two paths share one domain check instead of drifting apart.

    Args:
        mode: one of :data:`~src.services.game_config.MODES`.
        bit_width: a bit count **in bits for every mode** (D-7), so ``unicode``
            accepts 8/16/24/32 and rejects 4.

    Returns:
        The §4.4 core, always a multiple of 16 (D-3's exactness precondition).

    Raises:
        ValueError: if ``mode`` has no registry row (XP-G22) or ``bit_width`` is
            outside the mode's per-mode width set (XP-G21 / D-7). Defence in
            depth only — the user-facing path is already a 422 from US-32.
    """
    valid_widths = VALID_WIDTHS_BY_MODE.get(mode)
    if valid_widths is None:
        raise ValueError(f"unknown game mode {mode!r}; expected one of {MODES}")
    if bit_width not in valid_widths:
        raise ValueError(
            f"bit_width {bit_width} is not valid for mode {mode!r}; "
            f"valid widths in bits (D-7) are {valid_widths}"
        )
    return BASE_XP[mode][bit_width] * MODE_MULTIPLIERS[mode]


def _exact_quotient(value: int, numerator: int, denominator: int) -> int:
    """D-8 — ``value * numerator / denominator`` as an exact integer quotient.

    The divisor is always 1, 2 or 4 and every §4.4 ``value`` is a multiple of 16,
    so the division never has a remainder: the guard below is the D-8 runtime
    check of property XP-TP8, not a rounding path. It can only fire if the
    registry tables of US-25 ever change to violate the invariant, and then it
    must fail loudly instead of silently awarding a rounded-down amount.
    """
    product = value * numerator
    assert product % denominator == 0, (value, numerator, denominator)  # D-8 / XP-TP8
    return product // denominator


# --------------------------------------------------------------------------
# Public API (US-29)
# --------------------------------------------------------------------------


def streak_multiplier(streak: int) -> Fraction:
    """§4.5 / D-3 — the tier multiplier of a **post-increment** streak (D-3).

    Delegates the tier lookup to the registry, so the curve lives in exactly one
    place. The 11+ tier is a cap (x2.5), not a growing curve (XP-TP5).

    Args:
        streak: consecutive correct answers *including* the one being scored,
            so the first correct answer of a run passes 1 (D-3, Rev 4).

    Returns:
        The tier as an exact ``Fraction`` — one of 1, 5/4, 3/2, 2, 5/2.

    Raises:
        ValueError: if ``streak`` < 1 (XP-G20). An award always follows the
            increment, so 0 never occurs here and normalising it to x1.0 would
            hide the caller's bug.
    """
    return streak_tier_for(streak).multiplier


def xp_for_correct(mode: GameMode, bit_width: int, streak: int) -> int:
    """§4.1 / §4.6 — the award for a correct answer.

    ``base_xp * mode_mult * streak_mult``, computed as the integer quotient
    ``(core * tier.numerator) // tier.denominator`` (D-8), so the result is a
    plain ``int`` and an exact multiple of 4 (XP-TP1). The system maximum is the
    XP-G14 cell: ``bin_to_dec`` at 32 bits and streak 11+ = 1024 x 5/2 = 2560.

    Args:
        mode: one of :data:`~src.services.game_config.MODES`.
        bit_width: bits, per-mode valid set (D-7).
        streak: the post-increment streak, >= 1 (D-3).

    Returns:
        The awarded XP as a plain, positive ``int`` in 4z.

    Raises:
        ValueError: on an unknown mode, an invalid ``(mode, bit_width)`` pair, or
            a streak below 1 (XP-G20/21/22).
    """
    core = _effective_core(mode, bit_width)  # §4.4
    tier = streak_tier_for(streak)  # §4.5 / D-3
    return _exact_quotient(core, tier.numerator, tier.denominator)


def xp_penalty_for_incorrect(mode: GameMode, bit_width: int) -> int:
    """§4.7 / D-5 — the penalty magnitude for an incorrect answer.

    ``base_xp * mode_mult * PENALTY_FACTOR`` with the registry's exact 1/2
    factor, so the result is exactly half of the same combo's tier-x1.0 gain
    (XP-P08, the uniform 2:1 ratio) and therefore also a multiple of 4 (XP-TP7).
    There is deliberately **no** streak parameter: the tier never enters the
    penalty (D-5), and the signature is part of the US-29 contract.

    Args:
        mode: one of :data:`~src.services.game_config.MODES`.
        bit_width: bits, per-mode valid set (D-7). ``("unicode", 4)`` is not a
            combo at all and raises rather than reporting a locked value.

    Returns:
        The **positive** magnitude (D-8 sign convention). US-33 negates it into
        the wire ``xp_delta``; the applied delta may be smaller in magnitude when
        the D-1/D-5 floor of :func:`src.services.level_service.clamped_total_xp`
        truncates it (LV-09).

    Raises:
        ValueError: on an unknown mode or an invalid ``(mode, bit_width)`` pair —
            the same domain as :func:`xp_for_correct` (XP-G21/XP-G22).
    """
    core = _effective_core(mode, bit_width)  # §4.4
    return _exact_quotient(core, PENALTY_FACTOR.numerator, PENALTY_FACTOR.denominator)


def xp_required_for_level(level: int) -> int:
    """D-1 — the XP span ``level -> level + 1`` (not a cumulative threshold).

    ``round_to_nearest_multiple_of_4(100 * 1.5^(level-1))`` with exact integer
    arithmetic: the raw rational is ``100 * 3^(n-1) / (4 * 2^(n-1))`` in quarter
    units, and round-half-up on that quotient is the integer primitive
    ``(2 * num + den) // (2 * den)``. The builtin half-to-even helper is not used
    anywhere: it would inflate n=2 (raw 150) down to 148 instead of the plan's
    152, and diverge from the exact curve at n=74 (LV-06 companion).

    The curve is **unlimited** — no cap at 13 (D-1; the caps of D-2 bind unlocks
    only), and it is verified against an exact-``Fraction`` oracle up to n=100
    and beyond, past the n=84 float-drift threshold of D-1 / Rev 3 R-5.

    Args:
        level: the level whose span is wanted, >= 1.

    Returns:
        The span as a plain ``int`` in 4z: 100, 152, 224, 336, 508, ... (D-1
        row 1). US-30 sums these into ``cum(level)``.

    Raises:
        ValueError: if ``level`` < 1 (D-1 defines the span for n >= 1 only).
    """
    if level < _FIRST_LEVEL:
        raise ValueError(
            f"level must be >= {_FIRST_LEVEL} (D-1: the span n -> n+1 starts at "
            f"n=1), got {level}"
        )
    steps = level - _FIRST_LEVEL
    # The raw span divided by the D-8 grid, as an exact rational num/den.
    numerator = _CURVE_BASE_XP * _CURVE_GROWTH_NUMERATOR**steps
    denominator = _XP_GRID * _CURVE_GROWTH_DENOMINATOR**steps
    # D-1 tie rule: exact halves round UP (away from zero) — fires only at n=2.
    rounded_quarters = (2 * numerator + denominator) // (2 * denominator)
    return _XP_GRID * rounded_quarters
