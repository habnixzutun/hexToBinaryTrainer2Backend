"""US-30 (RED layer): tests for the pure level progression.

Target module: ``src/services/level_service.py`` — pure functions, stdlib +
``src.services.game_config`` / ``src.services.xp_engine`` only, no DB, no
framework (US-30 spec, plan §5).

Specification: ``docs/sprint4-plan.md`` **Section 4.8, LV-01..LV-10**, derived
from D-1 (the curve, the span semantics and the ``cum(level)`` XP floor), D-5
(the penalty side of the floor) and D-8 (integer-exact arithmetic, multiples of
4). The curve function itself, ``xp_required_for_level``, is US-29's
``src/services/xp_engine.py``; this file pins its *values* because LV-01..LV-06
are level tests, and imports it from there.

API under test:

* ``cumulative_xp_for_level(level) -> int`` — ``cum(1) == 0``,
  ``cum(L) == sum(req(1..L-1))`` (D-1 row 2)
* ``level_from_xp(total_xp) -> int`` — highest L with ``cum(L) <= total_xp``
* ``xp_progress(total_xp) -> tuple[int, int]`` —
  ``(xp_in_level, xp_required_for_next_level)``
* ``clamped_total_xp(total_xp, penalty) -> int`` — the floor seam of D-1/D-5:
  derive the level from ``total_xp`` and return
  ``max(cum(level), total_xp - penalty)``. The plan places the clamp "in the
  answer transaction"; here it is a pure, single-sourced function that US-33
  calls, so LV-08/LV-09/LV-10 are testable without a database. It must never
  allow a level decrease.

Expected values are **literals copied from the plan's D-1 table**, never derived
from the module under test. LV-06 additionally checks the implementation against
an *in-test* exact-``Fraction`` oracle, so the drift guard of D-1 ("floats must
never touch the XP/level path") bites above the threshold n >= 84 as well.

No database and no fixtures: this file imports service modules only.
"""

from __future__ import annotations

import inspect
from fractions import Fraction

import pytest
from src.services import level_service
from src.services.game_config import MODES, VALID_WIDTHS_BY_MODE
from src.services.level_service import (
    clamped_total_xp,
    cumulative_xp_for_level,
    level_from_xp,
    xp_progress,
)
from src.services.xp_engine import xp_penalty_for_incorrect, xp_required_for_level

# ---------------------------------------------------------------------------
# Expected values — literals from docs/sprint4-plan.md (never import-side)
# ---------------------------------------------------------------------------

# D-1 table, row 1 — ``req(n)``, the span n -> n+1, for n = 1..15.
EXPECTED_REQ_1_15 = (
    100,  # n=1
    152,  # n=2  (raw 150 — the only exact tie of the curve, rounds UP)
    224,  # n=3  (raw 225)
    336,  # n=4  (raw 337.5)
    508,
    760,
    1140,
    1708,
    2564,
    3844,
    5768,
    8648,
    12976,
    19460,
    29192,
)

# D-1 table, row 2 — cumulative XP needed to REACH a level. ``cum(1) == 0``; the
# plan lists "cum to reach L(n+1)" for n = 1..15, i.e. cum(2)..cum(16).
EXPECTED_CUM = {
    1: 0,
    2: 100,
    3: 252,
    4: 476,
    5: 812,
    6: 1320,
    7: 2080,
    8: 3220,
    9: 4928,
    10: 7492,
    11: 11336,
    12: 17104,
    13: 25752,
    14: 38728,
    15: 58188,
    16: 87380,
}

# §4.8 LV-05 — ``total_xp -> level`` at and around the boundaries.
LV05_ROWS = (
    (0, 1),
    (99, 1),
    (100, 2),
    (251, 2),
    (252, 3),
    (475, 3),
    (476, 4),
    (25751, 12),
    (25752, 13),
)

