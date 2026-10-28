"""US-29 (RED layer): golden + property tests for the pure XP engine.

Target module: ``src/services/xp_engine.py`` — pure functions, stdlib +
``src.services.game_config`` only, no DB, no framework (US-29 spec, plan §5).

Specification: ``docs/sprint4-plan.md`` **Section 4.6 in full** — every row of
XP-G01..XP-G22, every row of XP-P01..XP-P08 and every property XP-TP1..XP-TP9 —
checked against the normative tables the rows derive from:

* §4.2 base XP per ``(mode, bit_width)`` and §4.3 mode multipliers (D-7:
  ``bit_width`` means BITS in every mode, so unicode is 8/16/24/32)
* §4.4 effective core ``base_xp * mode_mult`` — the streak-independent x1.0 gain
* §4.5 / D-3 streak curve evaluated on the **post-increment** streak, tier 3-4
  fixed at x1.25 = (5, 4), 11+ capped at x2.5 = (5, 2)
* §4.7 / D-5 penalty ``base_xp * mode_mult * 1/2`` — no streak term, returned as
  a **positive magnitude** (D-8 sign convention pinned in Rev 3 / R-9: the
  router negates it into the wire ``xp_delta``)
* D-1 ``xp_required_for_level(n)`` is the **span** n -> n+1; only its input
  domain is pinned here, the curve itself is LV-01..LV-10 in
  ``tests/test_level_service.py``
* D-8 exact integer arithmetic and the multiple-of-4 invariant

Every expected value below is a **literal copied from the plan table**, never
derived from the module under test, so a wrong constant cannot hide behind a
mirrored formula. Matrix *shapes* come from the frozen registry
(``MODES`` / ``VALID_WIDTHS_BY_MODE`` / ``STREAK_TIERS``) so the 16 x 5 = 80 cell
matrix of §4.6 cannot silently shrink, while every asserted *value* stays
literal; ``test_registry_input_domain_matches_plan`` pins the two together.

XP-TP9's AST scans live here for all three Wave-1 pure modules
(xp_engine / level_service / unlock_service): the float prohibition of D-8 and
the "pure, no DB, no framework" rule are properties of the layer, not of one
file. The scans read the source files from the repository, so they stay
meaningful even before those modules are imported by any test.

No database and no fixtures: this file imports service modules only.
"""

from __future__ import annotations

import ast
import inspect
import sys
from fractions import Fraction
from pathlib import Path

import pytest
from src.services import xp_engine
from src.services.game_config import (
    MODES,
    STREAK_TIERS,
    VALID_WIDTHS_BY_MODE,
)
from src.services.xp_engine import (
    streak_multiplier,
    xp_for_correct,
    xp_penalty_for_incorrect,
    xp_required_for_level,
)

# ---------------------------------------------------------------------------
# Expected values — literals from docs/sprint4-plan.md (never import-side)
# ---------------------------------------------------------------------------

# §4.2 / D-7 — the engine's input domain, in plan order.
EXPECTED_MODES = ("hex_to_bin", "hex_to_dec", "bin_to_dec", "unicode")
EXPECTED_VALID_WIDTHS = {
    "hex_to_bin": (4, 8, 16, 32),
    "hex_to_dec": (4, 8, 16, 32),
    "bin_to_dec": (4, 8, 16, 32),
    "unicode": (8, 16, 24, 32),
}

# §4.4 — effective gain ``base_xp * mode_mult``: the award at tier x1.0.
EXPECTED_CORE = {
    "hex_to_bin": {4: 16, 8: 32, 16: 64, 32: 128},
    "hex_to_dec": {4: 48, 8: 96, 16: 192, 32: 384},
    "bin_to_dec": {4: 128, 8: 256, 16: 512, 32: 1024},
    "unicode": {8: 16, 16: 32, 24: 64, 32: 128},
}

# §4.5 / D-3 — the five tier multipliers in plan order, as exact rationals.
EXPECTED_TIER_MULTIPLIERS = (
    Fraction(1, 1),  # streak 1-2
    Fraction(5, 4),  # streak 3-4 — x1.25, the final curve of §4.5 / D-3
    Fraction(3, 2),  # streak 5-7
    Fraction(2, 1),  # streak 8-10
    Fraction(5, 2),  # streak 11+ (cap)
)

