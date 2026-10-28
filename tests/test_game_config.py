"""US-25: tests for the frozen game-economy config registry.

The registry (``src/services/game_config.py``) is the single source of truth
every other Sprint 4 story imports, so these tests pin it against the normative
tables in ``docs/sprint4-plan.md`` Section 3 (D-1..D-8) and Section 4. Every
expected value below is a **literal copied from the plan**, deliberately not
derived from the module, so a wrong constant cannot hide behind a wrong formula.

Covered acceptance criteria (US-25):

* table completeness — exactly 16 ``(mode, bit_width)`` base-XP entries whose
  keys match the per-mode width sets exactly, every value ``% 4 == 0`` (§4.2/D-8)
* the effective core ``base_xp * mode_mult`` is divisible by 16 for all 16
  combos (§4.4) — the property that makes the x5/4 streak tier exact
* per-mode width sets pinned in bits, incl. unicode ``8/16/24/32`` and the
  explicit rejections ``4 not in unicode`` / ``24 not in conversions`` (§4.2/D-7)
* streak tiers as exact integer rational pairs incl. the 11+ cap, no float
  anywhere (§4.5/D-3)
* unlock seed — 16 entries, caps L13/L4, slot ladders 1/4/8/13 and 1/2/3/4 (D-2)
* penalty factor ``Fraction(1, 2)`` and the 2:1 gain:penalty ratio (D-5/§4.7)
* import-side-effect freedom and the absence of any stale open-question or
  rounding seam marker (Section 7 gate #3)

No database is touched: this module imports the registry only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import get_args

import pytest
from src.services import game_config
from src.services.game_config import (
    ANSWER_RATE_LIMIT,
    ANSWER_RATE_WINDOW_SECONDS,
    BASE_XP,
    CONVERSION_MAX_UNLOCK_LEVEL,
    MODE_MULTIPLIERS,
    MODES,
    PENALTY_FACTOR,
    STREAK_TIERS,
    UNICODE_MAX_UNLOCK_LEVEL,
    UNLOCK_SEED,
    VALID_WIDTHS_BY_MODE,
    GameMode,
    streak_tier_for,
)

# ---------------------------------------------------------------------------
# Expected values — literals from docs/sprint4-plan.md (never import-side)
# ---------------------------------------------------------------------------

# US-25 / plan §4.2 + D-7: the four modes, in plan order.
EXPECTED_MODES = ("hex_to_bin", "hex_to_dec", "bin_to_dec", "unicode")

# Plan §4.2 / D-7 (OQ-7): bit widths, in every mode.
EXPECTED_VALID_WIDTHS = {
    "hex_to_bin": (4, 8, 16, 32),
    "hex_to_dec": (4, 8, 16, 32),
    "bin_to_dec": (4, 8, 16, 32),
    "unicode": (8, 16, 24, 32),
}

# Plan §4.2: base XP per (mode, width).
EXPECTED_BASE_XP = {
    "hex_to_bin": {4: 16, 8: 32, 16: 64, 32: 128},
    "hex_to_dec": {4: 24, 8: 48, 16: 96, 32: 192},
    "bin_to_dec": {4: 32, 8: 64, 16: 128, 32: 256},
    "unicode": {8: 16, 16: 32, 24: 64, 32: 128},
}

# Plan §4.3: mode multipliers.
EXPECTED_MODE_MULTIPLIERS = {
    "hex_to_bin": 1,
    "hex_to_dec": 2,
    "bin_to_dec": 4,
    "unicode": 1,
}

# Plan §4.5 / D-3: tiers as rational (numerator, denominator) pairs. The 3-4 tier
# is x1.25 = (5, 4); 11+ is the x2.5 = (5, 2) cap. Final — no held-back variant.
EXPECTED_TIER_PAIRS_BY_STREAK = [
    (1, 1),  # streak 1
    (1, 1),  # streak 2
    (5, 4),  # streak 3
    (5, 4),  # streak 4
    (3, 2),  # streak 5
    (3, 2),  # streak 6
    (3, 2),  # streak 7
    (2, 1),  # streak 8
    (2, 1),  # streak 9
    (2, 1),  # streak 10
]
EXPECTED_CAP_TIER = (5, 2)

# Plan §4.7: positive penalty magnitudes == core * 1/2.
EXPECTED_PENALTIES = {
    "hex_to_bin": {4: 8, 8: 16, 16: 32, 32: 64},
    "hex_to_dec": {4: 24, 8: 48, 16: 96, 32: 192},
    "bin_to_dec": {4: 64, 8: 128, 16: 256, 32: 512},
    "unicode": {8: 8, 16: 16, 24: 32, 32: 64},
}

# Plan D-2 / §4.9 (OQ-6): 16 seed rows, in table order.
EXPECTED_UNLOCK_SEED = [
    ("hex_to_bin", 4, 1),
    ("hex_to_bin", 8, 4),
    ("hex_to_bin", 16, 8),
    ("hex_to_bin", 32, 13),
    ("hex_to_dec", 4, 1),
    ("hex_to_dec", 8, 4),
    ("hex_to_dec", 16, 8),
    ("hex_to_dec", 32, 13),
    ("bin_to_dec", 4, 1),
    ("bin_to_dec", 8, 4),
    ("bin_to_dec", 16, 8),
    ("bin_to_dec", 32, 13),
    ("unicode", 8, 1),
    ("unicode", 16, 2),
    ("unicode", 24, 3),
    ("unicode", 32, 4),
]

CONVERSION_MODES = ("hex_to_bin", "hex_to_dec", "bin_to_dec")

# The 16 (mode, width) combos, built from the literals above (not the module).
ALL_COMBOS = [(mode, w) for mode in EXPECTED_MODES for w in EXPECTED_VALID_WIDTHS[mode]]

COMBO_IDS = [f"{mode}-{w}" for mode, w in ALL_COMBOS]

# Public names the rest of Sprint 4 (US-26/29/31/32) references by name.
EXPECTED_PUBLIC_NAMES = {
    "GameMode",
    "MODES",
    "VALID_WIDTHS_BY_MODE",
    "BASE_XP",
    "MODE_MULTIPLIERS",
    "STREAK_TIERS",
    "StreakTier",
    "streak_tier_for",
    "PENALTY_FACTOR",
    "UNLOCK_SEED",
    "UnlockRule",
    "CONVERSION_MAX_UNLOCK_LEVEL",
    "UNICODE_MAX_UNLOCK_LEVEL",
    "ANSWER_RATE_LIMIT",
    "ANSWER_RATE_WINDOW_SECONDS",
}


def _core(mode: str, width: int) -> int:
    """Effective gain ``base_xp * mode_mult`` (plan §4.4) from the literals."""
    return EXPECTED_BASE_XP[mode][width] * EXPECTED_MODE_MULTIPLIERS[mode]


def _literal_members() -> tuple[str, ...]:
    """Members of the PEP 695 ``type GameMode = Literal[...]`` alias.

    ``get_args`` treats a bare ``TypeAliasType`` as opaque, so the underlying
    value is unwrapped first.
    """
    return get_args(GameMode.__value__)


# ---------------------------------------------------------------------------
# Mode enum + per-mode width sets (§4.2 / D-7)
# ---------------------------------------------------------------------------


def test_mode_tuple_matches_plan_order() -> None:
    assert MODES == EXPECTED_MODES
    assert type(MODES) is tuple


def test_mode_literal_and_frozen_tuple_agree() -> None:
    """The Literal alias and the tuple must not drift apart (§4.2)."""
    assert _literal_members() == EXPECTED_MODES


def test_valid_widths_by_mode_pinned_in_bits() -> None:
    """All values are BITS in every mode (D-7)."""
    assert {mode: tuple(w) for mode, w in VALID_WIDTHS_BY_MODE.items()} == (
        EXPECTED_VALID_WIDTHS
    )
    assert set(VALID_WIDTHS_BY_MODE) == set(EXPECTED_MODES)


def test_unicode_widths_are_bits_and_exclude_4() -> None:
    """unicode speaks bits 8/16/24/32 — 4 is NOT a unicode width (§4.2)."""
    assert tuple(VALID_WIDTHS_BY_MODE["unicode"]) == (8, 16, 24, 32)
    assert 4 not in VALID_WIDTHS_BY_MODE["unicode"]
    assert 24 in VALID_WIDTHS_BY_MODE["unicode"]


@pytest.mark.parametrize("mode", CONVERSION_MODES)
def test_conversion_widths_exclude_24(mode: str) -> None:
    """Conversion modes carry 4/8/16/32 only — 24 is not a conversion width."""
    assert tuple(VALID_WIDTHS_BY_MODE[mode]) == (4, 8, 16, 32)
    assert 24 not in VALID_WIDTHS_BY_MODE[mode]


# ---------------------------------------------------------------------------
# Base XP table + mode multipliers (§4.2 / §4.3 / D-8)
# ---------------------------------------------------------------------------


def test_base_xp_table_has_exactly_16_entries() -> None:
    entries = [(mode, w, xp) for mode in BASE_XP for w, xp in BASE_XP[mode].items()]
    assert len(entries) == 16
    assert {(m, w) for m, w, _ in entries} == set(ALL_COMBOS)


def test_base_xp_values_pinned_to_plan() -> None:
    assert {m: dict(BASE_XP[m]) for m in BASE_XP} == EXPECTED_BASE_XP


def test_base_xp_keys_match_valid_widths_exactly() -> None:
    """No mode may have a base-XP row without a width, nor the reverse."""
    assert set(BASE_XP) == set(EXPECTED_MODES)
    for mode in EXPECTED_MODES:
        assert set(BASE_XP[mode]) == set(VALID_WIDTHS_BY_MODE[mode]), mode


def test_mode_multipliers_pinned_to_plan() -> None:
    assert dict(MODE_MULTIPLIERS) == EXPECTED_MODE_MULTIPLIERS
    assert set(MODE_MULTIPLIERS) == set(EXPECTED_MODES)


def test_base_xp_values_are_plain_ints_never_bools_or_floats() -> None:
    for mode, width in ALL_COMBOS:
        value = BASE_XP[mode][width]
        assert type(value) is int, (mode, width, type(value))


@pytest.mark.parametrize(("mode", "width"), ALL_COMBOS, ids=COMBO_IDS)
def test_every_base_xp_is_a_multiple_of_4(mode: str, width: int) -> None:
    """D-8 invariant: every base value is an exact multiple of 4."""
    assert BASE_XP[mode][width] % 4 == 0


@pytest.mark.parametrize(("mode", "width"), ALL_COMBOS, ids=COMBO_IDS)
def test_effective_core_is_divisible_by_16(mode: str, width: int) -> None:
    """§4.4: ``base_xp * mode_mult`` is divisible by 16 for all 16 combos.

    This is precisely the property that makes the x5/4 streak tier land on a
    multiple of 4 for every combination, so no rounding path is needed.
    """
    core = BASE_XP[mode][width] * MODE_MULTIPLIERS[mode]
    assert core == _core(mode, width)
    assert core % 16 == 0


# ---------------------------------------------------------------------------
# Awarded XP: exactness over the full 16 x 5 matrix (§4.5 / D-8 / XP-TP1/TP8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "width"), ALL_COMBOS, ids=COMBO_IDS)
def test_awarded_xp_is_integral_and_multiple_of_4(mode: str, width: int) -> None:
    """``(core * num) % den == 0`` for every combo and tier — no remainder."""
    core = _core(mode, width)
    for tier in STREAK_TIERS:
        product = core * tier.numerator
        assert product % tier.denominator == 0, (mode, width, tier)
        awarded = product // tier.denominator
        assert type(awarded) is int
        assert awarded % 4 == 0, (mode, width, tier)


def test_tier_multipliers_are_non_decreasing_and_capped() -> None:
    """§4.5 ordering: 1.0 <= 1.25 <= 1.5 <= 2.0 <= 2.5, cap never exceeded."""
    values = [Fraction(t.numerator, t.denominator) for t in STREAK_TIERS]
    assert values == sorted(values)
    assert values[0] == Fraction(1)
    assert max(values) == Fraction(5, 2)


def test_streak_tier_multiplier_is_an_exact_fraction() -> None:
    """The derived accessor US-29's ``streak_multiplier()`` returns: an exact
    ``Fraction`` of the stored pair, never a decimal approximation."""
    for tier in STREAK_TIERS:
        assert type(tier.multiplier) is Fraction
        assert tier.multiplier == Fraction(tier.numerator, tier.denominator)
    assert streak_tier_for(3).multiplier == Fraction(5, 4)
    assert streak_tier_for(11).multiplier == Fraction(5, 2)


# ---------------------------------------------------------------------------
# Streak tiers (§4.5 / D-3)
# ---------------------------------------------------------------------------


def test_streak_tier_pairs_pinned_to_plan() -> None:
    """Tier 3-4 is x1.25 = (5, 4); 11+ is the (5, 2) cap. No variants."""
    assert [(t.min_streak, t.numerator, t.denominator) for t in STREAK_TIERS] == [
        (1, 1, 1),
        (3, 5, 4),
        (5, 3, 2),
        (8, 2, 1),
        (11, 5, 2),
    ]


def test_streak_tiers_are_integer_rationals_never_floats() -> None:
    for tier in STREAK_TIERS:
        assert type(tier.numerator) is int, tier
        assert type(tier.denominator) is int, tier
        assert type(tier.min_streak) is int, tier
        assert tier.denominator > 0


def test_streak_tiers_start_at_one_and_are_sorted() -> None:
    mins = [t.min_streak for t in STREAK_TIERS]
    assert mins[0] == 1
    assert mins == sorted(mins)
    assert len(set(mins)) == len(mins)


@pytest.mark.parametrize("streak", list(range(1, 11)))
def test_streak_tier_for_matches_normative_curve(streak: int) -> None:
    """The multiplier uses the POST-INCREMENT streak (D-3): the 3rd correct
    answer already earns the 3-4 tier."""
    tier = streak_tier_for(streak)
    assert (tier.numerator, tier.denominator) == EXPECTED_TIER_PAIRS_BY_STREAK[
        streak - 1
    ]


@pytest.mark.parametrize("streak", range(11, 41))
def test_streak_tier_for_returns_cap_above_11(streak: int) -> None:
    assert (
        streak_tier_for(streak).numerator,
        streak_tier_for(streak).denominator,
    ) == EXPECTED_CAP_TIER


def test_streak_cap_is_flat_far_above_the_threshold() -> None:
    """XP-TP5: tier(11) == tier(12) == tier(10**6) == x2.5."""
    assert streak_tier_for(11) is streak_tier_for(12) is streak_tier_for(10**6)


@pytest.mark.parametrize("streak", [0, -1, -100])
def test_streak_tier_for_rejects_non_positive_streak(streak: int) -> None:
    """Streak 0 never occurs on an award (first correct -> streak 1): raise."""
    with pytest.raises(ValueError, match="streak"):
        streak_tier_for(streak)


def test_streak_tier_is_frozen() -> None:
    tier = streak_tier_for(1)
    with pytest.raises(FrozenInstanceError):
        tier.numerator = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Penalty factor + penalty table (§4.7 / D-5, OQ-2: the mode multiplier IS applied)
# ---------------------------------------------------------------------------


def test_penalty_factor_is_exact_half() -> None:
    assert type(PENALTY_FACTOR) is Fraction
    assert PENALTY_FACTOR == Fraction(1, 2)


@pytest.mark.parametrize(("mode", "width"), ALL_COMBOS, ids=COMBO_IDS)
def test_penalty_values_pinned_to_plan_section_4_7(mode: str, width: int) -> None:
    """Penalty == ``base_xp * mode_mult * 1/2``, exact integer, multiple of 4."""
    penalty = _core(mode, width) * PENALTY_FACTOR
    assert penalty.denominator == 1, (mode, width)
    assert int(penalty) == EXPECTED_PENALTIES[mode][width]
    assert int(penalty) % 4 == 0
    assert int(penalty) > 0


@pytest.mark.parametrize(("mode", "width"), ALL_COMBOS, ids=COMBO_IDS)
def test_penalty_is_exactly_half_the_tier_one_gain(mode: str, width: int) -> None:
    """XP-P08 / OQ-2: uniform 2:1 reward:risk in every mode and width."""
    tier_one = streak_tier_for(1)
    gain = _core(mode, width) * tier_one.numerator // tier_one.denominator
    penalty = int(_core(mode, width) * PENALTY_FACTOR)
    assert gain == 2 * penalty


# ---------------------------------------------------------------------------
# Unlock seed (D-2 / §4.9)
# ---------------------------------------------------------------------------


def test_unlock_seed_pinned_to_plan_d_2() -> None:
    rows = [(r.mode, r.bit_width, r.min_level) for r in UNLOCK_SEED]
    assert rows == EXPECTED_UNLOCK_SEED
    assert len(rows) == 16


@pytest.mark.parametrize("mode", CONVERSION_MODES)
def test_unlock_ladder_for_conversion_modes_is_1_4_8_13(mode: str) -> None:
    levels = sorted(r.min_level for r in UNLOCK_SEED if r.mode == mode)
    assert levels == [1, 4, 8, 13]


def test_unlock_ladder_for_unicode_is_1_2_3_4() -> None:
    levels = sorted(r.min_level for r in UNLOCK_SEED if r.mode == "unicode")
    assert levels == [1, 2, 3, 4]


def test_unlock_caps_are_binding() -> None:
    """PO cap: no conversion entry above L13, no unicode entry above L4."""
    assert CONVERSION_MAX_UNLOCK_LEVEL == 13
    assert UNICODE_MAX_UNLOCK_LEVEL == 4
    for rule in UNLOCK_SEED:
        cap = (
            UNICODE_MAX_UNLOCK_LEVEL
            if rule.mode == "unicode"
            else CONVERSION_MAX_UNLOCK_LEVEL
        )
        assert rule.min_level <= cap, rule
    assert max(r.min_level for r in UNLOCK_SEED if r.mode != "unicode") == 13
    assert max(r.min_level for r in UNLOCK_SEED if r.mode == "unicode") == 4


def test_unlock_seed_covers_every_combo_exactly_once() -> None:
    keys = [(r.mode, r.bit_width) for r in UNLOCK_SEED]
    assert len(keys) == len(set(keys)) == 16
    assert set(keys) == set(ALL_COMBOS)


def test_unicode_4bit_is_not_an_unlock_row() -> None:
    """UN-06: 4 is not a valid unicode width at all, so it must never appear
    in the seed — that combo is a 422, not a locked 403."""
    assert not any(r.mode == "unicode" and r.bit_width == 4 for r in UNLOCK_SEED)
    assert all((r.mode, r.bit_width) in set(ALL_COMBOS) for r in UNLOCK_SEED)


def test_unlock_rule_is_frozen() -> None:
    rule = UNLOCK_SEED[0]
    with pytest.raises(FrozenInstanceError):
        rule.min_level = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Anti-farming rate limit (US-32)
# ---------------------------------------------------------------------------


def test_answer_rate_limit_constants() -> None:
    """120 answers per minute per user; the limiter takes plain ints."""
    assert ANSWER_RATE_LIMIT == 120
    assert ANSWER_RATE_WINDOW_SECONDS == 60
    assert type(ANSWER_RATE_LIMIT) is int
    assert type(ANSWER_RATE_WINDOW_SECONDS) is int


# ---------------------------------------------------------------------------
# Immutability, import purity, stale-marker hygiene
# ---------------------------------------------------------------------------


def test_top_level_mappings_are_read_only() -> None:
    for mapping in (VALID_WIDTHS_BY_MODE, BASE_XP, MODE_MULTIPLIERS):
        assert isinstance(mapping, MappingProxyType)
    with pytest.raises(TypeError):
        VALID_WIDTHS_BY_MODE["hex_to_bin"] = ()  # type: ignore[typeddict-item]
    with pytest.raises(TypeError):
        del BASE_XP["unicode"]  # type: ignore[attr-defined]


def test_nested_base_xp_mappings_are_read_only() -> None:
    for mode in EXPECTED_MODES:
        assert isinstance(BASE_XP[mode], MappingProxyType), mode
    with pytest.raises(TypeError):
        BASE_XP["hex_to_bin"][4] = 999  # type: ignore[index]


def test_sequences_are_tuples_not_lists() -> None:
    assert type(MODES) is tuple
    assert type(STREAK_TIERS) is tuple
    assert type(UNLOCK_SEED) is tuple
    for mode in EXPECTED_MODES:
        assert type(VALID_WIDTHS_BY_MODE[mode]) is tuple, mode


def test_invalid_combo_lookup_is_a_keyerror_not_a_silent_zero() -> None:
    """Defence-in-depth shape: an unregistered (mode, width) has no row at all,
    so a lookup raises instead of returning a misleading 0 (XP-G21/G22)."""
    with pytest.raises(KeyError):
        BASE_XP["unicode"][4]
    with pytest.raises(KeyError):
        BASE_XP["unregistered"]


def test_module_exports_exactly_the_public_names() -> None:
    assert set(game_config.__all__) == EXPECTED_PUBLIC_NAMES
    assert sorted(game_config.__all__) == sorted(set(game_config.__all__))


def test_module_contains_no_stale_open_question_or_rounding_seam() -> None:
    """Section 7 gate #3 / US-42: every open question was closed before Day 1,
    so an ``OQ`` marker or an Option-B rounding seam in the registry means the
    code was written from a stale revision. Treat it as a defect."""
    source = Path(game_config.__file__).read_text(encoding="utf-8")
    assert "OQ" not in source
    assert "Option B" not in source
    assert "round(" not in source
    assert "math.ceil" not in source
    assert "float(" not in source


def test_import_is_side_effect_free_and_env_independent(tmp_path: Path) -> None:
    """The registry must import with an empty environment and print nothing:
    no settings, no logging, no database, no I/O at import time."""
    config_path = Path(game_config.__file__).resolve()
    # Loaded by file location (not via the package) so the probe measures this
    # module alone; registering it in sys.modules first is the stock importlib
    # recipe, and `dataclasses(slots=True)` needs it to resolve the class module.
    probe = (
        "import importlib.util as util, sys\n"
        "from fractions import Fraction\n"
        "spec = util.spec_from_file_location('gc_probe', sys.argv[1])\n"
        "mod = util.module_from_spec(spec)\n"
        "sys.modules['gc_probe'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "assert mod.ANSWER_RATE_LIMIT == 120\n"
        "assert mod.PENALTY_FACTOR == Fraction(1, 2)\n"
        "assert len(mod.UNLOCK_SEED) == 16\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(config_path)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"clean import failed:\n{proc.stderr}"
    assert proc.stdout == "", proc.stdout
    assert proc.stderr == "", proc.stderr


def test_registry_never_touches_the_db_or_settings() -> None:
    """Static guard paired with the import probe above: the module imports only
    stdlib, so it stays usable from migrations and contract tests."""
    source = Path(game_config.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "src.config", "get_settings", "src.database"):
        assert forbidden not in source, forbidden


def test_documentation_cross_references_the_plan() -> None:
    """US-25 AC: every constant traces to a plan table, and the docstring points
    at docs/xp-economy.md (US-40) + the normative Section 4."""
    source = Path(game_config.__file__).read_text(encoding="utf-8")
    assert "docs/xp-economy.md" in source
    assert "docs/sprint4-plan.md" in source
    for section in ("§4.2", "§4.3", "§4.4", "§4.5", "§4.7", "§4.9"):
        assert section in source, section
    for decision in ("D-2", "D-3", "D-5", "D-7", "D-8"):
        assert decision in source, decision


def test_module_has_no_runtime_dependencies_on_its_own_helpers() -> None:
    """Sanity: the mode Literal and the tuple agree with the table keys, so a
    downstream consumer can iterate either one (US-26/US-32)."""
    literal_modes: tuple[GameMode, ...] = _literal_members()
    assert set(literal_modes) == set(BASE_XP) == set(MODE_MULTIPLIERS) == set(MODES)