# §4.8 LV-10 / §4.6 XP-G — ``xp_progress`` on the LV-05 rows:
# (total_xp, level, xp_in_level, xp_required_for_next_level).
LV10_PROGRESS_ROWS = (
    (0, 1, 0, 100),
    (99, 1, 99, 100),
    (100, 2, 0, 152),
    (251, 2, 151, 152),
    (252, 3, 0, 224),
    (475, 3, 223, 224),
    (476, 4, 0, 336),
    (25751, 12, 8647, 8648),
    (25752, 13, 0, 12976),
)

# §4.8 LV-06 (D-1 drift threshold, Rev 3 / R-5): the first n at which a float
# implementation really returns a different ``req(n)`` is **84** — exact
# 41,264,301,843,800,316 vs float 41,264,301,843,800,312. n = 20 and n = 50 are
# identical under float and exact arithmetic, so they guard nothing and are not
# used as anchors here (they do appear in the generic 1..100 oracle sweep).
LV06_DRIFT_PROBES = (
    (84, 41264301843800316),
    (100, 27104078502347682492),
    (200, 11019466071921386867706683957038067412),
)

# A ``4 * round(x / 4)`` implementation — Python's round() is half-to-EVEN —
# already diverges one tier earlier, at n = 74 (exact 715,586,124,880,212 vs
# 715,586,124,880,208). That is the banker's-rounding artifact, not float drift;
# n = 84 stays the drift anchor, and n = 74 pins the rounding *mode*.
LV04_BANKERS_PROBE = (74, 715586124880212)

# §4.7 — positive penalty magnitudes, literals again (deliberately duplicated
# per test module: a golden test must not share its expectation with another
# test file, or one wrong table would silence both).
EXPECTED_PENALTIES = {
    "hex_to_bin": {4: 8, 8: 16, 16: 32, 32: 64},
    "hex_to_dec": {4: 24, 8: 48, 16: 96, 32: 192},
    "bin_to_dec": {4: 64, 8: 128, 16: 256, 32: 512},
    "unicode": {8: 8, 16: 16, 24: 32, 32: 64},
}
COMBOS = tuple((mode, w) for mode in MODES for w in VALID_WIDTHS_BY_MODE[mode])
COMBO_IDS = tuple(f"{mode}-{w}" for mode, w in COMBOS)

# LV-08 — the levels the demotion sweep walks. 20 and 30 are deliberately above
# the unlock caps (D-2: 13 / 4) to prove the level curve itself is unlimited.
LV08_LEVELS = (1, 2, 3, 4, 5, 8, 12, 13, 14, 15, 20, 30)

# US-30 target API (names + arity), pinned so US-33/US-35 can bind by name.
US30_API_ARITY = {
    "cumulative_xp_for_level": 1,
    "level_from_xp": 1,
    "xp_progress": 1,
    "clamped_total_xp": 2,
}


def _reference_req(n: int) -> int:
    """D-1 as an independent in-test oracle, exact rational arithmetic only.

    Round-half-UP to the nearest multiple of 4 via the integer primitive
    ``(2 * num + den) // (2 * den)`` — deliberately *not* the builtin ``round()``,
    which is half-to-even and would return 148 for the raw 150 at n = 2.
    """
    quarter = Fraction(100, 1) * Fraction(3, 2) ** (n - 1) / 4
    rounded = (2 * quarter.numerator + quarter.denominator) // (2 * quarter.denominator)
    return 4 * rounded


def _reference_cum(level: int) -> int:
    """D-1 row 2 as an independent oracle: ``sum(req(1..level-1))``."""
    assert level >= 1, level
    return sum(_reference_req(n) for n in range(1, level))


def _reference_level_for(total_xp: int) -> int:
    """LV-05 as an oracle: the largest ``L`` with ``cum(L) <= total_xp``."""
    assert total_xp >= 0, total_xp
    level = 1
    while _reference_cum(level + 1) <= total_xp:
        level += 1
    return level


def _reference_floor_for(total_xp: int) -> int:
    """The XP-floor decision (PO's third option, "kein Level-Verlust"): the floor
    of the level the total sits in is ``cum(level)``, never 0 above level 1. The
    clamp is ``max(total_xp - penalty, floor)`` and nothing else: no rounding, no
    snapping to the 4z grid."""
    return _reference_cum(_reference_level_for(total_xp))