# §4.5 — XP-TP6: multiplier of every streak from 1 to 15.
EXPECTED_MULTIPLIER_BY_STREAK_1_15 = (
    Fraction(1),
    Fraction(1),
    Fraction(5, 4),
    Fraction(5, 4),
    Fraction(3, 2),
    Fraction(3, 2),
    Fraction(3, 2),
    Fraction(2),
    Fraction(2),
    Fraction(2),
    Fraction(5, 2),
    Fraction(5, 2),
    Fraction(5, 2),
    Fraction(5, 2),
    Fraction(5, 2),
)

# §4.7 / D-5 — positive penalty magnitudes (D-5: the mode multiplier IS
# applied to the penalty, which makes the reward:risk ratio a uniform 2:1).
EXPECTED_PENALTIES = {
    "hex_to_bin": {4: 8, 8: 16, 16: 32, 32: 64},
    "hex_to_dec": {4: 24, 8: 48, 16: 96, 32: 192},
    "bin_to_dec": {4: 64, 8: 128, 16: 256, 32: 512},
    "unicode": {8: 8, 16: 16, 24: 32, 32: 64},
}

# §4.6 XP-G01..XP-G19 — (mode, width, post-increment streak, expected XP).
GOLDEN_AWARDS = (
    ("hex_to_bin", 4, 1, 16),  # XP-G01 floor case, x1.0
    ("hex_to_bin", 4, 2, 16),  # XP-G02 top of tier 1-2
    ("hex_to_bin", 4, 3, 20),  # XP-G03 bottom of tier 3-4 (16 x 1.25)
    ("hex_to_bin", 8, 4, 40),  # XP-G04 top of tier 3-4 (32 x 1.25)
    ("hex_to_bin", 16, 5, 96),  # XP-G05 bottom of tier 5-7
    ("hex_to_bin", 32, 7, 192),  # XP-G06 top of tier 5-7
    ("hex_to_dec", 4, 8, 96),  # XP-G07 bottom of tier 8-10, mode x2
    ("hex_to_dec", 8, 5, 144),  # XP-G08 96 x 1.5
    ("hex_to_dec", 16, 10, 384),  # XP-G09 top of tier 8-10
    ("hex_to_dec", 32, 11, 960),  # XP-G10 bottom of tier 11+, x2.5
    ("bin_to_dec", 4, 2, 128),  # XP-G11 mode x4 at the floor tier
    ("bin_to_dec", 8, 7, 384),  # XP-G12 256 x 1.5
    ("bin_to_dec", 16, 3, 640),  # XP-G13 512 x 1.25
    ("bin_to_dec", 32, 12, 2560),  # XP-G14 max single award (1024 x 2.5)
    ("bin_to_dec", 32, 50, 2560),  # XP-G15 cap: streak 11+ identical at 50
    ("unicode", 8, 1, 16),  # XP-G16 unicode floor (1 byte = 8 bits, D-7)
    ("unicode", 16, 8, 64),  # XP-G17 32 x 2.0 (2 bytes)
    ("unicode", 24, 4, 80),  # XP-G18 64 x 1.25 (3 bytes)
    ("unicode", 32, 15, 320),  # XP-G19 128 x 2.5 (4 bytes)
)
GOLDEN_AWARD_IDS = tuple(f"XP-G{index:02d}" for index in range(1, 20))

# §4.6 XP-P01..XP-P06 — (mode, width, expected magnitude). The plan's table shows
# the signed wire value (-8 …); the engine returns the positive magnitude (D-8).
GOLDEN_PENALTIES = (
    ("hex_to_bin", 4, 8),  # XP-P01 (wire -8)
    ("hex_to_bin", 32, 64),  # XP-P02 (wire -64)
    ("hex_to_dec", 16, 96),  # XP-P03 (wire -96)
    ("bin_to_dec", 4, 64),  # XP-P04 (wire -64)
    ("bin_to_dec", 32, 512),  # XP-P05 (wire -512)
    ("unicode", 16, 16),  # XP-P06 (wire -16), width in bits per D-7
)
GOLDEN_PENALTY_IDS = tuple(f"XP-P0{index}" for index in range(1, 7))

# §4.6 XP-P07 — streak probes that live in four different tiers (x1.0, x1.5,
# x2.5, x2.5). They must move the award and never the penalty.
STREAK_PROBES = (1, 5, 11, 50)

