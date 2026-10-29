# XP Economy — Reference Tables

> Stand: 2026-10-29 · Sprint 4 (US-40) · Spiegel von [`docs/sprint4-plan.md`](sprint4-plan.md) **Section 4**
> (normative Tabellen) und **Section 3** (Design Decisions D-1 … D-8).

This document exists so the economy numbers are **code-adjacent instead of doc rot**.
`docs/sprint4-plan.md` is the *decision record* — 800 lines of Rev-1 … Rev-4 history,
PO corrections and review findings around the tables that actually matter. This file is
those tables alone, next to the code that implements them.

**Precedence, if the three ever disagree:**

1. [`src/services/game_config.py`](../src/services/game_config.py) — the frozen registry
   every other module imports from. Code wins over prose.
2. [`docs/sprint4-plan.md`](sprint4-plan.md) Section 3/4 — the normative source.
3. This file — a mirror. A drift here is a documentation bug, never an implementation
   instruction.

The drift is not silent by design: the golden tests of US-29
(`tests/test_xp_engine.py`, `tests/test_level_service.py`, `tests/test_unlock_service.py`)
and the end-to-end suite of US-41 assert exactly these figures, so a table that moves
without the code turns the suite red.

**Who reads this:** anyone computing XP by hand (frontend HUD, US-38/US-39), debugging a
`xp_delta` that "looks wrong" (D-5 floor clamp), or adding a mode/width/tier (start in
`game_config.py`, then update this mirror).

## Contents