def _sample_positions(level: int) -> tuple[int, ...]:
    """Four in-range probes for ``[cum(L), cum(L+1))``, all multiples of 4."""
    floor = _reference_cum(level)
    ceiling = _reference_cum(level + 1)
    middle = ((floor + ceiling) // 2) // 4 * 4
    return tuple(dict.fromkeys((floor, floor + 4, middle, ceiling - 4)))


# ---------------------------------------------------------------------------
# Oracle self-check — the in-test reference must be the plan's curve
# ---------------------------------------------------------------------------


def test_fraction_oracle_reproduces_the_plan_curve() -> None:
    """The oracle every generic sweep uses is the D-1 table itself.

    Without this, a wrong oracle would make LV-02/LV-03/LV-06 unfalsifiable.
    """
    assert tuple(_reference_req(n) for n in range(1, 16)) == EXPECTED_REQ_1_15
    assert all(_reference_cum(level) == EXPECTED_CUM[level] for level in range(1, 17))
    # Every span is a multiple of 4 (D-8), so every floor is too.
    assert all(_reference_req(n) % 4 == 0 for n in range(1, 101))
    assert all(_reference_cum(level) % 4 == 0 for level in range(1, 32))


# ---------------------------------------------------------------------------
# §4.8 LV-01 — the curve and its cumulative row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", range(15))
def test_req_matches_the_d_1_table(index: int) -> None:
    """LV-01: ``req(n)`` equals the D-1 table for every n = 1..15."""
    n = index + 1
    required = xp_required_for_level(n)
    assert required == EXPECTED_REQ_1_15[index]
    assert type(required) is int


@pytest.mark.parametrize("level", range(1, 17))
def test_cumulative_xp_matches_the_d_1_cumulative_row(level: int) -> None:
    """LV-01 row 2: ``cum(L)`` is the XP needed to reach level L, ``cum(1) == 0``."""
    assert cumulative_xp_for_level(level) == EXPECTED_CUM[level]


@pytest.mark.parametrize("level", range(2, 21))
def test_cumulative_xp_is_the_sum_of_the_spans(level: int) -> None:
    """D-1: ``cum`` is the sum of ``req(1..L-1)`` — the two rows of the table are
    one arithmetic object, so they may never drift apart. The relation is also
    pinned to the oracle, so an engine that returns the same constant everywhere
    cannot satisfy it by making both sides equal."""
    spans = sum(xp_required_for_level(n) for n in range(1, level))
    assert spans == _reference_cum(level), level
    assert cumulative_xp_for_level(level) == spans
    assert cumulative_xp_for_level(level) == _reference_cum(level), level


def test_player_starts_at_level_one_with_zero_xp() -> None:
    """D-1: "Player starts at level 1 with 0 XP" — ``cum(1) == 0`` is also the
    floor of a fresh profile (LV-09 d)."""
    assert cumulative_xp_for_level(1) == 0
    assert level_from_xp(0) == 1
    assert xp_progress(0) == (0, 100)


# ---------------------------------------------------------------------------
# §4.8 LV-02 / LV-03 — integer-exact and strictly increasing through n = 100
# ---------------------------------------------------------------------------


def test_req_is_an_integer_multiple_of_4_through_level_100() -> None:
    """LV-02 / D-8: for n = 1..100 every ``req(n)`` is a plain int in 4z."""
    for n in range(1, 101):
        required = xp_required_for_level(n)
        assert type(required) is int, (n, type(required))
        assert required % 4 == 0, n
        assert required > 0, n


def test_req_strictly_increases_through_level_100() -> None:
    """LV-03: for n = 1..100 the curve is strictly increasing, so a level can
    never be reachable before the previous one."""
    previous = 0
    for n in range(1, 101):
        required = xp_required_for_level(n)
        assert required > previous, n
        previous = required


# ---------------------------------------------------------------------------
# §4.8 LV-04 — the tie rule: exact halves round UP (only n = 2 fires)
# ---------------------------------------------------------------------------


def test_tie_at_n_2_rounds_half_up_to_152() -> None:
    """LV-04 / D-1: raw ``100 * 1.5 = 150`` sits exactly between 148 and 152, and
    the tie rule sends it up. The builtin ``round()`` (half-to-even) would also
    land on 152 here, so this row pins the *value*, not the primitive."""
    assert Fraction(100, 1) * Fraction(3, 2) == 150  # the raw value is a tie
    assert 150 % 4 == 2  # exactly half-way between two multiples of 4
    assert xp_required_for_level(2) == 152
    assert xp_required_for_level(2) % 4 == 0


@pytest.mark.parametrize(
    ("n", "raw_numerator", "raw_denominator", "expected"),
    [
        (1, 100, 1, 100),
        (3, 225, 1, 224),  # raw 225 -> 224, NOT 228: no upward inflation
        (4, 675, 2, 336),  # raw 337.5 -> 336 (a tie-free .5 of the raw value)
        (5, 2025, 4, 508),  # raw 506.25 -> 508
    ],
    ids=["n1-raw-100", "n3-raw-225", "n4-raw-337-5", "n5-raw-506-25"],
)
def test_rounding_never_inflates_away_from_the_nearest_multiple(
    n: int, raw_numerator: int, raw_denominator: int, expected: int
) -> None:
    """LV-04 companion: "round to the nearest multiple of 4" — the raw rational is
    pinned too, so neither ceil-style inflation nor truncation can masquerade as
    the tie rule."""
    raw = Fraction(raw_numerator, raw_denominator)
    assert Fraction(100, 1) * Fraction(3, 2) ** (n - 1) == raw
    required = xp_required_for_level(n)
    assert required == expected
    assert abs(required - raw) <= 2  # nearest multiple of 4 is within 2


# ---------------------------------------------------------------------------
# §4.8 LV-05 / LV-07 — total_xp -> level, boundaries inclusive upward
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("total_xp", "level"), LV05_ROWS)
def test_level_from_xp_matches_the_lv05_mapping(total_xp: int, level: int) -> None:
    """LV-05: the nine plan rows, including the two unlock-relevant thresholds
    ``476 -> L4`` (D-2 pacing) and ``25752 -> L13``."""
    assert level_from_xp(total_xp) == level