# §4.6 XP-G20 — the engine never sees streak 0 (the first correct answer already
# posts 1); a caller passing it has a bug, so it raises instead of normalising.
NON_POSITIVE_STREAKS = (0, -1, -1000)
NON_POSITIVE_STREAK_IDS = ("XP-G20-streak-0", "streak-minus-1", "streak-minus-1000")

# §4.6 XP-G21 / D-7 — widths outside the per-mode set. XP-G21's literal case is
# ("hex_to_bin", 6); ("unicode", 4) is the D-7 case (4 is not a unicode width).
INVALID_WIDTHS = (
    ("hex_to_bin", 6),
    ("hex_to_bin", 24),
    ("hex_to_bin", 0),
    ("hex_to_bin", -8),
    ("hex_to_dec", 6),
    ("hex_to_dec", 24),
    ("bin_to_dec", 12),
    ("bin_to_dec", 24),
    ("unicode", 4),
    ("unicode", 12),
)
INVALID_WIDTH_IDS = (
    "XP-G21-hex_to_bin-6",
    "hex_to_bin-24",
    "hex_to_bin-0",
    "hex_to_bin-minus-8",
    "hex_to_dec-6",
    "hex_to_dec-24",
    "bin_to_dec-12",
    "bin_to_dec-24",
    "unicode-4-not-a-unicode-width",
    "unicode-12",
)

# §4.6 XP-G22 — modes with no row anywhere in the registry.
UNKNOWN_MODES = ("unregistered", "hex_to_binary", "HEX_TO_BIN", "", "unicode_to_bin")
UNKNOWN_MODE_IDS = (
    "XP-G22-unregistered",
    "near-miss-suffix",
    "wrong-case",
    "empty-string",
    "invented-direction",
)

# D-1 — ``xp_required_for_level`` is defined for n >= 1 only.
NON_POSITIVE_LEVELS = (0, -1, -100)

# US-29 target API (names + arity), pinned so US-33 can bind by name.
US29_API_ARITY = {
    "xp_for_correct": 3,
    "xp_penalty_for_incorrect": 2,
    "streak_multiplier": 1,
    "xp_required_for_level": 1,
}

# ---------------------------------------------------------------------------
# Matrices — shape from the frozen registry, values from the literals above
# ---------------------------------------------------------------------------

COMBOS = tuple((mode, w) for mode in MODES for w in VALID_WIDTHS_BY_MODE[mode])
COMBO_IDS = tuple(f"{mode}-{w}" for mode, w in COMBOS)

# One representative streak per tier: the inclusive lower bound (§4.5), so the
# 5 tiers x 16 combos matrix has exactly the 80 cells plan §4.6 demands.
TIER_STREAKS = tuple(tier.min_streak for tier in STREAK_TIERS)

MATRIX = tuple(
    (mode, w, TIER_STREAKS[tier_index])
    for mode, w in COMBOS
    for tier_index in range(len(TIER_STREAKS))
)
MATRIX_IDS = tuple(f"{mode}-{w}-s{streak}" for mode, w, streak in MATRIX)

# XP-TP5: how far past the cap the flatness is probed.
CAP_PROBE = 10**6

# The three Wave-1 pure modules, addressed by path so the AST scans of XP-TP9
# work without importing modules that US-30/US-31 will add.
REPO_ROOT = Path(__file__).resolve().parent.parent
PURE_MODULE_PATHS = (
    ("xp_engine", "src/services/xp_engine.py"),
    ("level_service", "src/services/level_service.py"),
    ("unlock_service", "src/services/unlock_service.py"),
)
PURE_MODULE_IDS = tuple(name for name, _ in PURE_MODULE_PATHS)

# D-8: neither a float literal nor a rounding helper may appear in the layer.
# The bare aliases close the ``from math import ceil`` bypass: an imported
# helper is called by its local name, which a ``math.``-only list would miss.
FORBIDDEN_CALLS = frozenset(
    {
        "round",
        "float",
        "math.ceil",
        "math.floor",
        "ceil",
        "floor",
        "trunc",
        "rint",
        "math.trunc",
    }
)

# "Pure, no DB, no framework": only stdlib plus this frozen slice of src.
PURE_SERVICES = frozenset(
    {"game_config", "xp_engine", "level_service", "unlock_service"}
)


