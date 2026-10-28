"""US-31 (RED layer): tests for the pure unlock system.

Target module: ``src/services/unlock_service.py`` — pure functions derived from
the frozen ``UNLOCK_SEED`` of US-25, stdlib + ``src.services.game_config`` only,
no DB, no framework (US-31 spec, plan §5).

Specification: ``docs/sprint4-plan.md`` **Section 4.9, UN-01..UN-07**, derived
from D-2 (the 16-row seed and the binding PO caps 13 / 4), D-7 (``bit_width`` is
BITS in every mode, so unicode is 8/16/24/32 and ``("unicode", 4)`` is not a
combo at all) and D-1/D-5 (the ``cum(level)`` XP floor that makes a level — and
therefore an unlock — irreversible).

API under test:

* ``unlocks_for_level(level) -> tuple[UnlockRule, ...]``
* ``newly_unlocked(level_before, level_after) -> tuple[UnlockRule, ...]`` — the
  delta set US-33 puts in its response
* ``is_unlocked(mode, bit_width, level) -> bool`` — a **total** predicate: an
  invalid ``(mode, width)`` answers ``False`` rather than raising, because a
  client bug must never be scored or silently accepted (§4.9 UN-06)
* ``required_level_for(mode, bit_width) -> int`` — the **partial** function that
  feeds the 403 detail of §4.9 UN-05 ("Content locked until level X", US-31
  decision); it raises ``ValueError`` for an invalid combo, so that case is a 422
  from US-32 upstream and never a 403 or a 200

The *shape* of the matrices is taken from the registry (``UNLOCK_SEED``,
``MODES``, ``VALID_WIDTHS_BY_MODE``) so a shrinking seed cannot hide, while every
asserted value is a **literal copied from the D-2 table** — a golden test that
would read its expectation back out of the registry could not catch a wrong
registry.

UN-07 ("unlocks are never revoked") needs the XP floor, so this file also drives
US-30's ``clamped_total_xp``: the replay is pure and database-free, which is the
whole point of placing the clamp in ``level_service`` instead of only in the
answer transaction. The static proof that no sticky-state column can exist (no
DB/framework import in the pure layer) lives in the AST scans of
``tests/test_xp_engine.py`` (XP-TP9).

No database and no fixtures: this file imports service modules only.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError

import pytest
from src.services import unlock_service
from src.services.game_config import (
    MODES,
    UNLOCK_SEED,
    VALID_WIDTHS_BY_MODE,
    UnlockRule,
)
from src.services.level_service import (
    clamped_total_xp,
    cumulative_xp_for_level,
    level_from_xp,
)
from src.services.unlock_service import (
    is_unlocked,
    newly_unlocked,
    required_level_for,
    unlocks_for_level,
)
from src.services.xp_engine import xp_penalty_for_incorrect

# ---------------------------------------------------------------------------
# Expected values — literals from docs/sprint4-plan.md D-2 / §4.9
# ---------------------------------------------------------------------------

# D-2 — the 16 seed rows in plan-table order: (mode, bit_width, min_level).
EXPECTED_UNLOCK_SEED = (
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
)

CONVERSION_MODES = ("hex_to_bin", "hex_to_dec", "bin_to_dec")

# D-2 — binding PO caps, as literals (UN-01).
CONVERSION_CAP = 13
UNICODE_CAP = 4

# D-2 — required level per combo, the 403-detail payload of UN-05.
EXPECTED_REQUIRED_LEVEL = {
    ("hex_to_bin", 4): 1,
    ("hex_to_bin", 8): 4,
    ("hex_to_bin", 16): 8,
    ("hex_to_bin", 32): 13,
    ("hex_to_dec", 4): 1,
    ("hex_to_dec", 8): 4,
    ("hex_to_dec", 16): 8,
    ("hex_to_dec", 32): 13,
    ("bin_to_dec", 4): 1,
    ("bin_to_dec", 8): 4,
    ("bin_to_dec", 16): 8,
    ("bin_to_dec", 32): 13,
    ("unicode", 8): 1,
    ("unicode", 16): 2,
    ("unicode", 24): 3,
    ("unicode", 32): 4,
}

# §4.9 UN-02 — level 1 unlocks exactly the four slot-1 rules (unicode's slot 1 is
# 8 BITS, not 4).
EXPECTED_SLOT_ONE_RULES = frozenset(
    {
        ("hex_to_bin", 4, 1),
        ("hex_to_dec", 4, 1),
        ("bin_to_dec", 4, 1),
        ("unicode", 8, 1),
    }
)

# §4.9 UN-02/UN-04 — how many rules are open at each level (1..16): nothing new
# between L5-L7 and L9-L12, and nothing at all above L13.
EXPECTED_UNLOCK_COUNTS = {
    1: 4,
    2: 5,
    3: 6,
    4: 10,
    5: 10,
    6: 10,
    7: 10,
    8: 13,
    9: 13,
    10: 13,
    11: 13,
    12: 13,
    13: 16,
    14: 16,
    15: 16,
    16: 16,
}

# §4.9 UN-03 — golden delta sets for the transitions US-33 announces.
EXPECTED_NEWLY_UNLOCKED = (
    (
        (1, 1),
        frozenset(),
    ),
    (
        (1, 4),
        frozenset(
            {
                ("unicode", 16, 2),
                ("unicode", 24, 3),
                ("hex_to_bin", 8, 4),
                ("hex_to_dec", 8, 4),
                ("bin_to_dec", 8, 4),
                ("unicode", 32, 4),
            }
        ),
    ),
    ((2, 3), frozenset({("unicode", 24, 3)})),
    (
        (4, 8),
        frozenset(
            {("hex_to_bin", 16, 8), ("hex_to_dec", 16, 8), ("bin_to_dec", 16, 8)}
        ),
    ),
    (
        (12, 13),
        frozenset(
            {("hex_to_bin", 32, 13), ("hex_to_dec", 32, 13), ("bin_to_dec", 32, 13)}
        ),
    ),
    ((13, 20), frozenset()),
    ((4, 4), frozenset()),
    ((30, 31), frozenset()),
)

# The 12-row literal for the full climb L1 -> L13 (everything but slot 1).
EXPECTED_NEWLY_UNLOCKED_L1_TO_L13 = (
    frozenset(EXPECTED_UNLOCK_SEED) - EXPECTED_SLOT_ONE_RULES
)

# §4.9 UN-06 / D-7 — combos that are not combos: invalid width for the mode, or
# an unknown mode. ``is_unlocked`` says False, ``required_level_for`` raises.
INVALID_WIDTH_PROBES = (
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
INVALID_MODE_PROBES = (
    ("unregistered", 4),
    ("hex_to_binary", 8),
    ("HEX_TO_BIN", 4),
    ("", 4),
    ("unicode_to_bin", 16),
)
INVALID_COMBO_PROBES = INVALID_WIDTH_PROBES + INVALID_MODE_PROBES
INVALID_COMBO_IDS = tuple(
    f"{mode or 'empty'}-{width}" for mode, width in INVALID_COMBO_PROBES
)

# §4.9 UN-05 — locked (mode, width, level) samples with the level the 403 detail
# has to name.
LOCKED_SAMPLES = (
    ("bin_to_dec", 32, 5, 13),
    ("hex_to_bin", 16, 7, 8),
    ("unicode", 32, 2, 4),
    ("hex_to_dec", 8, 1, 4),
)

# UN-07 — levels whose unlocks are replayed against a clamp to the floor.
UN07_LEVELS = (4, 8, 13)

COMBOS = tuple((mode, w) for mode in MODES for w in VALID_WIDTHS_BY_MODE[mode])
COMBO_IDS = tuple(f"{mode}-{w}" for mode, w in COMBOS)

# US-31 target API (names + arity), pinned so US-33/US-35/US-39 can bind by name.
US31_API_ARITY = {
    "unlocks_for_level": 1,
    "newly_unlocked": 2,
    "is_unlocked": 3,
    "required_level_for": 2,
}


def _triples(rules: tuple[UnlockRule, ...] | frozenset[UnlockRule]) -> frozenset:
    """Project the returned rules onto comparable ``(mode, width, level)`` tuples."""
    return frozenset((rule.mode, rule.bit_width, rule.min_level) for rule in rules)


def _expected_unlock_set(level: int) -> frozenset:
    """The D-2 literal seed filtered by ``min_level <= level`` — the oracle every
    set-level assertion uses (plan table, not module)."""
    return frozenset(
        (mode, width, min_level)
        for mode, width, min_level in EXPECTED_UNLOCK_SEED
        if min_level <= level
    )


# ---------------------------------------------------------------------------
# Seed completeness (D-2) — the domain the service is derived from
# ---------------------------------------------------------------------------


def test_seed_covers_every_combo_exactly_once() -> None:
    """D-2: 16 rows, one per valid ``(mode, bit_width)``, so ``required_level_for``
    is total on the whole engine domain and no combo is doubled."""
    assert len(UNLOCK_SEED) == 16
    keys = [(rule.mode, rule.bit_width) for rule in UNLOCK_SEED]
    assert len(set(keys)) == 16
    assert set(keys) == set(COMBOS)
    assert [(r.mode, r.bit_width, r.min_level) for r in UNLOCK_SEED] == list(
        EXPECTED_UNLOCK_SEED
    )


def test_unlock_service_exposes_the_four_us31_callables() -> None:
    """US-31 target API: the four public callables exist with the documented
    arity, so US-33/US-35/US-39 can bind them by name."""
    for name, arity in US31_API_ARITY.items():
        function = getattr(unlock_service, name)
        assert callable(function), name
        assert len(inspect.signature(function).parameters) == arity, name


def test_returns_frozen_unlock_rules_from_the_registry_type() -> None:
    """D-2 immutability: the service hands back the registry's frozen
    ``UnlockRule`` values — a caller cannot mutate shared economy state, and no
    per-user unlock object exists anywhere."""
    rules = unlocks_for_level(13)
    assert type(rules) is tuple
    assert len(rules) == 16
    for rule in rules:
        assert type(rule) is UnlockRule, rule
    with pytest.raises(FrozenInstanceError):
        rules[0].min_level = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §4.9 UN-01 — binding PO caps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", UNLOCK_SEED, ids=COMBO_IDS)
def test_no_unlock_is_above_its_track_cap(rule: UnlockRule) -> None:
    """UN-01: no conversion unlock above L13, no unicode unlock above L4 — the
    caps are PO-binding, and above them XP climbs only."""
    cap = UNICODE_CAP if rule.mode == "unicode" else CONVERSION_CAP
    assert rule.min_level <= cap, rule
    assert is_unlocked(rule.mode, rule.bit_width, cap) is True, rule


def test_caps_are_reached_exactly_not_left_slack() -> None:
    """UN-01: the caps bind — the ladder really tops out at 13 / 4 (otherwise the
    seed would be shorter than the plan allows and the pacing of D-2 would lie)."""
    conversion_levels = [
        rule.min_level for rule in UNLOCK_SEED if rule.mode in CONVERSION_MODES
    ]
    unicode_levels = [rule.min_level for rule in UNLOCK_SEED if rule.mode == "unicode"]
    assert max(conversion_levels) == CONVERSION_CAP
    assert max(unicode_levels) == UNICODE_CAP
    assert sorted(conversion_levels) == sorted([1, 4, 8, 13] * 3)
    assert sorted(unicode_levels) == [1, 2, 3, 4]


@pytest.mark.parametrize("mode", CONVERSION_MODES)
def test_conversion_ladder_is_1_4_8_13(mode: str) -> None:
    """D-2 (PO-confirmed ladder): conversion widths unlock at L1 / L4 / L8 / L13."""
    levels = sorted(rule.min_level for rule in UNLOCK_SEED if rule.mode == mode)
    assert levels == [1, 4, 8, 13]
    assert [required_level_for(mode, w) for w in VALID_WIDTHS_BY_MODE[mode]] == [
        1,
        4,
        8,
        13,
    ]


def test_unicode_ladder_is_1_2_3_4() -> None:
    """D-2: unicode unlocks one slot per level, L1..L4."""
    levels = sorted(rule.min_level for rule in UNLOCK_SEED if rule.mode == "unicode")
    assert levels == [1, 2, 3, 4]
    assert [
        required_level_for("unicode", w) for w in VALID_WIDTHS_BY_MODE["unicode"]
    ] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# §4.9 UN-02 — the level -> unlock-set function
# ---------------------------------------------------------------------------


def test_level_one_unlocks_exactly_the_four_slot_one_rules() -> None:
    """UN-02 first half: L1 = ``hex_to_bin``/``hex_to_dec``/``bin_to_dec`` at 4
    bits plus ``unicode`` at **8** bits (D-7: bits everywhere)."""
    unlocked = _triples(unlocks_for_level(1))
    assert unlocked == EXPECTED_SLOT_ONE_RULES
    assert len(unlocked) == 4
    assert ("unicode", 4, 1) not in unlocked


def test_level_thirteen_unlocks_all_sixteen_rules() -> None:
    """UN-02 second half: L13 is the top of the ladder — every seed row is open."""
    unlocked = _triples(unlocks_for_level(13))
    assert unlocked == frozenset(EXPECTED_UNLOCK_SEED)
    assert len(unlocked) == 16


@pytest.mark.parametrize("level", range(1, 17))
def test_unlocks_for_level_matches_the_seed_filter(level: int) -> None:
    """UN-02/UN-04: for every level of the plan table, the returned set is exactly
    the D-2 rows with ``min_level <= level``."""
    unlocked = _triples(unlocks_for_level(level))
    assert unlocked == _expected_unlock_set(level)
    assert len(unlocked) == EXPECTED_UNLOCK_COUNTS[level]


@pytest.mark.parametrize("level", (13, 14, 20, 100, 10**6))
def test_levels_above_the_cap_add_nothing(level: int) -> None:
    """D-2: above L13 there are no further unlocks at all — prestige only."""
    assert _triples(unlocks_for_level(level)) == frozenset(EXPECTED_UNLOCK_SEED)


@pytest.mark.parametrize("level", (0, -1, -100))
def test_unlocks_for_level_rejects_a_level_below_one(level: int) -> None:
    """D-1: levels start at 1, so a level below 1 is a caller bug — raise rather
    than answer with the L1 set (which would silently unlock content)."""
    with pytest.raises(ValueError):
        unlocks_for_level(level)


@pytest.mark.parametrize("level", (0, -1))
def test_predicates_reject_a_level_below_one(level: int) -> None:
    """Same domain rule for the other two level-taking entry points."""
    with pytest.raises(ValueError):
        is_unlocked("hex_to_bin", 4, level)
    with pytest.raises(ValueError):
        newly_unlocked(level, 5)
    with pytest.raises(ValueError):
        newly_unlocked(5, level)


# ---------------------------------------------------------------------------
# §4.9 UN-03 — the delta set US-33 announces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level_before", "level_after"),
    tuple(pair for pair, _ in EXPECTED_NEWLY_UNLOCKED),
    ids=tuple(
        f"L{before}-to-L{after}"
        for before, after in (pair for pair, _ in EXPECTED_NEWLY_UNLOCKED)
    ),
)
def test_newly_unlocked_returns_the_golden_delta_set(
    level_before: int, level_after: int
) -> None:
    """UN-03: exactly the rules that opened between the two levels — the payload
    of the US-33 response, with no already-open rule and no revoked rule in it."""
    expected = dict(EXPECTED_NEWLY_UNLOCKED)[(level_before, level_after)]
    delta = _triples(newly_unlocked(level_before, level_after))
    assert delta == expected
    assert delta <= _expected_unlock_set(level_after)
    assert delta.isdisjoint(_expected_unlock_set(level_before))


def test_newly_unlocked_covers_the_whole_ladder_from_level_one() -> None:
    """UN-03: L1 -> L13 opens the 12 non-slot-1 rows and nothing else."""
    delta = _triples(newly_unlocked(1, 13))
    assert delta == EXPECTED_NEWLY_UNLOCKED_L1_TO_L13
    assert len(delta) == 12


def test_newly_unlocked_equals_the_set_difference_for_a_grid() -> None:
    """UN-03 generic sweep: for every ordered pair in 1..16 the delta is
    ``set(after) - set(before)``, so the response can be built from either view."""
    for before in range(1, 17):
        for after in range(1, 17):
            delta = _triples(newly_unlocked(before, after))
            if after >= before:
                assert delta == (
                    _expected_unlock_set(after) - _expected_unlock_set(before)
                ), (before, after)
            else:
                assert delta == frozenset(), (before, after)


@pytest.mark.parametrize(
    ("level_before", "level_after"), ((4, 1), (13, 4), (8, 8), (20, 3))
)
def test_newly_unlocked_never_revokes_on_an_inverted_range(
    level_before: int, level_after: int
) -> None:
    """UN-03/UN-07: a demotion cannot occur (LV-08), so an inverted range is not a
    revoke list — it is empty. The entry point stays total so US-33 can call it
    without a guard clause."""
    assert newly_unlocked(level_before, level_after) == ()


def test_before_set_plus_delta_equals_after_set() -> None:
    """UN-03: the delta composes with the level view, so client and server can
    reconcile (D-2 "single source of truth")."""
    for before, after in ((1, 4), (4, 8), (8, 13), (1, 13), (2, 3)):
        before_set = _triples(unlocks_for_level(before))
        after_set = _triples(unlocks_for_level(after))
        delta = _triples(newly_unlocked(before, after))
        assert before_set | delta == after_set
        assert len(delta) + len(before_set) == len(after_set)


# ---------------------------------------------------------------------------
# §4.9 UN-04 — monotonicity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", range(1, 21))
def test_unlock_set_of_a_level_is_a_subset_of_the_next(level: int) -> None:
    """UN-04: ``unlocks(L) ⊆ unlocks(L+1)`` for every L — no content can ever
    close again, which is what lets D-2 drop the unlock-state table."""
    current = _triples(unlocks_for_level(level))
    following = _triples(unlocks_for_level(level + 1))
    assert current <= following
    assert len(following) >= len(current)


def test_unlock_count_grows_only_at_the_planned_levels() -> None:
    """UN-04: the count steps exactly at L2, L3, L4, L8 and L13 and is flat
    everywhere else (D-2 ladder 1/4/8/13 and 1/2/3/4)."""
    counts = [len(unlocks_for_level(level)) for level in range(1, 17)]
    assert counts == [EXPECTED_UNLOCK_COUNTS[level] for level in range(1, 17)]
    growth_points = [
        level
        for level in range(2, 17)
        if EXPECTED_UNLOCK_COUNTS[level] != EXPECTED_UNLOCK_COUNTS[level - 1]
    ]
    assert growth_points == [2, 3, 4, 8, 13]


# ---------------------------------------------------------------------------
# §4.9 UN-05 — the 403 seam: required level + is_unlocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_required_level_for_matches_the_seed(mode: str, width: int) -> None:
    """UN-05: the level the 403 detail names is the seed's ``min_level`` for the
    combo — one source, no second table."""
    required = required_level_for(mode, width)
    assert required == EXPECTED_REQUIRED_LEVEL[(mode, width)]
    assert type(required) is int


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_is_unlocked_flips_exactly_at_the_required_level(mode: str, width: int) -> None:
    """UN-05: locked strictly below ``min_level``, open at and above it. The
    predicate is a plain ``bool`` (a truthy int would break the 403 branch)."""
    required = EXPECTED_REQUIRED_LEVEL[(mode, width)]
    assert type(is_unlocked(mode, width, required)) is bool
    assert is_unlocked(mode, width, required) is True
    assert is_unlocked(mode, width, 10**6) is True
    if required > 1:
        assert is_unlocked(mode, width, required - 1) is False


@pytest.mark.parametrize(("mode", "width", "level", "required"), LOCKED_SAMPLES)
def test_locked_combo_reports_the_level_the_403_detail_must_name(
    mode: str, width: int, level: int, required: int
) -> None:
    """UN-05 (US-31 decision: **403** with detail naming the required level): the
    pure seam supplies both halves — "not unlocked at this level" and "the level
    that unlocks it" — so the router can build ``"Content locked until level X"``
    without touching a table of its own."""
    assert is_unlocked(mode, width, level) is False
    assert required_level_for(mode, width) == required


@pytest.mark.parametrize(("mode", "width"), COMBOS, ids=COMBO_IDS)
def test_predicate_and_level_view_agree_for_every_combo_and_level(
    mode: str, width: int
) -> None:
    """UN-05: ``is_unlocked`` is exactly membership in ``unlocks_for_level``, so
    the 403 path and the profile/lock-list path cannot disagree."""
    for level in (1, 2, 3, 4, 5, 8, 12, 13, 14, 30):
        expected = (mode, width) in {
            (rule.mode, rule.bit_width) for rule in unlocks_for_level(level)
        }
        assert is_unlocked(mode, width, level) is expected, (mode, width, level)


# ---------------------------------------------------------------------------
# §4.9 UN-06 — the seed speaks bits in every mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", (1, 4, 13, 50, 10**6))
def test_unicode_4bit_is_never_unlocked_at_any_level(level: int) -> None:
    """UN-06 (pins D-7): 4 is not a unicode width at all, so
    ``is_unlocked("unicode", 4, 13)`` is **False** — not a locked 403 candidate,
    not a silent 200: US-32 rejects the combo with a 422 before either is
    considered."""
    assert is_unlocked("unicode", 4, level) is False


@pytest.mark.parametrize(("mode", "width"), INVALID_COMBO_PROBES, ids=INVALID_COMBO_IDS)
def test_is_unlocked_is_a_total_predicate_that_says_false(
    mode: str, width: int
) -> None:
    """UN-06: an invalid combo never raises out of the predicate and never opens —
    an unrecognised width/mode is simply not unlocked."""
    assert type(is_unlocked(mode, width, 13)) is bool
    assert is_unlocked(mode, width, 13) is False
    assert is_unlocked(mode, width, 10**6) is False


@pytest.mark.parametrize(("mode", "width"), INVALID_COMBO_PROBES, ids=INVALID_COMBO_IDS)
def test_required_level_for_raises_on_a_combo_that_does_not_exist(
    mode: str, width: int
) -> None:
    """UN-06: the *partial* function raises, so an invalid combo is a 422 upstream
    and can never be rendered as a 403 with a made-up required level."""
    with pytest.raises(ValueError):
        required_level_for(mode, width)


@pytest.mark.parametrize("mode", MODES)
def test_seed_widths_are_bits_per_mode(mode: str) -> None:
    """UN-06 / D-7: every seed row's width is inside the mode's per-mode bit set —
    unicode 8/16/24/32, conversions 4/8/16/32 (so 24 is unicode-only and 4 is
    never a unicode width)."""
    widths = tuple(VALID_WIDTHS_BY_MODE[mode])
    seed_widths = sorted(rule.bit_width for rule in UNLOCK_SEED if rule.mode == mode)
    assert seed_widths == sorted(widths)
    assert set(seed_widths) <= set(widths)
    if mode == "unicode":
        assert 4 not in seed_widths
        assert 24 in seed_widths
    else:
        assert 24 not in seed_widths


def test_unicode_slot_one_is_eight_bits_and_open_at_level_one() -> None:
    """UN-06 / D-7: ``("unicode", 8)`` IS a valid combo — slot 1, open at L1 (the
    mirror image of the ``("unicode", 4)`` rejection)."""
    assert required_level_for("unicode", 8) == 1
    assert is_unlocked("unicode", 8, 1) is True
    assert ("unicode", 8, 1) in _triples(unlocks_for_level(1))


# ---------------------------------------------------------------------------
# §4.9 UN-07 — unlocks are never revoked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", UN07_LEVELS)
def test_losing_xp_down_to_the_floor_never_revokes_an_unlock(level: int) -> None:
    """UN-07 via LV-08: unlock at L4/L8/L13, then take every §4.7 penalty down to
    the ``cum(level)`` floor — the level, and therefore the unlock set, is
    unchanged, with no sticky-state column involved."""
    floor = cumulative_xp_for_level(level)
    ceiling = cumulative_xp_for_level(level + 1)
    before = _triples(unlocks_for_level(level))
    for position in (floor, floor + 4, ceiling - 4):
        assert level_from_xp(position) == level
        for mode, width in COMBOS:
            penalty = xp_penalty_for_incorrect(mode, width)
            clamped = clamped_total_xp(position, penalty)
            level_after = level_from_xp(clamped)
            assert level_after == level, (level, position, mode, width)
            after = _triples(unlocks_for_level(level_after))
            assert before <= after, (level, position, mode, width)
            assert newly_unlocked(level, level_after) == ()


def test_unsafe_state_loss_still_leaves_the_widest_set_intact() -> None:
    """UN-07 corollary: even a level-13 profile hammered to its floor by the
    largest penalty in §4.7 keeps all 16 combos open."""
    floor = cumulative_xp_for_level(13)
    clamped = clamped_total_xp(floor + 4, 512)  # bin_to_dec/32 penalty
    assert clamped == floor
    assert level_from_xp(clamped) == 13
    assert _triples(unlocks_for_level(level_from_xp(clamped))) == frozenset(
        EXPECTED_UNLOCK_SEED
    )


def test_unlock_lookup_is_pure_with_no_memo_that_could_go_stale() -> None:
    """UN-07 / D-2 "pure function of the level": reading the levels downwards and
    then upwards returns the same sets, so no hidden per-process cache or mutable
    module state participates in the answer."""
    downwards = [_triples(unlocks_for_level(level)) for level in range(20, 0, -1)]
    forwards = [_triples(unlocks_for_level(level)) for level in range(1, 21)][::-1]
    assert downwards == forwards
    assert all(
        _triples(unlocks_for_level(level)) == _expected_unlock_set(level)
        for level in range(1, 21)
    )
    # and the same for the predicates, interleaved
    for level in (1, 2, 4, 8, 13, 20):
        assert is_unlocked("bin_to_dec", 32, level) is (level >= 13)
        assert is_unlocked("unicode", 32, level) is (level >= 4)