@pytest.mark.parametrize("level", range(1, 16))
def test_level_boundary_is_inclusive_at_the_top_and_exclusive_below(
    level: int,
) -> None:
    """LV-07: exactly at ``cum(L)`` the player already IS level L; one multiple of
    4 below, the player is still level L-1. All totals are in 4z (D-8), so the
    last reachable XP of a level is ``cum(L+1) - 4``."""
    floor = EXPECTED_CUM[level]
    ceiling = EXPECTED_CUM[level + 1]
    assert level_from_xp(floor) == level
    assert level_from_xp(ceiling) == level + 1
    assert level_from_xp(ceiling - 4) == level
    assert level_from_xp(ceiling - 1) == level  # non-4z value, still level L


# ---------------------------------------------------------------------------
# §4.8 LV-06 — unlimited levels + the float-drift guard above n >= 84
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("n", "expected"), LV06_DRIFT_PROBES)
def test_req_survives_the_float_drift_threshold(n: int, expected: int) -> None:
    """LV-06 / D-1 (Rev 3 R-5): n = 84 is the first genuine float drift point, so
    it — not 20 or 50 — is the regression anchor. 100 and 200 keep the guard
    honest further out. Each value is checked against a literal AND against the
    in-test exact-``Fraction`` oracle."""
    required = xp_required_for_level(n)
    assert required == expected
    assert required == _reference_req(n)
    assert type(required) is int


def test_curve_matches_the_fraction_oracle_for_n_1_to_100() -> None:
    """LV-06 generic sweep: the whole 1..100 range equals the exact-rational
    oracle, so any float participation fails somewhere in the range."""
    for n in range(1, 101):
        assert xp_required_for_level(n) == _reference_req(n), n


def test_req_at_n_74_pins_the_rounding_mode() -> None:
    """LV-06 companion: ``4 * round(x / 4)`` (half-to-even) diverges at n = 74
    while the mandated half-up primitive does not, so this row separates "float
    drift" from "wrong tie rule" — both are D-8 violations."""
    n, expected = LV04_BANKERS_PROBE
    assert xp_required_for_level(n) == expected
    assert expected == _reference_req(n)