def _expected_award(mode: str, width: int, tier_index: int) -> int:
    """Literal §4.4/§4.5 oracle: ``core * tier_multiplier`` as an exact int."""
    product = EXPECTED_CORE[mode][width] * EXPECTED_TIER_MULTIPLIERS[tier_index]
    assert product.denominator == 1, (mode, width, tier_index)  # D-8 / XP-TP8
    return int(product)


def _tier_index_of(streak: int) -> int:
    """Map a matrix representative streak back to its tier ordinal."""
    return TIER_STREAKS.index(streak)


# ---------------------------------------------------------------------------
# Static helpers for the XP-TP9 AST scans
# ---------------------------------------------------------------------------


def _source(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"missing pure module: {relative_path}"
    return path.read_text(encoding="utf-8")


def _dotted_name(node: ast.expr) -> str | None:
    """``math.ceil`` -> ``"math.ceil"``; anything dynamic -> ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _rounding_violations(source: str) -> list[str]:
    """Float literals and rounding calls, found by AST (not by regex, because a
    docstring legitimately quotes decimals such as 1.25)."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            violations.append(f"line {node.lineno}: float literal {node.value!r}")
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name in FORBIDDEN_CALLS:
                violations.append(f"line {node.lineno}: call to {name}()")
    return violations


def _allows_module(dotted: str) -> bool:
    if not dotted:
        return False
    top, _, rest = dotted.partition(".")
    if top == "src":
        if rest not in ("services",) and not rest.startswith("services."):
            return False
        member = rest.removeprefix("services").removeprefix(".")
        return member == "" or member in PURE_SERVICES
    return top in sys.stdlib_module_names


def _import_violations(source: str) -> list[str]:
    """Every import must resolve to the stdlib or to the frozen pure slice."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _allows_module(alias.name):
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                violations.append(f"line {node.lineno}: relative import")
                continue
            module = node.module or ""
            if not _allows_module(module):
                violations.append(f"line {node.lineno}: from {module} import ...")
            elif module == "src.services":
                for alias in node.names:
                    if alias.name not in PURE_SERVICES:
                        violations.append(
                            f"line {node.lineno}: from src.services import {alias.name}"
                        )
    return violations


# ---------------------------------------------------------------------------
# Registry input domain (D-7 / §4.2) — the shape the matrices iterate over
# ---------------------------------------------------------------------------


def test_registry_input_domain_matches_plan() -> None:
    """The engine's domain is the registry's, and the registry is the plan's.

    Guard for the parametrised matrices: if ``MODES`` or ``VALID_WIDTHS_BY_MODE``
    drifted, a 16-combo matrix could silently become a 12-combo matrix and the
    golden properties would stop covering the missing cells.
    """
    assert MODES == EXPECTED_MODES
    assert {mode: tuple(VALID_WIDTHS_BY_MODE[mode]) for mode in MODES} == (
        EXPECTED_VALID_WIDTHS
    )
    assert len(COMBOS) == 16
    # D-7 made concrete for the engine's own domain: unicode speaks bits.
    assert EXPECTED_VALID_WIDTHS["unicode"] == (8, 16, 24, 32)
    assert 4 not in EXPECTED_VALID_WIDTHS["unicode"]


def test_property_matrix_covers_all_80_combinations() -> None:
    """§4.6: the property tests run over 16 (mode, width) x 5 tiers."""
    assert len(TIER_STREAKS) == 5
    assert TIER_STREAKS == (1, 3, 5, 8, 11)
    assert len(MATRIX) == 80
    assert len(set(MATRIX)) == 80
    assert {(mode, width) for mode, width, _ in MATRIX} == set(COMBOS)


def test_engine_exposes_the_four_us29_callables() -> None:
    """US-29 target API: the four public callables exist with the documented
    arity, so US-33's router can bind them by name."""
    for name, arity in US29_API_ARITY.items():
        function = getattr(xp_engine, name)
        assert callable(function), name
        assert len(inspect.signature(function).parameters) == arity, name


# ---------------------------------------------------------------------------
# §4.6 XP-G01..XP-G19 — the 19 golden awards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "width", "streak", "expected"),
    GOLDEN_AWARDS,
    ids=GOLDEN_AWARD_IDS,
)
def test_golden_award_matches_plan_row(
    mode: str, width: int, streak: int, expected: int
) -> None:
    """§4.6: ``base_xp * mode_mult * streak_mult`` on the post-increment streak."""
    awarded = xp_for_correct(mode, width, streak)
    assert awarded == expected
    assert type(awarded) is int


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_tier_one_award_equals_the_effective_core(mode: str, width: int) -> None:
    """§4.4: at tier x1.0 the award IS the streak-independent core, and streak 2
    sits in the same tier as streak 1 (D-3 post-increment curve)."""
    assert xp_for_correct(mode, width, 1) == EXPECTED_CORE[mode][width]
    assert xp_for_correct(mode, width, 2) == xp_for_correct(mode, width, 1)