1. [Master formula](#1-master-formula-41) · 2. [Base XP](#2-base-xp-table-42) ·
3. [Mode multipliers](#3-mode-multipliers-43) · 4. [Effective gain](#4-effective-gain--the-streak-independent-core-44) ·
5. [Streak curve + rational pairs](#5-streak-curve-45-d-3) · 6. [Golden matrix](#6-golden-xp-test-matrix-46-us-29) ·
7. [Penalty table](#7-penalty-table-47) · 8. [Level curve](#8-level-curve--cumulative-d-1-48) ·
9. [Unlock seed](#9-unlock-seed-d-2-49) · 10. [Worked example](#10-worked-example--the-us-33-golden-fixture) ·
11. [Where the numbers live](#11-where-the-numbers-live-in-code) · 12. [`bit_width` semantics](#12-bit_width-semantics-across-the-api-oq-7)

---

## 1. Master formula (§4.1)

```
correct:   xp_awarded  = base_xp(mode, bit_width) * mode_mult(mode) * streak_mult(new_streak)
           total_xp   += xp_awarded
incorrect: xp_penalty  = base_xp(mode, bit_width) * mode_mult(mode) * 0.5   (no streak term)
           total_xp    = max( cum(level), total_xp - xp_penalty )          # OQ-4 floor, NOT 0
           current_streak = 0
xp_delta (wire)        = total_xp_new - total_xp_old                        # post-clamp, signed
```

`cum(level)` is the cumulative XP the current level requires (row 2 of the level curve
in §8, with `cum(1) = 0`). Because the floor is a
multiple of 4 and `total_xp` is one, `xp_delta` stays in 4z even when the penalty is
truncated (D-8).

Three consequences worth internalising before reading the tables:

- **`xp_delta` on the wire is signed and post-clamp.** `xp_penalty_for_incorrect()`
  returns a **positive magnitude**; the router negates it (D-8 sign convention, Rev 3
  R-9). §7 below lists magnitudes.
- **A client must not assume a nonzero delta on `is_correct: false`.** A level-1 player
  at 0 XP who answers incorrectly gets `xp_delta: 0` — accepted, but it needs explicit
  UI treatment (D-5).
- **A player sitting exactly on `cum(level)` is temporarily immune to penalties** until
  they gain XP again (D-5). Also accepted.
- **Demotion is impossible by construction**, not by sticky state: `level` stays a pure
  function of `total_xp` and the floor does the rest. No `max_level_reached` column, no
  unlock-state table, and **an unlock can never be revoked** (D-1/OQ-4, D-2, UN-07).

### Arithmetic rules (D-8)

- No floating point anywhere in XP/level computation. Streak tiers are integer rational
  pairs; the penalty factor is a `Fraction`.
- Computation is `xp = (base_xp * mode_mult * num) // den`, guarded by
  `assert (base_xp * mode_mult * num) % den == 0` — an O(1) guard, tested directly as
  property **TP-8**. There is **no rounding branch anywhere in the economy**.
- **Invariant: every awarded XP, every penalty and every level requirement is an exact
  multiple of 4.** A hard tested property, not a style preference — it is what keeps the
  level arithmetic lossless forever.

---

## 2. Base XP table (§4.2)

PO-specified; every value divisible by 4.

**Columns are width *slots*, not a shared bit count.** Each mode's slot values are given
in the row label — per OQ-7, `unicode` speaks bits `8/16/24/32`, so its slots do not
align by bit count with the conversion modes'.

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 16 | 32 | 64 | 128 |
| hex_to_dec (4 / 8 / 16 / 32) | 24 | 48 | 96 | 192 |
| bin_to_dec (4 / 8 / 16 / 32) | 32 | 64 | 128 | 256 |
| unicode (**8 / 16 / 24 / 32**) | 16 | 32 | 64 | 128 |

---

## 3. Mode multipliers (§4.3)

| mode | multiplier |
|------|-----------|
| hex_to_bin | x1 |
| hex_to_dec | x2 |
| bin_to_dec | x4 |
| unicode | x1 |

---

## 4. Effective gain — the streak-independent core (§4.4)

`base_xp * mode_mult`. This is the number every tier below multiplies.

Verified: **every value is divisible by 16** — the property that makes the x5/4 tier
exact (§5, D-3).

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 16 | 32 | 64 | 128 |
| hex_to_dec (4 / 8 / 16 / 32) | 48 | 96 | 192 | 384 |
| bin_to_dec (4 / 8 / 16 / 32) | 128 | 256 | 512 | 1024 |
| unicode (**8 / 16 / 24 / 32**) | 16 | 32 | 64 | 128 |

---

## 5. Streak curve (§4.5, D-3)

The multiplier uses the **post-increment** streak — the streak *including* the current
correct answer (OQ-5). The 3rd consecutive correct answer already earns the 3-4 tier.

| streak | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11+ |
|--------|---|---|---|---|---|---|---|---|---|----|-----|
| mult | 1.0 | 1.0 | **1.25** | **1.25** | 1.5 | 1.5 | 1.5 | 2.0 | 2.0 | 2.0 | 2.5 (cap) |

### The tiers as rational pairs (what the code stores)

`game_config.STREAK_TIERS` holds integer `(numerator, denominator)` pairs, never
decimals — this is the D-8 "no float" rule made concrete:

| current streak (incl. this answer) | multiplier | rational pair |
|-----------------------------------|------------|---------------|
| 1-2 | x1.0 | (1,1) |
| 3-4 | **x1.25** | (5,4) |
| 5-7 | x1.5 | (3,2) |
| 8-10 | x2.0 | (2,1) |
| 11+ | x2.5 (cap; does not grow further) | (5,2) |

### Why x1.25 and not the originally specified x1.2 (OQ-1 → Option A)

x1.2 = 6/5 requires the core value to be divisible by 5, and **none of the 16**
`base_xp * mode_mult` values is (verified). All 16 products are non-integral:

```
HB-4   x 1.2 = 19.2     HD-4   x 1.2 = 57.6     BD-4   x 1.2 = 153.6     UNI-8  x 1.2 = 19.2
HB-8   x 1.2 = 38.4     HD-8   x 1.2 = 115.2    BD-8   x 1.2 = 307.2     UNI-16 x 1.2 = 38.4
HB-16  x 1.2 = 76.8     HD-16  x 1.2 = 230.4    BD-16  x 1.2 = 614.4     UNI-24 x 1.2 = 76.8
HB-32  x 1.2 = 153.6    HD-32  x 1.2 = 460.8    BD-32  x 1.2 = 1228.8    UNI-32 x 1.2 = 153.6
```

x1.25 = 5/4 works because every core value is divisible by 16 (§4), so x5/4 lands on a
multiple of 20 — hence a multiple of 4 — for **all 80** combinations (16 mode/width ×
5 tiers, re-verified with `Fraction` in Rev 3). Balance impact versus the specified 1.2:
**+4 % on one tier only**; every other tier untouched.

Rejected alternatives, recorded so the question is not re-litigated: *Option B* (keep
x1.2, round half up to the nearest multiple of 4 — costs a special-case rounding path in
an otherwise exact engine plus a divergent golden table) and *Option C* (keep x1.2,
relax the invariant from "multiple of 4" to "integer" — contradicts PO correction #1).

On an incorrect answer the streak multiplier is **never applied** (D-5), and
`current_streak` resets to `0`; there is no negative-streak concept. `best_streak` is
untouched.

---

## 6. Golden XP test matrix (§4.6, US-29)

Named spot-checks, **final** (x1.25 for the 3-4 tier per OQ-1; unicode widths in bits per
OQ-7). All 19 computable values were independently recomputed with `Fraction` in Rev 3
and match.

| ID | mode | width | streak | expected XP | note |
|----|------|-------|--------|-------------|------|
| XP-G01 | hex_to_bin | 4 | 1 | 16 | floor case, x1.0 |
| XP-G02 | hex_to_bin | 4 | 2 | 16 | top of tier 1-2 |
| XP-G03 | hex_to_bin | 4 | 3 | 20 | bottom of tier 3-4 (16 x 1.25, exact) |
| XP-G04 | hex_to_bin | 8 | 4 | 40 | top of tier 3-4 (32 x 1.25) |
| XP-G05 | hex_to_bin | 16 | 5 | 96 | bottom of tier 5-7 |
| XP-G06 | hex_to_bin | 32 | 7 | 192 | top of tier 5-7 |
| XP-G07 | hex_to_dec | 4 | 8 | 96 | bottom of tier 8-10, mode x2 |
| XP-G08 | hex_to_dec | 8 | 5 | 144 | 96 x 1.5 |
| XP-G09 | hex_to_dec | 16 | 10 | 384 | top of tier 8-10 |
| XP-G10 | hex_to_dec | 32 | 11 | 960 | bottom of tier 11+, x2.5 |
| XP-G11 | bin_to_dec | 4 | 2 | 128 | mode x4 at floor tier |
| XP-G12 | bin_to_dec | 8 | 7 | 384 | 256 x 1.5 |
| XP-G13 | bin_to_dec | 16 | 3 | 640 | 512 x 1.25 |
| XP-G14 | bin_to_dec | 32 | 12 | 2560 | max single award in system (1024 x 2.5) |
| XP-G15 | bin_to_dec | 32 | 50 | 2560 | cap: streak 11+ identical at 50 |
| XP-G16 | unicode | **8** | 1 | 16 | unicode floor (1 byte = 8 bits, OQ-7) |
| XP-G17 | unicode | **16** | 8 | 64 | 32 x 2.0 (2 bytes) |
| XP-G18 | unicode | **24** | 4 | 80 | 64 x 1.25 (3 bytes) |
| XP-G19 | unicode | **32** | 15 | 320 | 128 x 2.5 (4 bytes) |
| XP-G20 | hex_to_bin | 4 | 0 | — | invalid input: streak 0 never occurs on award (first correct -> streak 1). **Pinned (Rev 3, R-9): the engine raises `ValueError`** — no normalization, no silent clamp. |
| XP-G21 | hex_to_bin | 6 | 1 | — | invalid width for the mode -> `ValueError` (the US-29 "invalid (mode, width) combos raise" rule, made concrete). Defence-in-depth only: the user-facing path must already be a 422 from US-32, never this raise. |
| XP-G22 | unregistered | 4 | 1 | — | unknown mode -> `ValueError` |

### Penalty spot-checks (§4.6)

Streak-independent, verified multiples of 4. **Values below are the signed wire value
(`xp_delta`)**; `xp_penalty_for_incorrect()` returns the positive magnitude — see the sign
convention in D-8 and §7 below.

| ID | mode | width | expected penalty |
|----|------|-------|------------------|
| XP-P01 | hex_to_bin | 4 | -8 |
| XP-P02 | hex_to_bin | 32 | -64 |
| XP-P03 | hex_to_dec | 16 | -96 |
| XP-P04 | bin_to_dec | 4 | -64 |
| XP-P05 | bin_to_dec | 32 | -512 |
| XP-P06 | unicode | **16** | -16 |
| XP-P07 | any | any | penalty identical for streak inputs {1, 5, 11, 50} — asserts streak never touches penalty |
| XP-P08 | all 16 | all | **2:1 ratio check (OQ-2):** penalty == exactly half of the same combo's tier-1.0 gain, for all 16 (mode, width) pairs. Spot example: bin_to_dec/32 → 512 vs a 1,024 tier-1.0 gain. |

XP-P06's width is labelled in bits per OQ-7 (2 bytes = 16 bits); the penalty value is
unchanged.

> These are **gross** penalties as the engine computes them. The value actually applied to
> `total_xp` may be truncated by the OQ-4 floor at `cum(level)`; the applied delta is
> asserted in LV-09 (below) and in US-41, not here.

### Property tests (§4.6)

Parametrize over the FULL matrix — 16 mode/width × tiers:

| ID | property |
|----|----------|
| XP-TP1 | For all (mode, width, tier): awarded XP is an integer and `xp % 4 == 0` |
| XP-TP2 | Tier monotonicity: fixed (mode, width), XP non-decreasing across tiers 1.0 <= 1.25 <= 1.5 <= 2.0 <= 2.5 |
| XP-TP3 | Width monotonicity: fixed (mode, tier), XP strictly increasing with width |
| XP-TP4 | Difficulty ordering at equal tier/width: bin_to_dec > hex_to_dec > hex_to_bin; unicode values == hex_to_bin values |
| XP-TP5 | Streak cap: tier(11) == tier(12) == tier(10**6) == x2.5 |
| XP-TP6 | Tier boundaries exact: streaks 1..15 map to multipliers [1, 1, 1.25, 1.25, 1.5, 1.5, 1.5, 2, 2, 2, 2.5, 2.5, 2.5, 2.5, 2.5] |
| XP-TP7 | Penalty = half of tier-1.0 gain, positive, `% 4 == 0`, independent of streak argument |
| XP-TP8 | Divisibility guard: `(base * mode_mult * num) % den == 0` for every combination (the D-8 runtime assertion tested directly) |
| XP-TP9 | No float leakage: engine module exposes only int/Fraction arithmetic; a test monkeypatches `float` usage check or asserts result types are `int` for all 80 combinations |

---

## 7. Penalty table (§4.7)

`penalty = base_xp * mode_mult * 0.5` — **positive magnitudes**, i.e. the engine's return
value. `xp_delta` on the wire is the negation of these (US-33). Verified: all 16 are
integers and multiples of 4.

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 8 | 16 | 32 | 64 |
| hex_to_dec (4 / 8 / 16 / 32) | 24 | 48 | 96 | 192 |
| bin_to_dec (4 / 8 / 16 / 32) | 64 | 128 | 256 | 512 |
| unicode (**8 / 16 / 24 / 32**) | 8 | 16 | 32 | 64 |

Notes (D-5):

- **The mode multiplier IS part of the penalty** — confirmed canonical by PO (OQ-2). The
  heading of PO correction #1 said the opposite; the formula wins. Consequence: the
  reward:risk ratio is a uniform **2:1 in every mode** (the penalty is exactly half of
  that mode/width's tier-1.0 gain, XP-TP7). A wrong `bin_to_dec`-32 costs 512 XP, a wrong
  `hex_to_bin`-4 costs 8.
- **No streak multiplier.** Streak state is irrelevant to the penalty value.
- **The floor is `cum(level)`, not 0** (OQ-4). The DB `CHECK` stays `total_xp >= 0` and is
  a backstop only — `total_xp >= cum(level)` is not expressible as a SQL CHECK (it is a
  geometric sum), so the real clamp lives in the answer transaction and is pinned by
  LV-08/LV-09 and US-41.

---

## 8. Level curve + cumulative (D-1, §4.8)

```
xp_required_for_level(n) = round_to_nearest_multiple_of_4( 100 * 1.5^(n-1) )
```

- `xp_required_for_level(n)` = XP needed to advance **from level n to level n+1** — a
  per-level **span**, not a cumulative threshold. Player starts at **level 1 with 0 XP**
  (OQ-3). The internal engine function keeps this name; only the *wire field* is renamed
  `xp_required_for_next_level` (OQ-8).
- **Tie rule:** exact halves round **up** (away from zero). Fires only at n=2 (150 → 152).
- **Unlimited levels** — no cap at 13. The cap at 13 applies only to unlocks (§9).
- `total_xp → level` is derived by walking cumulative sums: level = highest `L` with
  `cum(L) <= total_xp`, with `cum(1) = 0`.
- **`cum(L)` doubles as the XP floor** (OQ-4): `total_xp` may never fall below
  `cum(level)`. Every `req(n)` is a multiple of 4, so every `cum(L)` is too — the clamp
  keeps `total_xp` in 4z.

Verified curve (exact, computed with rational arithmetic):

| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|----|----|----|----|----|----|
| req(n) | 100 | 152 | 224 | 336 | 508 | 760 | 1140 | 1708 | 2564 | 3844 | 5768 | 8648 | 12976 | 19460 | 29192 |
| cum to reach L(n+1) | 100 | 252 | 476 | 812 | 1320 | 2080 | 3220 | 4928 | 7492 | 11336 | 17104 | 25752 | 38728 | 58188 | 87380 |

### Float-drift threshold (Rev 3, R-5)

The exact rational `3^(n-1)` stops being representable in 53 bits at **n ≥ 35**
(3^34 = 16,677,181,699,666,569 > 2^53), but rounding to a multiple of 4 absorbs that error
much longer: the **first level at which a float implementation actually returns a
different `req(n)` is n = 84** (exact 41,264,301,843,800,316 vs float
41,264,301,843,800,312). Consequence for testing: a float-based implementation **passes**
`req(20)` and `req(50)` while violating D-8, so LV-06 must probe n ≥ 84, not n = 20/50.

### Level test cases (§4.8, US-30)

| ID | assertion |
|----|-----------|
| LV-01 | `req(n)` matches D-1 table exactly for n = 1..15 |
| LV-02 | For n = 1..100: `req(n) % 4 == 0` and integer |
| LV-03 | For n = 1..100: strictly increasing |
| LV-04 | Tie rule: n=2 -> 152 (round-half-up; raw 150) |
| LV-05 | `total_xp -> level`: 0->L1, 99->L1, 100->L2, 251->L2, 252->L3, 475->L3, 476->L4, 25751->L12, 25752->L13 |
| LV-06 | No cap + **float-drift guard**: `req(84)`, `req(100)`, `req(200)` computable and equal to a `Fraction`-based reference. **Corrected in Rev 3 (R-5):** the first n where a float implementation diverges is **84**, not 35 — `req(20)`/`req(50)` are *identical* under float and exact arithmetic (verified), so the Rev 2 version of this test could not fail and guarded nothing. n=84 is the regression anchor: exact 41,264,301,843,800,316 vs float 41,264,301,843,800,312. |
| LV-07 | Boundary is inclusive upward: exactly at threshold -> new level (LV-05 covers) |
| LV-08 | **Level is monotonically non-decreasing — demotion is impossible** (PO decision OQ-4, Rev 4; supersedes the Rev 2/3 "demotion accepted" behaviour). `level` is still a pure function of `total_xp`; the guarantee comes from the floor, not from sticky state. Test: for every level L and every penalty P in the 4.7 table, applying P to a profile sitting anywhere in `[cum(L), cum(L+1))` leaves `level_from_xp(total_xp_new) == L`. |
| LV-09 | **Floor clamp (OQ-4):** `total_xp_new == max(cum(level), total_xp - penalty)`. Cases: (a) comfortable margin → full penalty applies; (b) `total_xp == cum(level)` exactly → `xp_delta == 0`, level unchanged, `current_streak` still resets to 0; (c) `total_xp == cum(level) + 4` with a penalty of 64 → clamps to `cum(level)`, `xp_delta == -4`; (d) level 1 → `cum(1) == 0`, so a fresh profile floors at 0 exactly as the legacy rule did. Assert `xp_delta % 4 == 0` in **every** case (the clamp preserves the D-8 invariant). |
| LV-10 | `xp_progress(total_xp)` never returns a negative `xp_in_level`, including immediately after a clamped penalty (follows from LV-09 but pinned separately — it is the value US-33 puts on the wire and US-38 renders). |

---

## 9. Unlock seed (D-2, §4.9)

Binding PO constraints: **all conversion-track unlocks land at or below level 13; all
Unicode unlocks at or below level 4.** Above those levels there are no further unlocks —
XP climbing only (prestige/leaderboard).

Ladder (OQ-6): **1 / 4 / 8 / 13** for conversion widths, **L1-L4** for unicode. Unicode
`bit_width` values are **bits (8/16/24/32)**, not bytes (OQ-7). Stored as a frozen
constant in [`src/services/game_config.py`](../src/services/game_config.py) — code, not
DB; a seed migration was rejected as YAGNI.

| mode | bit_width | min_level |
|------|-----------|-----------|
| hex_to_bin | 4 | 1 |
| hex_to_bin | 8 | 4 |
| hex_to_bin | 16 | 8 |
| hex_to_bin | 32 | 13 |
| hex_to_dec | 4 | 1 |
| hex_to_dec | 8 | 4 |
| hex_to_dec | 16 | 8 |
| hex_to_dec | 32 | 13 |
| bin_to_dec | 4 | 1 |
| bin_to_dec | 8 | 4 |
| bin_to_dec | 16 | 8 |
| bin_to_dec | 32 | 13 |
| unicode | 8 | 1 |
| unicode | 16 | 2 |
| unicode | 24 | 3 |
| unicode | 32 | 4 |

Unlocks are a **pure function of level** — no unlock-state table; the server recomputes
from `level`, the client renders from the same seed exported by the API (US-35
`unlocks`, US-33 `newly_unlocked`). Because of the OQ-4 floor, `level` never decreases, so
**an unlock can never be revoked** — the pure-function property holds without sticky state.

**The width-value overlap across modes is intentional:** `bit_width: 8` means 8 bits for
`hex_to_bin` and 8 bits (= 1 byte) for `unicode`. It is unambiguous once every mode speaks
bits — the mode always disambiguates, and the mode-aware validator (US-32) rejects values
outside the per-mode set.

### Pacing sanity check (Rev 3, R-4)

Cumulative XP required to **reach** the unlock level (`cum(req(1..L-1))` from §8):

| unlock | min_level | XP to reach that level | ~answers at the x1.25 tier |
|--------|-----------|------------------------|---------------------------|
| 8-bit | L4 | **476** | ~24 correct HB-4 answers (20 XP each) |
| 16-bit | L8 | **3,220** | _(Rev 2 wrongly said 2,080 — that is `cum(req(1..6))`, i.e. reaching **L7**)_ |
| 32-bit | L13 | **25,752** | reachable quickly via high multipliers: a BD-16 answer at x2.0 awards 1,024 XP (~25 answers) |

Cross-check: LV-05 (`476 → L4`, `25752 → L13`) and US-41 both use these same figures.

### Unlock test cases (§4.9, US-31)

| ID | assertion |
|----|-----------|
| UN-01 | No unlock has `min_level > 13` for conversion modes; none `> 4` for unicode (binding PO caps) |
| UN-02 | `unlocks_for_level(1)` = the four slot-1 widths (`hex_to_bin`/`hex_to_dec`/`bin_to_dec` at 4, `unicode` at **8**); `unlocks_for_level(13)` = all 16 entries |
| UN-03 | `newly_unlocked(level_before, level_after)` returns exactly the delta set (used by US-33 response) |
| UN-04 | Monotonicity: unlock set for level L is a subset of the set for level L+1 |
| UN-05 | Answering with a locked (mode, width) -> **403** with `detail` naming the required level (decision fixed in US-31) |
| UN-06 | The seed uses **bits** for every mode: `unicode` entries are 8/16/24/32, and `is_unlocked("unicode", 4, 13)` is **False** because 4 is not a valid unicode width at all (that combination must be a 422 from US-32, never a silent 403/200) — pins the OQ-7 decision |
| UN-07 | **Unlocks are never revoked.** Given LV-08 (level cannot decrease), `is_unlocked(mode, width, level)` cannot flip from True to False for a given user. Assert via the answer path: unlock at L4, then lose XP down to the floor, then re-check — still unlocked, with no sticky-state column anywhere |

---

## 10. Worked example — the US-33 golden fixture

The fixture `tests/test_game_profile_models.py` reproduces byte-for-byte, and the README
examples are the same numbers:

`hex_to_bin`, `bit_width: 8`, third consecutive correct answer:

```
core (§4.4)          = base_xp 32 * mode_mult 1      = 32
tier  (§5, streak 3) = (5,4)  → 32 * 5 // 4          = 40      (exact, no remainder)
xp_delta             = +40                           (correct answer, so no floor clamp)
```

The remaining figures are the frozen fixture state, and each follows from §8 without
further arithmetic:

```
total_xp             = 1320
level (§8)           = highest L with cum(L) <= 1320 = 6       (cum(6) = 1320, cum(7) = 2080)
xp_in_level          = 1320 - cum(6)                 = 0       (exactly on the threshold)
xp_required_for_next_level = req(6)                  = 760
newly_unlocked       = []                            (L6 fires no seed row — the common case)
```

Wire response (`POST /api/game/answer`, [README §Game Endpoints](../README.md#game-endpoints)):

```json
{
  "xp_delta": 40,
  "total_xp": 1320,
  "level": 6,
  "xp_in_level": 0,
  "xp_required_for_next_level": 760,
  "current_streak": 3,
  "best_streak": 7,
  "correct_count": 42,
  "incorrect_count": 5,
  "newly_unlocked": []
}
```

The most valuable counter-example, because it looks like a bug: a level-1 player at
`total_xp: 0` answers incorrectly in `hex_to_bin`/4. The engine computes a gross penalty of
**8** (§7), but the floor is `cum(1) = 0`, so the applied `xp_delta` is **0** and
`incorrect_count` still increments. Streak resets, nothing is lost, and the client must not
render "nothing happened" (D-5).

---

## 11. Where the numbers live in code

| Table in this document | Implementation |
|---|---|
| §2 base XP, §3 mode multipliers, §5 tiers, §7 factor, §9 seed, valid widths (§12) | [`src/services/game_config.py`](../src/services/game_config.py) — frozen registry, single source of truth |
| §1 award half, §7 magnitudes | [`src/services/xp_engine.py`](../src/services/xp_engine.py) (US-29) — `xp_for_correct`, `xp_penalty_for_incorrect` |
| §8 curve, floor, clamp | [`src/services/level_service.py`](../src/services/level_service.py) (US-30) — `cumulative_xp_for_level`, `level_from_xp`, `xp_progress`, `clamped_total_xp` |
| §9 ladder + delta set | [`src/services/unlock_service.py`](../src/services/unlock_service.py) (US-31) — `unlocks_for_level`, `newly_unlocked`, `is_unlocked` |
| §1 wiring, streak lifecycle, row lock | [`src/services/answer_service.py`](../src/services/answer_service.py) (US-33) + [`src/services/game_service.py`](../src/services/game_service.py) (US-28) |
| Wire shapes (`xp_required_for_next_level` et al., OQ-8) | [`src/models/game_schemas.py`](../src/models/game_schemas.py) (US-26) |
| Rate limit (120 answers / 60 s) | `game_config.ANSWER_RATE_LIMIT` / `ANSWER_RATE_WINDOW_SECONDS`, enforced in `src/routers/game.py` (US-32/US-33) |

---

## 12. `bit_width` semantics across the API (OQ-7)

`bit_width` is **bits in every mode** — this is the one thing to get wrong when reading or
writing any Sprint-4 payload.

| mode | valid `bit_width` | meaning |
|------|-------------------|---------|
| `hex_to_bin` / `hex_to_dec` / `bin_to_dec` | 4, 8, 16, 32 | bits |
| `unicode` | **8, 16, 24, 32** | bits — i.e. 1, 2, 3, 4 **bytes** |

Rev 2/3 had unicode carrying byte counts (1-4) in a field named `bit_width`, which meant
`bit_width: 4` signified 4 bits in one mode and 32 bits in another — a client bug would
have produced a *valid but wrong* answer worth 128 XP instead of a 422. Speaking bits
everywhere removes that bug class: `unicode + bit_width: 4` is now a loud rejection. The
frontend needs no change to its arithmetic (`UnicodeTrainer.tsx` already computes
`bytes * 8`). Cost accepted: the unicode rows in §2/§4/§7 and §9 lose the symmetric
1/2/3/4 labelling, and 24 is not a power of two.

Validation is driven by `game_config.VALID_WIDTHS_BY_MODE` — never a hardcoded tuple,
never a global width set (`4` is a legal conversion width and an illegal unicode one;
`24` is the reverse).

---

## Related documents

- [`docs/sprint4-plan.md`](sprint4-plan.md) — the decision record: Section 3 (D-1 … D-8),
  Section 4 (these tables in context), Section 5 (user stories), Section 9 (the OQ
  decision record behind every "PO confirmed" above)
- [`docs/architecture.md`](architecture.md) — module/community structure, incl. the Sprint-4
  game community and the two-migration strategy
- [`docs/backlog.md`](backlog.md) — the deferred economy hardening: server-issued challenge
  nonces / HMAC-signed answers, and the answer-history event table
- [`README.md`](../README.md) — endpoint contracts and example payloads
- `http://localhost:8000/docs` — live OpenAPI reference; the field descriptions there are
  generated from `src/models/game_schemas.py`