def test_levels_are_unlimited_above_the_unlock_caps() -> None:
    """LV-06 / D-1: "Unlimited levels — no cap at 13" (the cap binds unlocks only,
    D-2). The curve keeps computing and the level keeps climbing past 13/4."""
    assert xp_required_for_level(1000) > 0
    assert type(xp_required_for_level(1000)) is int
    assert level_from_xp(EXPECTED_CUM[16]) == 16
    for level in (20, 30, 100):
        floor = _reference_cum(level)
        assert level_from_xp(floor) == level
        assert level_from_xp(floor + xp_required_for_level(level)) == level + 1


# ---------------------------------------------------------------------------
# §4.8 LV-08 — demotion is impossible (floor is cum(level), D-1/D-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_no_penalty_can_demote_a_player(mode: str, width: int) -> None:
    """LV-08: for every level and every §4.7 penalty, applying the penalty to a
    profile anywhere in ``[cum(L), cum(L+1))`` leaves the level unchanged. The
    guarantee comes from the ``cum(level)`` floor, not from sticky state."""
    penalty = EXPECTED_PENALTIES[mode][width]
    assert xp_penalty_for_incorrect(mode, width) == penalty  # §4.7 literals
    for level in LV08_LEVELS:
        floor = _reference_cum(level)
        for position in _sample_positions(level):
            clamped = clamped_total_xp(position, penalty)
            assert level_from_xp(clamped) == level, (level, position, penalty)
            assert floor <= clamped <= position, (level, position, penalty)
            assert (position - clamped) % 4 == 0, (level, position, penalty)


# ---------------------------------------------------------------------------
# §4.8 LV-09 — the floor clamp, case by case
# ---------------------------------------------------------------------------


def test_clamp_applies_the_full_penalty_when_the_margin_is_comfortable() -> None:
    """LV-09 (a): far above the floor, the whole penalty is taken."""
    # level 5: cum(5) = 812, cum(6) = 1320. 1312 is inside [812, 1320).
    clamped = clamped_total_xp(1312, 64)
    assert clamped == 1248
    assert clamped - 1312 == -64
    assert level_from_xp(clamped) == 5


def test_clamp_yields_zero_delta_exactly_on_the_floor() -> None:
    """LV-09 (b): a player sitting exactly on ``cum(level)`` is immune to the
    penalty — ``xp_delta == 0`` and the level is untouched. (The accompanying
    ``current_streak = 0`` reset is US-28/US-33 state, not part of this seam.)"""
    floor = EXPECTED_CUM[5]  # 812
    clamped = clamped_total_xp(floor, 512)
    assert clamped == floor
    assert clamped - floor == 0
    assert level_from_xp(clamped) == 5


def test_clamp_floors_at_cum_level_and_reports_the_partial_delta() -> None:
    """LV-09 (c): ``cum(level) + 4`` with a 64 penalty clamps to ``cum(level)``,
    so the applied delta is -4, not -64."""
    floor = EXPECTED_CUM[5]  # 812
    total = floor + 4
    clamped = clamped_total_xp(total, 64)
    assert clamped == floor
    assert clamped - total == -4
    assert level_from_xp(clamped) == 5


def test_clamp_at_level_one_floors_at_zero() -> None:
    """LV-09 (d): ``cum(1) == 0``, so a fresh profile floors at 0 exactly as the
    legacy "floor at 0" rule did."""
    assert clamped_total_xp(0, 8) == 0
    assert clamped_total_xp(4, 64) == 0
    assert clamped_total_xp(99, 512) == 0
    assert level_from_xp(clamped_total_xp(0, 512)) == 1