def test_max_single_award_in_the_system_is_2560() -> None:
    """§4.6 XP-G14 note: 1024 x 2.5 on bin_to_dec/32 is the system maximum."""
    awards = [xp_for_correct(mode, width, streak) for mode, width, streak in MATRIX]
    assert max(awards) == 2560
    assert len(awards) == 80


# ---------------------------------------------------------------------------
# §4.6 XP-G20..XP-G22 — invalid input raises ValueError (never a silent 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streak", NON_POSITIVE_STREAKS, ids=NON_POSITIVE_STREAK_IDS)
def test_award_raises_for_a_streak_below_one(streak: int) -> None:
    """§4.6 XP-G20: streak 0 never occurs on an award — raise, no normalisation
    and no silent clamp to tier x1.0 (that would hide the caller's bug)."""
    with pytest.raises(ValueError, match="(?i)streak"):
        xp_for_correct("hex_to_bin", 4, streak)


@pytest.mark.parametrize("streak", NON_POSITIVE_STREAKS, ids=NON_POSITIVE_STREAK_IDS)
def test_streak_multiplier_raises_for_a_streak_below_one(streak: int) -> None:
    """§4.5 / D-3: the multiplier shares the award's domain — streak >= 1."""
    with pytest.raises(ValueError, match="(?i)streak"):
        streak_multiplier(streak)


@pytest.mark.parametrize(("mode", "width"), INVALID_WIDTHS, ids=INVALID_WIDTH_IDS)
def test_award_raises_for_a_width_outside_the_mode_set(mode: str, width: int) -> None:
    """§4.6 XP-G21 / D-7: an invalid ``(mode, width)`` pair raises. Defence in
    depth only — the user-facing path is already a 422 from US-32."""
    with pytest.raises(ValueError):
        xp_for_correct(mode, width, 1)


@pytest.mark.parametrize(("mode", "width"), INVALID_WIDTHS, ids=INVALID_WIDTH_IDS)
def test_penalty_raises_for_a_width_outside_the_mode_set(mode: str, width: int) -> None:
    """§4.7 / D-7: the penalty shares the award's domain — ``("unicode", 4)`` is
    not a locked combo, it is not a combo at all (§4.9 UN-06)."""
    with pytest.raises(ValueError):
        xp_penalty_for_incorrect(mode, width)


@pytest.mark.parametrize("mode", UNKNOWN_MODES, ids=UNKNOWN_MODE_IDS)
def test_award_raises_for_an_unknown_mode(mode: str) -> None:
    """§4.6 XP-G22: a mode with no registry row raises ValueError."""
    with pytest.raises(ValueError):
        xp_for_correct(mode, 4, 1)


@pytest.mark.parametrize("mode", UNKNOWN_MODES, ids=UNKNOWN_MODE_IDS)
def test_penalty_raises_for_an_unknown_mode(mode: str) -> None:
    """§4.7: unknown mode raises on the penalty path too (same domain)."""
    with pytest.raises(ValueError):
        xp_penalty_for_incorrect(mode, 4)


@pytest.mark.parametrize("level", NON_POSITIVE_LEVELS)
def test_xp_required_for_level_raises_below_one(level: int) -> None:
    """D-1: the span n -> n+1 is defined for n >= 1; the curve values themselves
    are pinned by LV-01..LV-10 in ``tests/test_level_service.py``."""
    with pytest.raises(ValueError):
        xp_required_for_level(level)


