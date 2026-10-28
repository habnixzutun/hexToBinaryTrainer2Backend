"""Sprint 4 / US-25: frozen game-economy config registry.

Single source of truth for every constant the XP economy is derived from: the
mode enum, the per-mode valid bit widths, the base XP table, the mode
multipliers, the streak tiers, the penalty factor, the unlock seed and the
anti-farming rate limit. Every other Sprint 4 story (US-26 models, US-29 XP
engine, US-30 levels, US-31 unlocks, US-32 validation) imports from here instead
of re-declaring a number.

Normative source: ``docs/sprint4-plan.md`` — Section 3 holds the design
decisions (D-1..D-8), Section 4 the reference tables. Each constant below
carries the section it comes from. ``docs/xp-economy.md`` (written in US-40)
mirrors these tables in prose for humans; if the two ever disagree, this module
and plan Section 4 win, and the golden tests of US-29/US-41 fail loudly on the
drift.

Guarantees this registry is tested for (tests/test_game_config.py):

* **Bits everywhere** — ``bit_width`` is a bit count in all four modes, so
  ``unicode`` uses 8/16/24/32 (= 1..4 bytes) and 4 is not a unicode width (D-7).
* **Exact rationals only** — streak tiers are integer ``(numerator,
  denominator)`` pairs and the penalty factor is a ``Fraction``. No decimal is
  stored, and because every effective core ``base_xp * mode_mult`` is a multiple
  of 16 (§4.4), the x5/4 tier divides exactly: no remainder, and therefore no
  rounding branch anywhere in the economy (D-8).
* **Immutable** — frozen dataclasses, tuples and read-only mappings, so no
  consumer can mutate shared economy state by accident.
* **Import-side-effect free** — stdlib imports only: no settings, no logging, no
  database, no I/O. Safe to import from models, routers, migrations and contract
  tests alike.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

__all__ = [
    "ANSWER_RATE_LIMIT",
    "ANSWER_RATE_WINDOW_SECONDS",
    "BASE_XP",
    "CONVERSION_MAX_UNLOCK_LEVEL",
    "GameMode",
    "MODES",
    "MODE_MULTIPLIERS",
    "PENALTY_FACTOR",
    "STREAK_TIERS",
    "StreakTier",
    "UNICODE_MAX_UNLOCK_LEVEL",
    "UNLOCK_SEED",
    "UnlockRule",
    "VALID_WIDTHS_BY_MODE",
    "streak_tier_for",
]

# --------------------------------------------------------------------------
# Modes (§4.2, D-7)
# --------------------------------------------------------------------------

# §4.2 / D-7 — the four play modes. Direction-agnostic: the frontend owns the
# direction, the backend infers track/multiplier/widths from the mode name.
# US-26 reuses this alias verbatim as the Pydantic `mode` field type.
type GameMode = Literal["hex_to_bin", "hex_to_dec", "bin_to_dec", "unicode"]

# §4.2 / US-25 — the same enum as a frozen tuple, in plan order (iteration and
# wire-enum order for US-26/US-32). Kept in lockstep with the Literal by tests.
MODES: Final[tuple[GameMode, ...]] = (
    "hex_to_bin",
    "hex_to_dec",
    "bin_to_dec",
    "unicode",
)

# §4.2 / D-7 — valid ``bit_width`` per mode, all values BITS in every mode:
# 4/8/16/32 for the conversion modes, 8/16/24/32 (= 1..4 bytes) for unicode.
# ``unicode`` + 4 is invalid by construction, so US-32 rejects it with a 422
# instead of scoring a 32-bit question as a 4-bit one.
VALID_WIDTHS_BY_MODE: Final[Mapping[GameMode, tuple[int, ...]]] = MappingProxyType(
    {
        "hex_to_bin": (4, 8, 16, 32),
        "hex_to_dec": (4, 8, 16, 32),
        "bin_to_dec": (4, 8, 16, 32),
        "unicode": (8, 16, 24, 32),
    }
)

# --------------------------------------------------------------------------
# Base XP and mode weighting (§4.2, §4.3)
# --------------------------------------------------------------------------

# §4.2 — base XP per (mode, bit_width): 16 entries, keys exactly the width sets
# above, every value a multiple of 4 (D-8). Columns are per-mode width slots,
# not a shared bit count, so unicode's rows sit at 8/16/24/32.
BASE_XP: Final[Mapping[GameMode, Mapping[int, int]]] = MappingProxyType(
    {
        "hex_to_bin": MappingProxyType({4: 16, 8: 32, 16: 64, 32: 128}),
        "hex_to_dec": MappingProxyType({4: 24, 8: 48, 16: 96, 32: 192}),
        "bin_to_dec": MappingProxyType({4: 32, 8: 64, 16: 128, 32: 256}),
        "unicode": MappingProxyType({8: 16, 16: 32, 24: 64, 32: 128}),
    }
)

# §4.3 — difficulty multiplier per mode. Plain ints: they scale an integer core
# and never introduce a decimal (D-8).
MODE_MULTIPLIERS: Final[Mapping[GameMode, int]] = MappingProxyType(
    {
        "hex_to_bin": 1,
        "hex_to_dec": 2,
        "bin_to_dec": 4,
        "unicode": 1,
    }
)

# --------------------------------------------------------------------------
# Streak tiers (§4.4, §4.5, D-3)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreakTier:
    """One streak tier as an exact rational (D-3 / §4.5).

    ``min_streak`` is the inclusive lower bound; the upper bound is the next
    tier's ``min_streak - 1``, and the final tier is open-ended (the cap). The
    tiers are contiguous, so every post-increment streak >= 1 resolves to
    exactly one tier.
    """

    min_streak: int
    numerator: int
    denominator: int

    @property
    def multiplier(self) -> Fraction:
        """The tier as an exact ``Fraction`` (never a decimal)."""
        return Fraction(self.numerator, self.denominator)


# §4.5 / D-3 — the normative streak curve, final: 1-2 x1.0, 3-4 x1.25 = (5,4),
# 5-7 x1.5, 8-10 x2.0, 11+ x2.5 = (5,2) cap (does not grow further). Stored as
# integer rational pairs because the §4.4 core ``base_xp * mode_mult`` is
# divisible by 16 for all 16 combos, so ``(core * numerator) / denominator``
# lands on a multiple of 4 for all 80 combinations with no remainder (D-8).
# The tier is looked up by the POST-increment streak, i.e. the streak including
# the current correct answer: the 3rd consecutive correct answer already earns
# the (5,4) tier (D-3).
STREAK_TIERS: Final[tuple[StreakTier, ...]] = (
    StreakTier(min_streak=1, numerator=1, denominator=1),  # x1.0
    StreakTier(min_streak=3, numerator=5, denominator=4),  # x1.25
    StreakTier(min_streak=5, numerator=3, denominator=2),  # x1.5
    StreakTier(min_streak=8, numerator=2, denominator=1),  # x2.0
    StreakTier(min_streak=11, numerator=5, denominator=2),  # x2.5 (cap)
)


def streak_tier_for(streak: int) -> StreakTier:
    """Resolve a post-increment streak to its tier (§4.5, D-3).

    Args:
        streak: consecutive-correct count *including* the answer being scored,
            so the first correct answer of a run passes 1.

    Returns:
        The matching :class:`StreakTier`; every streak >= 11 gets the (5,2) cap.

    Raises:
        ValueError: if ``streak`` < 1. An award always follows the increment, so
            0 never occurs here; a caller that passes 0 has a bug, and silently
            normalising it to tier x1.0 would hide that bug.
    """
    if streak < 1:
        raise ValueError(f"streak must be >= 1 (post-increment value), got {streak}")
    for index in range(len(STREAK_TIERS) - 1):
        if streak < STREAK_TIERS[index + 1].min_streak:
            return STREAK_TIERS[index]
    return STREAK_TIERS[-1]


# --------------------------------------------------------------------------
# Penalty (D-5, §4.7)
# --------------------------------------------------------------------------

# D-5 / §4.7 — penalty factor, as an exact ``Fraction``. The mode multiplier IS
# applied to the penalty, so ``base_xp * mode_mult * PENALTY_FACTOR`` is exactly
# half of the same combo's x1.0 gain: a uniform 2:1 reward:risk ratio in every
# mode and width (§4.6 XP-P08). The engine returns the positive magnitude of
# that product; the router negates it for ``xp_delta`` (D-8 sign convention).
# Every §4.7 value is an integer multiple of 4, so penalties keep the D-8
# invariant — and the streak tier never enters the penalty (D-5).
PENALTY_FACTOR: Final[Fraction] = Fraction(1, 2)

# --------------------------------------------------------------------------
# Unlock seed (D-2, §4.9)
# --------------------------------------------------------------------------

# D-2 — binding PO caps: no conversion unlock above L13, no unicode unlock
# above L4. Above those levels XP climbs only (prestige/leaderboard).
CONVERSION_MAX_UNLOCK_LEVEL: Final[int] = 13
UNICODE_MAX_UNLOCK_LEVEL: Final[int] = 4


@dataclass(frozen=True, slots=True)
class UnlockRule:
    """A (mode, bit_width) pair available from ``min_level`` (D-2 / §4.9).

    Unlocks are a pure function of the level: no unlock-state table and no
    sticky column. Because the XP floor is ``cum(level)`` (D-1/D-5) the level
    can never drop, so an unlock can never be revoked (§4.9 UN-07).
    """

    mode: GameMode
    bit_width: int  # BITS, in every mode (D-7)
    min_level: int


# D-2 / §4.9 — the seed, 16 rows in plan-table order so a diff against the plan
# reads line-for-line. Ladder (confirmed): slot 1/2/3/4 -> L1/L4/L8/L13 for the
# three conversion modes, L1/L2/L3/L4 for unicode. Widths are bits everywhere,
# hence unicode at 8/16/24/32; ("unicode", 4) has no row on purpose — it is not
# a valid combo at all, and US-32 must reject it with a 422 before any locked
# 403 could be considered (§4.9 UN-06).
UNLOCK_SEED: Final[tuple[UnlockRule, ...]] = (
    UnlockRule("hex_to_bin", 4, 1),
    UnlockRule("hex_to_bin", 8, 4),
    UnlockRule("hex_to_bin", 16, 8),
    UnlockRule("hex_to_bin", 32, 13),
    UnlockRule("hex_to_dec", 4, 1),
    UnlockRule("hex_to_dec", 8, 4),
    UnlockRule("hex_to_dec", 16, 8),
    UnlockRule("hex_to_dec", 32, 13),
    UnlockRule("bin_to_dec", 4, 1),
    UnlockRule("bin_to_dec", 8, 4),
    UnlockRule("bin_to_dec", 16, 8),
    UnlockRule("bin_to_dec", 32, 13),
    UnlockRule("unicode", 8, 1),
    UnlockRule("unicode", 16, 2),
    UnlockRule("unicode", 24, 3),
    UnlockRule("unicode", 32, 4),
)

# --------------------------------------------------------------------------
# Anti-farming rate limit (US-32)
# --------------------------------------------------------------------------

# US-32 — ceiling for POST /api/game/answer: 120 answers per minute per user.
# Plain ints so they plug straight into ``RateLimitService.check(key, limit,
# window_seconds)``; the limiter runs on its own short-lived session, before the
# answer transaction opens, so its commit can never release the row lock.
ANSWER_RATE_LIMIT: Final[int] = 120
ANSWER_RATE_WINDOW_SECONDS: Final[int] = 60