@pytest.mark.parametrize(
    ("total_xp", "penalty"),
    [(1312, 64), (812, 512), (816, 64), (0, 8), (4, 512), (25752, 256)],
    ids=[
        "margin",
        "on-floor",
        "four-above-floor",
        "fresh-0",
        "fresh-4",
        "L13",
    ],
)
def test_clamped_delta_is_always_a_multiple_of_4(total_xp: int, penalty: int) -> None:
    """LV-09 / D-8: on a reachable total (D-8 keeps every total in 4z) the clamp
    keeps both the new total and ``xp_delta`` in 4z. Pinned to the whole
    floor formula and not only to the residue, so an identity clamp cannot
    satisfy it."""
    clamped = clamped_total_xp(total_xp, penalty)
    expected = max(total_xp - penalty, _reference_floor_for(total_xp))
    assert clamped == expected, (total_xp, penalty)
    assert clamped % 4 == 0, (total_xp, penalty)
    assert (clamped - total_xp) % 4 == 0, (total_xp, penalty)
    assert type(clamped) is int


def test_clamp_applies_the_raw_formula_to_a_total_outside_the_4z_grid() -> None:
    """LV-09 boundary, stated precisely because D-8's "totals stay in 4z" is
    conditional (plan §3: "*because* the floor is a multiple of 4 **and
    ``total_xp`` is one*").  A migrated or legacy total of 99 loses the penalty as
    plain arithmetic: the result is 35, NOT snapped to 32 or 36 — and ``xp_delta``
    is still -64, a multiple of 4, which is the residue LV-09 actually asserts.
    """
    clamped = clamped_total_xp(99, 64)
    assert clamped == 35
    assert clamped - 99 == -64
    assert (clamped - 99) % 4 == 0
    assert clamped % 4 != 0  # documents the absence of any silent rounding
    assert clamped_total_xp(99, 512) == 0  # floors at cum(1) == 0 like any L1 total


# ---------------------------------------------------------------------------
# §4.8 LV-10 — xp_progress never negative, including after a clamped penalty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total_xp", "level", "in_level", "required"), LV10_PROGRESS_ROWS
)
def test_xp_progress_reports_in_level_and_span(
    total_xp: int, level: int, in_level: int, required: int
) -> None:
    """LV-10 / US-30: ``(xp_in_level, xp_required_for_next_level)`` — the pair
    US-33 puts on the wire and US-38 renders as a progress bar."""
    progress = xp_progress(total_xp)
    assert progress == (in_level, required)
    assert level_from_xp(total_xp) == level
    assert in_level == total_xp - cumulative_xp_for_level(level)
    assert required == xp_required_for_level(level)


def test_xp_progress_never_returns_a_negative_in_level_value() -> None:
    """LV-10: the in-level XP is never negative — not at a floor, not directly
    after a clamped penalty (follows from LV-09, pinned separately because it is
    the value the HUD renders)."""
    for level in LV08_LEVELS:
        for position in _sample_positions(level):
            for mode, width in COMBOS:
                clamped = clamped_total_xp(position, EXPECTED_PENALTIES[mode][width])
                in_level, required = xp_progress(clamped)
                assert in_level >= 0, (level, position, mode, width)
                assert 0 <= in_level <= required, (level, position, mode, width)
                assert level_from_xp(clamped) == level


def test_xp_progress_returns_a_pair_of_plain_ints() -> None:
    """D-8 / US-30: the wire pair is two plain ints — no Fraction, no float, and
    exactly two elements."""
    for total_xp, _level, _in_level, _required in LV10_PROGRESS_ROWS:
        progress = xp_progress(total_xp)
        assert type(progress) is tuple
        assert len(progress) == 2
        assert all(type(value) is int for value in progress)


def test_level_from_xp_returns_a_plain_int() -> None:
    """D-8: the level itself is a plain int (a bool would silently pass an
    ``==`` check against 1/0, so the type is asserted exactly)."""
    for total_xp, level in LV05_ROWS:
        derived = level_from_xp(total_xp)
        assert derived == level
        assert type(derived) is int


def test_level_service_exposes_the_four_us30_callables() -> None:
    """US-30 target API: the four public callables exist with the documented
    arity, so US-33/US-35 can bind them by name."""
    for name, arity in US30_API_ARITY.items():
        function = getattr(level_service, name)
        assert callable(function), name
        assert len(inspect.signature(function).parameters) == arity, name