# ---------------------------------------------------------------------------
# §4.6 XP-P01..XP-P08 / §4.7 / D-5 — penalties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "width", "expected"), GOLDEN_PENALTIES, ids=GOLDEN_PENALTY_IDS
)
def test_golden_penalty_is_the_positive_magnitude(
    mode: str, width: int, expected: int
) -> None:
    """§4.6 XP-P01..P06 with D-8's sign convention (R-9): the engine returns the
    positive magnitude; the router negates it into the wire ``xp_delta``."""
    penalty = xp_penalty_for_incorrect(mode, width)
    assert penalty == expected
    assert penalty > 0
    assert type(penalty) is int


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_penalty_table_matches_plan_section_4_7(mode: str, width: int) -> None:
    """§4.7: all 16 positive magnitudes, pinned as literals, each a multiple
    of 4 (D-8)."""
    penalty = xp_penalty_for_incorrect(mode, width)
    assert penalty == EXPECTED_PENALTIES[mode][width]
    assert penalty % 4 == 0


def test_penalty_sign_convention_is_positive_for_every_combo() -> None:
    """D-8 / R-9: no engine return is negative or zero, so the only place a sign
    appears is the router's negation."""
    for mode, width in COMBOS:
        penalty = xp_penalty_for_incorrect(mode, width)
        assert penalty > 0, (mode, width)
        assert type(penalty) is int, (mode, width, type(penalty))


def test_penalty_function_takes_no_streak_argument() -> None:
    """§4.6 XP-P07 at the API level: the penalty has no streak axis at all — D-5
    says the streak tier never enters the penalty."""
    assert tuple(inspect.signature(xp_penalty_for_incorrect).parameters) == (
        "mode",
        "bit_width",
    )


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_penalty_is_never_a_streak_scaled_award(mode: str, width: int) -> None:
    """§4.6 XP-P07: streaks {1, 5, 11, 50} spread over four tiers move the award
    but the penalty stays the single table value, and can never coincide with an
    award of the same combo."""
    penalty = xp_penalty_for_incorrect(mode, width)
    awards = {xp_for_correct(mode, width, streak) for streak in STREAK_PROBES}
    assert penalty == EXPECTED_PENALTIES[mode][width]
    assert len(awards) >= 2  # the streak probes really do move the award
    assert penalty not in awards


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_penalty_is_exactly_half_the_tier_one_gain(mode: str, width: int) -> None:
    """§4.6 XP-P08 / D-5 (the mode multiplier IS part of the penalty): a uniform
    2:1 reward:risk ratio in every mode and width."""
    penalty = xp_penalty_for_incorrect(mode, width)
    tier_one_gain = xp_for_correct(mode, width, 1)
    assert tier_one_gain == EXPECTED_CORE[mode][width]
    assert tier_one_gain == 2 * penalty


@pytest.mark.parametrize("slot", range(4))
def test_unicode_and_hex_to_bin_share_the_same_penalty_per_slot(slot: int) -> None:
    """§4.7: unicode and hex_to_bin have identical cores, so identical penalties
    slot by slot (8/16/24/32 bits vs 4/8/16/32 bits — the mode disambiguates).
    Anchored to both literal rows, not just to each other, so a degenerate engine
    that returns the same value everywhere cannot satisfy the parity."""
    unicode_width = EXPECTED_VALID_WIDTHS["unicode"][slot]
    conversion_width = EXPECTED_VALID_WIDTHS["hex_to_bin"][slot]
    unicode_penalty = xp_penalty_for_incorrect("unicode", unicode_width)
    conversion_penalty = xp_penalty_for_incorrect("hex_to_bin", conversion_width)
    assert unicode_penalty == EXPECTED_PENALTIES["unicode"][unicode_width]
    assert conversion_penalty == EXPECTED_PENALTIES["hex_to_bin"][conversion_width]
    assert unicode_penalty == conversion_penalty


# ---------------------------------------------------------------------------
# §4.6 XP-TP1..XP-TP9 — property tests over the full 16 x 5 matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_award_is_an_integer_multiple_of_4(mode: str, width: int, streak: int) -> None:
    """XP-TP1 / D-8: every one of the 80 awards is a plain int in 4z — no
    remainder, no rounding branch, no decimal anywhere in the economy."""
    awarded = xp_for_correct(mode, width, streak)
    assert type(awarded) is int
    assert awarded % 4 == 0
    assert awarded > 0


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_award_does_not_decrease_as_the_streak_tier_rises(
    mode: str, width: int, streak: int
) -> None:
    """XP-TP2: fixed (mode, width) — 1.0 <= 1.25 <= 1.5 <= 2.0 <= 2.5. Because
    every core is positive and the multipliers are distinct, the chain is strict;
    the floor tier must reproduce the §4.4 core exactly."""
    awarded = xp_for_correct(mode, width, streak)
    tier_index = _tier_index_of(streak)
    assert awarded == _expected_award(mode, width, tier_index)
    if tier_index == 0:
        assert awarded == EXPECTED_CORE[mode][width]
    else:
        lower = xp_for_correct(mode, width, TIER_STREAKS[tier_index - 1])
        assert awarded > lower


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_award_strictly_increases_with_bit_width(
    mode: str, width: int, streak: int
) -> None:
    """XP-TP3: fixed (mode, tier) — XP strictly increases with the width slot.
    Every cell has a neighbour to compare against (slot 1 a wider one, slot 4 a
    narrower one), plus the literal §4.4/§4.5 oracle."""
    awarded = xp_for_correct(mode, width, streak)
    widths = VALID_WIDTHS_BY_MODE[mode]
    assert awarded == _expected_award(mode, width, _tier_index_of(streak))
    narrower = [w for w in widths if w < width]
    wider = [w for w in widths if w > width]
    for other in narrower:
        assert awarded > xp_for_correct(mode, other, streak), (mode, width, streak)
    for other in wider:
        assert awarded < xp_for_correct(mode, other, streak), (mode, width, streak)


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_award_orders_the_modes_by_difficulty_within_a_slot(
    mode: str, width: int, streak: int
) -> None:
    """XP-TP4: equal slot and tier — bin_to_dec > hex_to_dec > hex_to_bin, and
    unicode equals hex_to_bin. Compared by **slot index**, not raw bit count:
    §4.2 gives unicode the slots 8/16/24/32, so 24 has no conversion counterpart
    and a bit-count join would be undefined."""
    tier_index = _tier_index_of(streak)
    slot = VALID_WIDTHS_BY_MODE[mode].index(width)
    same_slot = {
        other: xp_for_correct(other, VALID_WIDTHS_BY_MODE[other][slot], streak)
        for other in MODES
    }
    assert same_slot["bin_to_dec"] > same_slot["hex_to_dec"]
    assert same_slot["hex_to_dec"] > same_slot["hex_to_bin"]
    assert same_slot["unicode"] == same_slot["hex_to_bin"]
    assert same_slot[mode] == _expected_award(mode, width, tier_index)


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_streak_cap_is_flat_only_from_streak_eleven(
    mode: str, width: int, streak: int
) -> None:
    """XP-TP5 / D-3: x2.5 is a cap and does not grow further. Inside the cap tier
    the award is flat across 11 / 12 / 10**6; below it the same +10**6 jump still
    lifts the award, so the cap region cannot start early."""
    tier_index = _tier_index_of(streak)
    beyond = xp_for_correct(mode, width, streak + CAP_PROBE)
    if tier_index == len(TIER_STREAKS) - 1:
        assert beyond == xp_for_correct(mode, width, streak)
        assert beyond == xp_for_correct(mode, width, 12)
        assert beyond == xp_for_correct(mode, width, CAP_PROBE)
        assert beyond == _expected_award(mode, width, tier_index)
    else:
        assert beyond > xp_for_correct(mode, width, streak)


def test_streak_multiplier_cap_is_exactly_two_and_a_half() -> None:
    """XP-TP5 on the multiplier itself: tier(11) == tier(12) == tier(10**6) ==
    (5, 2) as an exact Fraction."""
    assert streak_multiplier(11) == streak_multiplier(12)
    assert streak_multiplier(12) == streak_multiplier(CAP_PROBE)
    assert streak_multiplier(CAP_PROBE) == Fraction(5, 2)


@pytest.mark.parametrize("streak", range(1, 16))
def test_streak_multiplier_matches_the_normative_curve(streak: int) -> None:
    """XP-TP6 / §4.5: streaks 1..15 map to
    [1, 1, 1.25, 1.25, 1.5, 1.5, 1.5, 2, 2, 2, 2.5, 2.5, 2.5, 2.5, 2.5] — the
    post-increment curve, so the 3rd correct answer already earns x1.25 (D-3)."""
    assert streak_multiplier(streak) == EXPECTED_MULTIPLIER_BY_STREAK_1_15[streak - 1]


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_penalty_is_positive_multiple_of_4_and_half_the_gain(
    mode: str, width: int
) -> None:
    """XP-TP7 / D-5: penalty is a positive int, a multiple of 4, and exactly half
    of the tier-1.0 gain — identical whether the surrounding streak is 1 or 2
    (both x1.0) so no streak term can leak into it."""
    penalty = xp_penalty_for_incorrect(mode, width)
    assert type(penalty) is int
    assert penalty > 0
    assert penalty % 4 == 0
    assert 2 * penalty == xp_for_correct(mode, width, 1)
    assert 2 * penalty == xp_for_correct(mode, width, 2)


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_divisibility_guard_holds_for_every_combination(
    mode: str, width: int, streak: int
) -> None:
    """XP-TP8 / D-8: ``(core * num) % den == 0`` for all 80 cells — the runtime
    guard of the engine, tested directly — and the award is exactly that integer
    quotient, never a rounded approximation of it."""
    tier = STREAK_TIERS[_tier_index_of(streak)]
    product = EXPECTED_CORE[mode][width] * tier.numerator
    assert product % tier.denominator == 0, (mode, width, streak)
    assert xp_for_correct(mode, width, streak) == product // tier.denominator


@pytest.mark.parametrize(("mode", "width", "streak"), MATRIX, ids=MATRIX_IDS)
def test_award_return_type_is_a_plain_int(mode: str, width: int, streak: int) -> None:
    """XP-TP9: the award is a plain ``int`` — an exact type check rejects both a
    ``float`` and a ``bool`` disguised as a count. The positivity anchor stops the
    type assertion from being satisfied by a degenerate always-zero engine."""
    award = xp_for_correct(mode, width, streak)
    assert type(award) is int
    assert award > 0, (mode, width, streak)


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_penalty_return_type_is_a_plain_int(mode: str, width: int) -> None:
    """XP-TP9: same exact-type rule on the penalty path, again anchored to a
    strictly positive magnitude (D-8 sign convention)."""
    penalty = xp_penalty_for_incorrect(mode, width)
    assert type(penalty) is int
    assert penalty > 0, (mode, width)


@pytest.mark.parametrize("tier_index", range(len(EXPECTED_TIER_MULTIPLIERS)))
def test_streak_multiplier_returns_an_exact_fraction(tier_index: int) -> None:
    """XP-TP9: the only non-integer return in the engine is the exact ``Fraction``
    of the stored rational pair — never a decimal approximation."""
    multiplier = streak_multiplier(TIER_STREAKS[tier_index])
    assert type(multiplier) is Fraction
    assert multiplier == EXPECTED_TIER_MULTIPLIERS[tier_index]
    assert multiplier.denominator > 0


@pytest.mark.parametrize(
    ("name", "relative_path"), PURE_MODULE_PATHS, ids=PURE_MODULE_IDS
)
def test_pure_module_uses_no_float_or_rounding(name: str, relative_path: str) -> None:
    """XP-TP9 / D-8: no float literal and no ``round`` / ``float`` / ``math.ceil``
    / ``math.floor`` call in any Wave-1 pure module.

    The scan walks the AST instead of grepping the text, because a docstring
    legitimately quotes decimals such as "1.25" and a regex would report false
    reds.
    """
    violations = _rounding_violations(_source(relative_path))
    assert violations == [], f"{name}: {violations}"


@pytest.mark.parametrize(
    ("name", "relative_path"), PURE_MODULE_PATHS, ids=PURE_MODULE_IDS
)
def test_pure_module_imports_only_stdlib_and_the_registry(
    name: str, relative_path: str
) -> None:
    """US-29/US-30/US-31 "pure, no DB, no framework": every import resolves to
    the stdlib or to the frozen pure slice of ``src.services`` — never to
    SQLAlchemy, FastAPI, pydantic, settings, the engine or the ORM models (which
    is also what keeps §4.9 UN-07 free of any sticky-state column)."""
    violations = _import_violations(_source(relative_path))
    assert violations == [], f"{name}: {violations}"
