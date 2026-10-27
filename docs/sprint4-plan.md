# Sprint 4 Plan (Rev 4) — XP Economy: Modes, Streaks, Levels, Unlocks

> **Status:** FINAL — all open questions resolved by the PO (2026-10-27, Rev 4). This document supersedes Rev 3, Rev 2 and Rev 1. **No OQ remains open; Day 1 can start without any pending decision.**
> **Rev 4 scope:** the eight PO decisions OQ-1..OQ-8 were applied throughout (Section 0.3). Headline changes: streak tier 3–4 frozen at **x1.25**; `bit_width` is **bits in every mode** (unicode: 8/16/24/32); XP floor at **`cum(level)`** — demotion is impossible; wire field renamed to **`xp_required_for_next_level`**; penalty **includes** the mode multiplier (uniform 2:1 ratio).
> **Rev 3 verification scope:** every normative table in Section 4 and D-1 was recomputed with exact rational arithmetic (`fractions.Fraction`) — **all confirmed correct as written**; every Section 2 code fact was re-checked against both repos; the sibling frontend was audited for integration surface. Three execution blockers were found in the story sequencing and at the frontend system boundary (R-1..R-3 below). See Section 0.2.
> **Sprint goal:** Replace the legacy `+bits / -4*bits` points system with the new XP economy: unified answer endpoint (`mode` + `bit_width` + `is_correct`), exact-integer XP calculation with mode and streak multipliers, unlimited level progression, and level-gated content unlocks.
> **Repo scope:** Primarily `hexToBinaryTrainer2Backend` (this repo); US-38/US-39 touch `hexToBinaryTrainer2Frontend`.
> **Complexity note:** US-29 and US-33 are HIGH, but **no architect/spike round is needed** — the PO has frozen the spec (D-1..D-8 below are closed-form, normative). The spike protocol applies only when multiple valid approaches with real trade-offs exist. Residual risk is concentrated in test completeness, mitigated by the property + golden test matrix in Section 4.6/5-US-29.

---

## 0. Changelog

### 0.1 Rev 2 — what changed vs Rev 1

| # | PO correction | Affected artifacts | Status in Rev 2 |
|---|---------------|------------------|-----------------|
| 1 | XP formula is NOT `+bits / -4*bits`. New: correct = `base_xp * mode_mult * streak_mult`; incorrect = `base_xp * mode_mult * 0.5` (no streak, no negative streak). Base table + multipliers specified, all values exact multiples of 4. | US-29, US-33, Section 4 | CORRECTED — full normative tables in Section 4 |
| 2 | No legacy backfill. DB is empty. Fresh schema only; drop/recreate in migration. | US-27, D-6 | CORRECTED — no data migration. _(Rev 2 also had v004 **drop** `user_stats`; **superseded by Rev 3 / R-1** — v004 is additive, the drop moved to v005 in US-34.)_ |
| 3 | Streak-to-XP multiplier IS in scope this sprint (core booster mechanic). | D-4 removed, D-3 added, US-28/US-29 | CORRECTED — D-4 deleted (tombstone), streak multiplier implemented in US-29 |
| 4 | Level curve: `xp_required_for_level(n) = 100 * 1.5^(n-1)` rounded to nearest multiple of 4. Unlimited levels. Unlocks capped at L13 (Hex/Bin) and L4 (Unicode). | US-30, US-31, D-1, D-2 | CORRECTED — exact curve table in Section 4.8, unlock seed in Section 4.9 |
| 5 | Answer payload = `{mode, bit_width, is_correct}`, NOT `{track, bits}`. Backend infers track from mode name. | US-33, D-7 | CORRECTED — contract in US-33 |
| — | **New finding (this revision):** the specified streak tier x1.2 (streak 3-4) violates the "exact multiples of 4, no rounding" invariant for all 16 mode/width combinations (e.g. 16 x 1.2 = 19.2). x1.25 preserves it exactly. | US-29 test matrix, D-3 | FLAGGED as OQ-1 — **RESOLVED in Rev 4: x1.25** (Section 4.5 final). |
| — | **New finding (this revision):** correction #1 heading says mode multipliers are "NOT applied to penalty", but the penalty formula `base_xp * mode_multiplier * 0.5` includes the mode multiplier. Rev 2 follows the formula (canonical). | D-5 | FLAGGED as OQ-2 — **RESOLVED in Rev 4: the formula is canonical** (penalty includes the mode multiplier). |

### 0.2 Rev 3 — what changed vs Rev 2 (verification pass)

**Confirmed correct — no change needed.** Recomputed with `Fraction`, not floats: the D-1 curve (all 15 `req(n)` values), the cumulative row, the tie rule (fires at **n=2 only**, verified n=1..400), the 4.4 div-by-16 claim, all five streak tiers producing `%4==0` across all 16 combos, the 4.7 penalty table, and **all 19 computable golden values XP-G01..G19**. LV-05's boundaries are consistent with the cumulative row. The **OQ-1 diagnosis is mathematically sound** (0 of 16 core values are divisible by 5 → all 16 non-integral under x6/5) and Option A (x1.25) genuinely preserves the invariant by construction. US-33's example response is internally consistent (`total_xp` 1320 → L6, `req(6)`=760, `xp_delta` 40 = HB-8 @ streak 3 under x1.25). The economy spec is stable; the defects below are all in sequencing and at the system boundary.

| # | Review finding | Sev | Affected artifacts | Resolution in Rev 3 |
|---|----------------|-----|--------------------|--------------------|
| R-1 | US-26 removes the `UserStats` ORM while `src/routers/game.py:10` and `tests/test_user_me.py:13` still import it. `main.py:17` imports `game_router`, so **the FastAPI app cannot boot** and pytest fails at collection from Day 1 PM until US-33 (Day 2 PM) / US-37 (Day 3 AM) land. US-27's AC-3 (grep clean) was **unsatisfiable at its own delivery time**. Violates the house rules (atomic commit per branch, green tests as merge precondition). | CRITICAL | D-6, US-26, US-27, US-34, §6, §7 | **v004 is additive** — creates `user_game_profile`, leaves `user_stats` in place. The `DROP TABLE user_stats` moves to **v005, delivered together with US-34 at sprint close**. Every PR stays green; also removes the data-loss risk in dev/preview DBs where "DB is empty" may not hold. |
| R-2 | `RateLimitService.check()` **commits internally** (`await self.session.commit()`, `src/services/rate_limit_service.py`). Called on the answer session after the row lock, it ends the transaction and **releases US-28's `SELECT … FOR UPDATE`** → breaks "one HTTP call = one atomic transaction" and the US-41 concurrency test; double-submits can double-count. Separately, `check()` runs an unbounded `DELETE … WHERE expires_at < now` on **every** call — a full delete scan per answer on the hottest endpoint, at 120 answers/min/user. | CRITICAL | US-32, US-28, US-33 | US-32 gains an explicit **transaction-boundary AC + test** (rate limiting must not commit the answer transaction) and the cleanup `DELETE` moves off the request path. |
| R-3 | US-36 made the leaderboard auth-required, but `README.md:689` documents it as **"Public — no authentication required"** and the frontend calls it with plain `fetch`, no `Authorization` header (`api.ts:142-146`), inside a handler with **no try/catch** that **polls every 30 s** (`Leaderboard.tsx:26-42,50`) and is permanently mounted (`App.tsx:139-143`) → unhandled-rejection loop + eternal spinner, no visible error. No story covered the fallout; the decision was buried as a parenthetical in a P1 story. | CRITICAL | US-36, US-38, US-40 | **PO decision (2026-10-27): the leaderboard stays PUBLIC. No auth.** Entries carry only `username` + game numbers, no PII. Only the sort field and entry shape change; US-38 covers the PTS→XP column migration and the error-handling gap. |
| R-4 | D-2 pacing check said "16-bit at 2,080 XP". 2,080 = `cum(req(1..6))` = reaching **L7**; L8 requires **3,220**. (8-bit/L4 = 476 and 32-bit/L13 = 25,752 were both correct — an off-by-one-level slip, not a systematic error.) | HIGH | D-2 | Corrected to 3,220. LV-05 and US-41 were already consistent with 3,220. |
| R-5 | D-1/D-8 claimed floats drift at **n ≥ 35**. True for the exact rational (3^34 = 16,677,181,699,666,569 > 2^53), but rounding to a multiple of 4 absorbs the error far longer: the **first actual divergence is n = 84** (exact 41,264,301,843,800,316 vs float …312). LV-06 tested `req(20)`/`req(50)` — at both, float and exact **agree**, so LV-06 could not detect the drift it existed to detect. | HIGH | D-1, D-8, LV-06, §8 | Claim corrected; LV-06 now tests n = 84 / 100 / 200 against a Fraction reference. D-8's integer-only rule is unchanged. |
| R-6 | US-38/US-39 targeted `types.ts` `Score`, which is **dead code** (zero importers in `src/` or `test/`), and `BitByteSelector.tsx`, which is a **generic** component (`options: number[]`) used only by `Sidebar.tsx:207-221` (mobile). The live `points` contracts are `api.ts:23,35`, `authApi.ts:63`, `scoreCache.ts:20`, `AuthContext.tsx:60-66,186-198`, `Sidebar.tsx:246`, `translations.ts:137,284`. Width/mode lists are **triplicated**: `HexBinTrainer.tsx:9,11`, `App.tsx:14`, `Sidebar.tsx:11-13`. | HIGH | §2, US-38, US-39 | File lists rewritten to the real surface. New ACs: i18n keys, `applyGameResult` field pass-through, scoreCache version bump, shared frontend constant module. |
| R-7 | Section 2 missed that `UnicodeTrainer.tsx:67,73` **already submits scores** via `gameApi.submitCorrect(bytes * 8)` — a 3-byte answer sends `bits=24`, which the *legacy* endpoint cannot interpret (no `mode` field). US-34's grep AC (`game/correct\|game/incorrect`) cannot see these call sites (they call the method, not the URL). | HIGH | §2, US-34, US-38 | Befund extended; US-34 grep widened to `submitCorrect\|submitIncorrect`. _(Rev 3's prescription — mapper AC "bytes 1-4 → `bit_width` 1-4, **not** ×8" — is **superseded by the OQ-7 decision in Rev 4**: unicode speaks bits, so `bytes * 8` → 8/16/24/32 is the correct wire value and stays. The grep ACs are unaffected.)_ |
| R-8 | Frontend critical path overran Day 4: US-38 (1 d) started Day 3 PM → ended Day 4 PM, then US-39 (0.5 d) → Day 5, while US-34 is gated on US-38 shipping. Yet the US-33 contract was **already frozen inside this document**. | MEDIUM | §6 | Contract frozen **Day 1** as a typed stub; frontend-dev starts **Day 2** against a mock server. Pulls ~1 day off the critical path. |
| R-9 | `newly_unlocked` element shape was unspecified in US-33 but consumed by US-38's level-up toast. Penalty sign was inconsistent: XP-P01..P06 show `-8`, Section 4.7 shows `8`, XP-TP7 asserts "positive". XP-G20 said "ValueError **or equivalent**" — not a testable spec. | MEDIUM | US-33, US-35, 4.6, 4.7, XP-G20 | Shape pinned to `{mode, bit_width, min_level}`; the engine returns a **positive magnitude** and the router negates it for `xp_delta`; XP-G20 pinned to `ValueError`. |
| R-10 | US-27 told the team to update `tests/test_migrations.py` assertions expecting `user_stats` — **no such assertions exist** (the `points` hits at :170,177 belong to a `users` fixture). Section 2 line refs drifted. Gate #6's `grep "points"` is substring-loose. `/api/user/me` returns **401** for a missing user (`user.py:104`) while game endpoints return **404**. | LOW | US-27, §2, §7, US-37 | All corrected; gate #6 uses word boundaries; US-37 aligns the status code. |
| R-11 | `bit_width` carries **bits** for the three conversion modes but **bytes** for `unicode` — `bit_width: 4` silently means 32 bits there, so a client bug yields a *valid-but-wrong* answer (16 XP) rather than a 422. And the response field `xp_required_for_level` actually holds the span to the **next** level (760 at L6), while the name reads as "XP required to attain level 6" (1,704 cumulative). | MEDIUM | US-26, US-32, US-33 | Raised as OQ-7/OQ-8 — **both RESOLVED in Rev 4**: OQ-7 → option (d), **bits in every mode** (unicode 8/16/24/32; the ambiguous-bytes bug class is gone); OQ-8 → wire field renamed **`xp_required_for_next_level`** (internal engine function keeps its D-1 name). |

### 0.3 Rev 4 — what changed vs Rev 3 (PO decisions applied)

All eight open questions were decided by the PO on 2026-10-27 and are applied normatively throughout this document. **No open question remains.** Full decision record in Section 9.

| OQ | PO decision | Applied in |
|----|-------------|-----------|
| OQ-1 | Streak tier 3–4 = **x1.25** (rational pair (5,4)); no rounding path, no Option-B seam | D-3, 4.4, 4.5, 4.6 golden tables (final), US-25, US-29 |
| OQ-2 | **Formula is canonical:** penalty = `base_xp * mode_mult * 0.5` — mode multiplier IS applied; uniform 2:1 gain:penalty ratio per mode/width | D-5, 4.7, new XP-P08 ratio test |
| OQ-3 | `xp_required_for_level(n)` = **span n→n+1**; player starts **L1 with 0 XP** | D-1, 4.8, US-30 |
| OQ-4 | **No demotion, ever:** XP floor is `cum(level)`, not 0 (`total_xp_new = max(cum(level), total_xp - penalty)`); no `max_level_reached` column, no sticky state; `cum(1) = 0` preserves legacy behaviour at L1 | D-1, D-5, 4.1, LV-08/09/10, UN-07, US-26/30/33/41 |
| OQ-5 | **Post-increment** streak: the 3rd consecutive correct answer already earns the 3–4 tier | D-3, 4.5, US-28/29 |
| OQ-6 | Unlock ladder **1 / 4 / 8 / 13** (conversion slots 1–4); unicode L1–L4; caps binding | D-2, 4.9, US-31 |
| OQ-7 | **Option (d): `bit_width` is bits in every mode.** Unicode valid widths = 8/16/24/32. Frontend's existing `bytes * 8` is correct as-is (reverses Rev 3's R-7 prescription) | D-2, D-7, 4.2/4.4/4.7 slot tables, UN-02/06, US-25/26/32/38/40 |
| OQ-8 | Wire field renamed **`xp_required_for_next_level`** (US-33 + US-35 identically); internal engine function keeps the D-1 name | US-33, US-35, US-38 HUD |

---

## 1. Scope

**In scope (Sprint 4):**
- New XP/streak/level schema (migration v004), no backfill (DB is empty)
- Pure-function XP engine (exact integer arithmetic, multiples-of-4 invariant)
- Streak tracking (current/best), streak multiplier applied to correct answers
- Level progression (unlimited, geometric curve), unlock gating (caps: L13 Hex/Bin, L4 Unicode)
- Unified `POST /api/game/answer` endpoint; removal of legacy `/correct` + `/incorrect`
- Leaderboard by total XP — **stays public, no auth** (PO decision Rev 3, R-3); `/api/user/me` and profile endpoint adapted
- Frontend: unified submit client, XP/streak/level HUD, unlock-gated width selection
- Security/input-validation on the answer endpoint (anti-XP-farming)

**Out of scope (deliberately — YAGNI):**
- Server-issued challenge nonces / HMAC-signed answers (deferred; **filed in `docs/backlog.md` by US-40** if rate limiting proves insufficient)
- Answer-history event table (no analytics requirement this sprint; **also filed in `docs/backlog.md` by US-40**. Accepted trade-off: with no event log there is no forensic trail for an XP-farming investigation or a false-positive rate-limit complaint — the rate-limit counter is the only evidence, and it expires.)
- Achievements, daily goals, prestige mechanics (levels climb for leaderboard/prestige only)
- OAuth, phone auth, account deletion (auth-hardening backlog, not game economy)
- Direction-aware XP (hex->bin and bin->hex award identical XP; direction is a frontend concern)

---

## 2. Befund — current state (verified against code)

Re-verified against both repos on 2026-10-27 (Rev 3). `BE-` = this repo, `FE-` = `hexToBinaryTrainer2Frontend`.

### 2.1 Backend

| Fact | Evidence |
|------|----------|
| Legacy scoring `points += bits` / `points -= bits * 4` lives inline in the router | BE-1 `src/routers/game.py:77, 82, 125, 130` |
| Payload today is `{bits: int}` with **no validation** (tests submit `bits: 12`; negatives would pass and manipulate scores) | BE-2 `src/routers/game.py:16-19`, `tests/test_game.py:113` |
| ORM model `UserStats(user_id, correct_count, incorrect_count, points)` | BE-3 `src/models/game.py:9-21` |
| Migration v002 creates `user_stats`; **v001-v003 share ONE session** and run sequentially in the app lifespan | BE-4 `src/migrations/v002_game_stats.py`, `src/main.py:26-33` |
| `/api/user/me` exposes `points`, and returns **401** (not 404) for a missing user | BE-5 `src/routers/user.py:86, 104, 108-120` |
| Leaderboard sorts by `points DESC`, `limit` unbounded, and is **public** — no `Request` param, no bearer extraction | BE-6 `src/routers/game.py:144-172`; documented as public in `README.md:689` |
| `RateLimitService.check(key, limit, window_seconds) -> bool` **commits internally** and issues an unbounded `DELETE … WHERE expires_at < now` on every call | BE-7 `src/services/rate_limit_service.py` (`self.session.commit()` at end of `check`); call sites `src/routers/auth.py:130, 264, 419` |
| `tests/test_game.py` asserts the OLD formula (`points == 8`, `points == -16`) — full rewrite required | BE-8 `tests/test_game.py:92, 104` (block 82-163) |
| `tests/test_user_me.py` **imports `UserStats` directly** and asserts `points` — breaks at collection if the ORM class disappears | BE-9 `tests/test_user_me.py:13, 91, 97, 107, 118` |
| `tests/test_migrations.py` has **no** `user_stats` assertions (the `points` hits belong to a `users` fixture) — nothing to update there for v004 | BE-10 `tests/test_migrations.py:170, 177` |
| DB is empty (PO confirmation) — no production data to migrate | BE-11 PO clarification #2 |

### 2.2 Frontend (sibling repo — integration surface)

| Fact | Evidence |
|------|----------|
| `gameApi.submitCorrect(bits)` / `submitIncorrect(bits)` are **methods on a `gameApi` object** (not standalone exports) posting `{ bits }` to `/api/game/correct` \| `/incorrect` via `authenticatedFetch` | FE-1 `src/services/api.ts:96-135` (methods at `:103`, `:123`; bodies at `:106`, `:126`) |
| `src/types.ts` `Score {correct, wrong, points}` is **DEAD CODE** — zero importers in `src/` or `test/`. It is *not* the integration contract. | FE-2 `src/types.ts:1-5`; grep for importers returns nothing |
| The **live** `points` contracts are in four places, none of them `types.ts` | FE-3 `api.ts:23` (`LeaderboardEntry`), `api.ts:35` (`GameSubmitResponse`), `authApi.ts:63` (`UserMeResponse.points`), `utils/scoreCache.ts:20` (`CachedScore.points`) |
| Trainer implements all six directions; `BIT_OPTIONS = [4, 8, 16, 32]` (default 8). **Mode is never sent to the backend** — only `bits`. | FE-4 `src/components/HexBinTrainer.tsx:9, 11, 27, 45-49`; submit at `:83, 89` |
| **`UnicodeTrainer` already submits scores, converting bytes→bits**: `submitCorrect(bytes * 8)`. A 3-byte answer sends **`bits=24`** — uninterpretable by the *legacy* endpoint, which has no `mode` field. Under the OQ-7 decision (Rev 4) `bit_width` is bits in every mode, so 24 is exactly the **valid** unicode wire value; the ×8 conversion needs no change, only the call shape (US-38). | FE-5 `src/components/UnicodeTrainer.tsx:67, 73`; byte options `[1,2,3,4]` at `:86` |
| Width/mode lists are **triplicated** with no shared constant | FE-6 `HexBinTrainer.tsx:9, 11`, `App.tsx:14`, `Sidebar.tsx:11-13` |
| `BitByteSelector.tsx` is **generic** (`options: number[]`, emits whatever it is given) and is used only by the mobile sidebar — it is not where gating belongs | FE-7 `src/components/BitByteSelector.tsx:3-9, 21`; consumer `Sidebar.tsx:207-221` |
| Leaderboard fetch uses **plain `fetch`, no `Authorization` header**, no 401-refresh; handler has **no try/catch** and polls every **30 s**; panel is permanently mounted | FE-8 `api.ts:142-150`; `Leaderboard.tsx:26-42, 50`; `App.tsx:139-143` |
| Leaderboard UI is built around `points`: client-side sort, "PTS" column, `toLocaleString()`, aria-labels, plus a hardcoded easter egg pinning user `'Max'` to rank 1 | FE-9 `Leaderboard.tsx:29, 30-40, 82-84, 106, 152, 162` |
| Optimistic chain copies **exactly** `correct_count`/`incorrect_count`/`points` — new XP fields would be silently dropped | FE-10 `AuthContext.tsx:60-66` (`syncScoreCache`), `:186-198` (`applyGameResult`); render `Sidebar.tsx:246` (`user?.points \|\| 0`) |
| `scoreCache` is an **optimistic-render cache only, NOT an offline queue** — failed submits are discarded with a toast. No cache versioning: stale entries fail the `isCachedScore` guard and are silently dropped. | FE-11 `utils/scoreCache.ts:12, 15, 20, 27-29, 41, 80-93`; discard path `HexBinTrainer.tsx:93-96` |
| i18n strings hardcode the points vocabulary | FE-12 `translations.ts:137, 284` ("Points ✨" / "Punkte ✨") |
| No existing notion of level / XP / streak / unlock anywhere — all progression UI is new. All modes and widths are currently freely selectable. | FE-13 word-boundary grep across `src/` returns zero; `Sidebar.tsx:154-175`, `UnicodeTrainer.tsx:96-111` |
| Test setup is mature — the US-38/US-39 Vitest ACs are realistic | FE-14 `vite.config.ts:19-35` (jsdom, `@vitest/coverage-v8`, **80% thresholds enforced**); 17 test files / ~138 cases incl. `test/services/api.test.ts`, `test/utils/scoreCache.test.ts`, `test/components/{Leaderboard,HexBinTrainer,UnicodeTrainer}.test.tsx` |

---

## 3. Design Decisions (normative)

IDs are stable from Rev 1 where possible so PO references remain valid.

### D-1: Level XP curve [UPDATED per PO correction #4]

```
xp_required_for_level(n) = round_to_nearest_multiple_of_4( 100 * 1.5^(n-1) )
```

- `xp_required_for_level(n)` = XP needed to advance **from level n to level n+1** (per-level span, not cumulative threshold). Player starts at **level 1 with 0 XP**. **CONFIRMED by PO (OQ-3, Rev 4).** The internal engine function keeps this name; only the *wire field* is renamed — see OQ-8.
- **Tie rule:** exact halves round **up** (away from zero). This fires only at n=2 (150 -> 152).
- **Unlimited levels** — no cap at 13. The cap at 13 applies only to unlocks (D-2).
- **Implementation constraint (D-8):** compute with exact integer/rational arithmetic (`Fraction(100) * Fraction(3,2)**(n-1)` or equivalent integer math). Floats must never touch the XP/level path.
- **Float-drift threshold (corrected in Rev 3, R-5):** the exact rational `3^(n-1)` stops being representable in 53 bits at **n >= 35** (3^34 = 16,677,181,699,666,569 > 2^53), but rounding to a multiple of 4 absorbs that error much longer — the **first level at which a float implementation actually returns a different `req(n)` is n = 84** (exact 41,264,301,843,800,316 vs float 41,264,301,843,800,312). Consequence for testing: a float-based implementation **passes** `req(20)` and `req(50)` while violating D-8. LV-06 must therefore probe n >= 84, not n = 20/50.

Verified curve (exact, computed with rational arithmetic):

| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|----|----|----|----|----|----|
| req(n) | 100 | 152 | 224 | 336 | 508 | 760 | 1140 | 1708 | 2564 | 3844 | 5768 | 8648 | 12976 | 19460 | 29192 |
| cum to reach L(n+1) | 100 | 252 | 476 | 812 | 1320 | 2080 | 3220 | 4928 | 7492 | 11336 | 17104 | 25752 | 38728 | 58188 | 87380 |

`total_xp -> level` is derived by walking cumulative sums; level = highest L with `cum(L) <= total_xp` (with `cum(1) = 0`).

**`cum(L)` doubles as the XP floor (PO decision, OQ-4, Rev 4).** `total_xp` may never fall below `cum(level)`, which makes demotion impossible *by construction* — no `max_level_reached` column, and `level` stays a pure function of `total_xp`. Every `req(n)` is a multiple of 4, so every `cum(L)` is too: the clamp keeps `total_xp` in 4z and the D-8 invariant survives intact. At level 1, `cum(1) = 0` — new players still floor at 0 exactly as before. See D-5 for the penalty-side consequences.

### D-2: Unlock seed data [UPDATED per PO correction #4]

Binding constraints (PO): **all conversion-track unlocks land at or below level 13; all Unicode unlocks at or below level 4.** Above those levels there are no further unlocks — XP climbing only (prestige/leaderboard).

**Ladder CONFIRMED by PO (OQ-6, Rev 4): 1 / 4 / 8 / 13 for conversion widths, L1-L4 for unicode.** Unicode `bit_width` values are **bits (8/16/24/32)**, not bytes — see OQ-7. Stored as a frozen constant in `src/services/game_config.py` (code, not DB — YAGNI on a seed migration):

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

Unlocks are a **pure function of level** — no unlock-state table; server recomputes from `level`, client renders from the same seed (single source of truth exported via API, US-35/US-33 response). Because of the OQ-4 floor, `level` never decreases, so **an unlock can never be revoked** — the pure-function property holds without needing sticky state.

Note the width-value overlap across modes: `bit_width: 8` means 8 bits for `hex_to_bin` and 8 bits (= 1 byte) for `unicode`. That is intentional and unambiguous once every mode speaks bits (OQ-7) — the mode always disambiguates, and the mode-aware validator (US-32) rejects values outside the per-mode set.

Pacing sanity check — cumulative XP required to **reach** the unlock level (`cum(req(1..L-1))` from D-1), corrected in Rev 3 (R-4):

| unlock | min_level | XP to reach that level | ~answers at the x1.25 tier |
|--------|-----------|------------------------|---------------------------|
| 8-bit | L4 | **476** | ~24 correct HB-4 answers (20 XP each) |
| 16-bit | L8 | **3,220** | _(Rev 2 wrongly said 2,080 — that is `cum(req(1..6))`, i.e. reaching **L7**)_ |
| 32-bit | L13 | **25,752** | reachable quickly via high multipliers: a BD-16 answer at x2.0 awards 1,024 XP (~25 answers) |

Cross-check: LV-05 (`476 -> L4`, `25752 -> L13`) and US-41 both use these same figures and were already consistent — only the Rev 2 sanity-check line was wrong.

### D-3: Streak multipliers [IN SCOPE per PO correction #3; tier 3-4 FIXED at x1.25 per OQ-1, Rev 4]

The multiplier uses the **post-increment** streak, i.e. the streak *including* the current correct answer (**CONFIRMED by PO, OQ-5**): the 3rd consecutive correct answer already earns the 3-4 tier.

| current streak (incl. this answer) | multiplier | rational pair |
|-----------------------------------|------------|---------------|
| 1-2 | x1.0 | (1,1) |
| 3-4 | **x1.25** | (5,4) |
| 5-7 | x1.5 | (3,2) |
| 8-10 | x2.0 | (2,1) |
| 11+ | x2.5 (cap; does not grow further) | (5,2) |

**Why x1.25 and not the originally specified x1.2 (OQ-1, resolved — PO chose Option A):** x1.2 = 6/5 requires the core value to be divisible by 5, and **none of the 16** `base_xp * mode_mult` values is (verified). All 16 products are non-integral: HB-4 = 19.2, HD-8 = 115.2, BD-32 = 1228.8. x1.25 = 5/4 works because every core value is divisible by 16 (Section 4.4), so x5/4 lands on a multiple of 20 — hence a multiple of 4 — for **all 80** combinations (16 mode/width x 5 tiers, re-verified with `Fraction` in Rev 3). Balance impact vs. the specified 1.2: **+4% on one tier only**; every other tier is untouched. No rounding path exists anywhere in the engine.

### D-4: ~~Streak-to-XP multiplier deferred to a later sprint~~ [DELETED per PO correction #3]

Tombstone retained so Rev 1 references resolve. The streak multiplier is core scope (US-28/US-29). Nothing else was renumbered into D-4.

### D-5: Penalty formula [per PO correction #1; OQ-2 and OQ-4 resolved in Rev 4]

```
penalty = base_xp * mode_multiplier * 0.5          # engine returns a POSITIVE magnitude (D-8)
total_xp_new = max( cum(level), total_xp - penalty )
```

- **The mode multiplier IS part of the penalty — CONFIRMED canonical by PO (OQ-2).** The heading of PO correction #1 said the opposite; the formula wins. Consequence: the reward:risk ratio is a uniform **2:1 in every mode** (the penalty is exactly half of that mode/width's tier-1.0 gain — see XP-TP7). A wrong `bin_to_dec`-32 costs 512 XP, a wrong `hex_to_bin`-4 costs 8.
- Applied on incorrect answers. **No streak multiplier** — streak state is irrelevant to the penalty value.
- **No negative streak application**: an incorrect answer resets `current_streak` to 0; there is no negative-streak concept. `best_streak` is untouched.
- All penalty values are exact multiples of 4 (verified, Section 4.7).
- **The floor is `cum(level)`, not 0 (PO decision, OQ-4, Rev 4)** — "kein Level-Verlust soll möglich sein". `total_xp` can never drop below the cumulative XP that the current level requires, so **level demotion is impossible** while `level` remains a pure function of `total_xp`. No `max_level_reached` column, no sticky-level state, and unlocks can never be revoked. At level 1 the floor is `cum(1) = 0`, i.e. new players behave exactly as under the old "floor at 0" rule.
- **Consequence for the response: `xp_delta` is the APPLIED (post-clamp) delta**, which may be smaller in magnitude than the computed penalty. Edge case to handle in US-38's HUD: a level-1 player at 0 XP answering incorrectly gets `xp_delta: 0` — without an explicit UI treatment that reads as "nothing happened". A second consequence: a player sitting exactly on `cum(level)` is temporarily immune to penalties until they gain XP again. Both are accepted.
- **The DB CHECK stays `total_xp >= 0`** — a backstop only. `total_xp >= cum(level)` is not expressible as a SQL CHECK (it is a geometric sum), so the real clamp lives in the answer transaction and is pinned by tests (LV-08, US-41).

### D-6: Fresh schema, no backfill [UPDATED per PO correction #2; sequencing corrected in Rev 3 per R-1]

DB is empty. No `points -> xp` conversion, no data migration, no dual-write of *values*. Forward-only migrations; v002 stays in history untouched.

**The schema change is split across two migrations so that no commit leaves the repo red** (Rev 3, R-1). Rev 2 had v004 drop `user_stats` on Day 1 PM while `src/routers/game.py:10` and `tests/test_user_me.py:13` still imported the ORM — that breaks app startup (`main.py:17` imports `game_router`) and pytest collection for ~1.5 days, and made US-27's own grep AC unsatisfiable.

| Migration | Delivered with | Action |
|-----------|----------------|--------|
| **v004** (additive) | US-27, Day 1 PM | `CREATE TABLE IF NOT EXISTS user_game_profile …` only. `user_stats` is **left in place**. The legacy routers keep working against it, so the app boots and the full suite stays green. |
| **v005** (destructive) | US-34, sprint close — **same release train as the frontend switch** | `DROP TABLE IF EXISTS user_stats;` — legal only once US-33/US-36/US-37 have removed every reader/writer. |

Both are idempotent via the existing `migration_already_applied` / `schema_migrations` pattern, and both are registered in `src/main.py`'s lifespan after v003. **Coexistence window:** between v004 and v005 both tables exist; `user_stats` is written by the legacy endpoints and read by nothing new. That is an explicitly accepted, documented intermediate state — not dual-write of the economy (XP is only ever written to `user_game_profile`).

Side benefit: dropping late also removes the data-loss risk in dev/preview databases where the "DB is empty" assumption may not hold.

### D-7: Unified answer payload [UPDATED per PO correction #5]

`POST /api/game/answer` with body `{mode, bit_width, is_correct}`. Mode enum: `hex_to_bin | hex_to_dec | bin_to_dec | unicode` (direction-agnostic; the frontend owns direction). Backend infers track/base-row/multiplier/valid widths/unlock group from the mode name. The old `{track, bits}` proposal is dead.

**`bit_width` is BITS in every mode (PO decision, OQ-7 Option d, Rev 4):**

| mode | valid `bit_width` | meaning |
|------|-------------------|---------|
| `hex_to_bin` / `hex_to_dec` / `bin_to_dec` | 4, 8, 16, 32 | bits |
| `unicode` | **8, 16, 24, 32** | bits — i.e. 1, 2, 3, 4 **bytes** |

Rev 2/3 had unicode carrying byte counts (1-4) in a field named `bit_width`, which meant `bit_width: 4` signified 4 bits in one mode and 32 bits in another — a client bug would have been a *valid but wrong* answer worth 128 XP rather than a 422. Speaking bits everywhere removes that bug class: `unicode + bit_width: 4` is now a loud rejection. It also means **the frontend needs no change to its arithmetic** — `UnicodeTrainer.tsx:67` already computes `bytes * 8` (FE-5); that value is now correct as-is. Cost accepted: the unicode rows in Sections 4.2/4.4/4.7 and the D-2 seed lose the symmetric 1/2/3/4 labelling, and 24 is not a power of two.

### D-8: Exact integer arithmetic; multiples-of-4 invariant [NEW — enforcement decision for correction #1]

- No floating point anywhere in XP/level computation. Streak tiers are represented as rational pairs: **(1,1), (5,4), (3,2), (2,1), (5,2)** — the (5,4) tier is settled (OQ-1 → x1.25), so **no rounding branch and no Option-B seam exists in the code**.
- Computation: `xp = (base_xp * mode_mult * num) // den`, guarded by `assert (base_xp * mode_mult * num) % den == 0`. The guard is O(1) and encoded as a property test (TP-8, Section 4.6).
- Invariant: **every awarded XP, every penalty, every level requirement is an exact multiple of 4.** This is a hard, tested property — not a style preference. It keeps all totals, thresholds, and progress values in 4z, which makes the level arithmetic lossless forever.
- **Sign convention (pinned in Rev 3, R-9):** `xp_penalty_for_incorrect()` returns a **positive magnitude** (Section 4.7). The router negates it when populating `xp_delta` in the US-33 response. Section 4.6's XP-P01..P06 show the **wire value** (`-8`); Section 4.7 and XP-TP7 show the **engine return value** (`8`). Both are correct at their own layer.
- **Level curve drift threshold:** see the corrected n >= 84 note in D-1 (Rev 3, R-5). TP9/LV-06 must probe above it.

---

## 4. XP Economy Reference (normative tables)

### 4.1 Master formula

```
correct:   xp_awarded  = base_xp(mode, bit_width) * mode_mult(mode) * streak_mult(new_streak)
           total_xp   += xp_awarded
incorrect: xp_penalty  = base_xp(mode, bit_width) * mode_mult(mode) * 0.5   (no streak term)
           total_xp    = max( cum(level), total_xp - xp_penalty )          # OQ-4 floor, NOT 0
           current_streak = 0
xp_delta (wire)        = total_xp_new - total_xp_old                        # post-clamp, signed
```

`cum(level)` is the cumulative XP that the current level requires (D-1 row 2, with `cum(1) = 0`). Because the floor is a multiple of 4 and `total_xp` is one, `xp_delta` stays in 4z even when the penalty is truncated — see D-8.

### 4.2 Base XP table (PO-specified, all divisible by 4)

Columns are **width slots**, not a shared bit count — each mode's slot values are given in the row label (OQ-7: unicode speaks bits 8/16/24/32, so its slots do not align by bit count with the conversion modes').

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 16 | 32 | 64 | 128 |
| hex_to_dec (4 / 8 / 16 / 32) | 24 | 48 | 96 | 192 |
| bin_to_dec (4 / 8 / 16 / 32) | 32 | 64 | 128 | 256 |
| unicode (**8 / 16 / 24 / 32**) | 16 | 32 | 64 | 128 |

### 4.3 Mode multipliers

| mode | multiplier |
|------|-----------|
| hex_to_bin | x1 |
| hex_to_dec | x2 |
| bin_to_dec | x4 |
| unicode | x1 |

### 4.4 Effective gain (`base_xp * mode_mult`) — the streak-independent core

Verified: every value is divisible by 16 — the property that makes the x5/4 tier exact (D-3).

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 16 | 32 | 64 | 128 |
| hex_to_dec (4 / 8 / 16 / 32) | 48 | 96 | 192 | 384 |
| bin_to_dec (4 / 8 / 16 / 32) | 128 | 256 | 512 | 1024 |
| unicode (**8 / 16 / 24 / 32**) | 16 | 32 | 64 | 128 |

### 4.5 Streak curve [OQ-1 RESOLVED — PO chose Option A, x1.25]

**Normative curve (multiplier of the post-increment streak, OQ-5):**

| streak | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11+ |
|--------|---|---|---|---|---|---|---|---|---|----|-----|
| mult | 1.0 | 1.0 | **1.25** | **1.25** | 1.5 | 1.5 | 1.5 | 2.0 | 2.0 | 2.0 | 2.5 (cap) |

Originally specified as x1.2 for the 3-4 tier, which is **arithmetically impossible** under the multiples-of-4 invariant: x1.2 = 6/5 requires the core value to be divisible by 5, and none of the 16 values in 4.4 is. All 16 products are non-integral:

```
HB-4   x 1.2 = 19.2     HD-4   x 1.2 = 57.6     BD-4   x 1.2 = 153.6     UNI-8  x 1.2 = 19.2
HB-8   x 1.2 = 38.4     HD-8   x 1.2 = 115.2    BD-8   x 1.2 = 307.2     UNI-16 x 1.2 = 38.4
HB-16  x 1.2 = 76.8     HD-16  x 1.2 = 230.4    BD-16  x 1.2 = 614.4     UNI-24 x 1.2 = 76.8
HB-32  x 1.2 = 153.6    HD-32  x 1.2 = 460.8    BD-32  x 1.2 = 1228.8    UNI-32 x 1.2 = 153.6
```

The PO note's claim that "no rounding is needed because base values are chosen to make multipliers work cleanly" holds for x1.0/x1.5/x2.0/x2.5 (verified: all 64 resulting values are exact multiples of 4) and for **x1.25** — but not for x1.2.

**Decision (OQ-1 → Option A): tier 3-4 = x1.25.** Rejected alternatives, recorded so the question is not re-litigated: *Option B* (keep x1.2, round half up to the nearest multiple of 4 — costs a special-case rounding path in an otherwise exact engine plus a divergent golden table) and *Option C* (keep x1.2, relax the invariant from "multiple of 4" to "integer" — contradicts PO correction #1). With x1.25 the invariant holds **by construction** for all 80 combinations, and no rounding exists anywhere in the engine. Balance impact: +4% on one tier.

Everything in Sections 4.6-4.9 is computed with x1.25 and is now final — **no OQ-1 markers or Option-B seams remain**.

### 4.6 Golden XP test cases (US-29; heart of the sprint)

Named spot-checks, **final** (x1.25 for the 3-4 tier per OQ-1; unicode widths in bits per OQ-7). All 19 computable values were independently recomputed with `Fraction` in Rev 3 and match.

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

Penalty spot-checks (streak-independent, verified multiples of 4). **Values below are the signed wire value (`xp_delta`)**; `xp_penalty_for_incorrect()` returns the positive magnitude — see the sign convention in D-8 and Section 4.7 (Rev 3, R-9):

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

XP-P06's width is relabelled to bits per OQ-7 (2 bytes = 16 bits); the penalty value is unchanged.

> These are **gross** penalties as the engine computes them. The value actually applied to `total_xp` may be truncated by the OQ-4 floor at `cum(level)`; the applied delta is asserted in LV-09 and US-41, not here.

Property tests (parametrize over the FULL matrix — 16 mode/width x tiers):

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

### 4.7 Penalty table (`base_xp * mode_mult * 0.5`)

**Positive magnitudes** — the engine's return value. `xp_delta` on the wire is the negation of these (US-33). Verified: all 16 are integers and multiples of 4.

| mode (valid `bit_width` values) | slot 1 | slot 2 | slot 3 | slot 4 |
|---------------------------------|--------|--------|--------|--------|
| hex_to_bin (4 / 8 / 16 / 32) | 8 | 16 | 32 | 64 |
| hex_to_dec (4 / 8 / 16 / 32) | 24 | 48 | 96 | 192 |
| bin_to_dec (4 / 8 / 16 / 32) | 64 | 128 | 256 | 512 |
| unicode (**8 / 16 / 24 / 32**) | 8 | 16 | 32 | 64 |

### 4.8 Level curve (see D-1 table in Section 3)

Level test cases (US-30):

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

### 4.9 Unlock seed (see D-2 table in Section 3)

Unlock test cases (US-31):

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

## 5. User Stories

Estimates assume 2 backend-dev instances + 1 frontend-dev + tdd-guide + reviewers. Branch per story: `feature/us-XX-...` off the previous merged branch (house cascade rule). Total ≈ 9.25 person-days (≈ 7.75 backend, ≈ 1.5 frontend) against ~4 calendar days — see the **buffer note** at the end of Section 6: backend has zero slack, and the descope order is fixed (US-39 first, never US-32/US-41).

### Wave 0 — Foundations

#### US-25: Game economy config registry
**P0 | LOW | depends: none | backend-dev | 0.25d**
Single frozen source of truth for the whole economy: mode enum (`hex_to_bin`, `hex_to_dec`, `bin_to_dec`, `unicode`), per-mode valid widths — **all in bits** (OQ-7 resolved): 4/8/16/32 for conversion modes, **8/16/24/32** for unicode —, base XP table (4.2), mode multipliers (4.3), streak tiers as rational pairs (4.5, **frozen per OQ-1: 3–4 → x1.25 = (5,4)**, no runtime seam), penalty factor (1/2), unlock seed (D-2). File: `src/services/game_config.py` with `@dataclass(frozen=True)` / `Final` structures. AC: every constant traceable to a Section 4 table comment; import-side-effect free; unit tests cover table completeness (16 entries, all %4==0, div16 core) and pin the per-mode width sets incl. unicode's 8/16/24/32.

#### US-26: Domain models — UserGameProfile + schemas
**P0 | MEDIUM | depends: US-25 | backend-dev | 0.5d**
**Add** `UserGameProfile(user_id PK/FK CASCADE, total_xp, level, current_streak, best_streak, correct_count, incorrect_count, updated_at)` to `src/models/game.py`; CHECK constraints `total_xp >= 0`, `level >= 1`, streaks `>= 0`; index on `total_xp DESC`. Pydantic schemas in `src/models/game_schemas.py`: `AnswerRequest` (Literal mode enum, width validated per mode, `is_correct: bool`), `AnswerResponse`, `ProfileResponse`, `LeaderboardEntryV2`.

**Do NOT remove `UserStats` in this story** (Rev 3, R-1). Rev 2 said "Replace `UserStats` ORM … Remove `UserStats` imports from `src/routers/user.py` (compile-time break — fix in US-37)". That is unschedulable: `src/routers/game.py:10` and `tests/test_user_me.py:13` still import it, and `main.py:17` imports `game_router` — deleting the class stops the app from booting and breaks pytest *collection*, for the ~1.5 days until US-33/US-37 land. `UserStats` therefore survives until US-34 (see D-6's two-migration split); the only story allowed to delete it is US-34.

`bit_width` semantics per mode (OQ-7 **resolved**, Rev 4): the field is **bits in every mode**. Valid values: 4/8/16/32 for `hex_to_bin`/`hex_to_dec`/`bin_to_dec`, 8/16/24/32 for `unicode`. The frontend's existing `bytes * 8` mapping already produces exactly this — no client-side unit change (see US-38). The CHECK `total_xp >= 0` is a **backstop only**: the real floor under OQ-4 is `cum(level)`, enforced in the engine (US-30/LV-09); the DB constraint can never fire before the engine floor does.

AC:
1. Model/table/schema trio matches D-6/D-7.
2. `UserStats` still importable and still used by the legacy routers — **`pytest` green and the app boots at this commit** (the repo is never red between stories).
3. `UserGameProfile` and `UserStats` coexist without name/table collision.

#### US-27: Migration v004 — additive XP schema, NO backfill [CORRECTED per PO #2; re-sequenced Rev 3 per R-1]
**P0 | MEDIUM | depends: US-26 | backend-dev | 0.5d**
`src/migrations/v004_xp_schema.py`: `CREATE TABLE IF NOT EXISTS user_game_profile ...` (as US-26), register in `schema_migrations`, wire into `src/main.py` lifespan after v003. **No `DROP` in v004** — `user_stats` stays until v005 (US-34). **Explicitly out: any data migration, backfill, value dual-write, or compatibility view** — the DB is empty (D-6).

**Note (Rev 3, R-10):** Rev 2 instructed "Update `tests/test_migrations.py` assertions that expect `user_stats`". No such assertions exist — `tests/test_migrations.py:170,177` mention a `points` column on a `users` *fixture*, unrelated to `user_stats`. Nothing to update there; add **new** v004 tests following the existing v003 pattern (`tests/test_migrations.py:337-386`).

AC:
1. v004 is idempotent (re-run after applied -> no-op, per the existing `migration_already_applied` pattern) and records exactly one `schema_migrations` row.
2. After lifespan boot: `user_game_profile` present with CHECK constraints and the `total_xp` index — **and `user_stats` still present** (v005 removes it).
3. **The full suite is green and the app boots at this commit** — the legacy `/correct`, `/incorrect`, `/leaderboard`, `/api/user/me` endpoints still function against `user_stats`.
4. Cascade: deleting a user removes the `user_game_profile` row (FK test).
5. A fresh database (no `user_stats`) reaches the same end state as a migrated one — v004 does not depend on v002's table existing.

_(The Rev 2 AC-3 grep proof moved to US-34/v005, where it is actually satisfiable — see Section 7 gate #8.)_

#### US-28: Streak tracking service
**P0 | MEDIUM | depends: US-26, US-27 | backend-dev + tdd-guide | 0.5d**
Streak lifecycle owned by `src/services/game_service.py` (or `streak_service.py` if file size demands): correct -> `current_streak += 1`, `best_streak = max(best_streak, current_streak)`; incorrect -> `current_streak = 0` (best untouched; no negative streak, D-5). Streak value used for the multiplier is the **post-increment** value (OQ-5). AC:
1. All mutations happen inside the answer transaction with `SELECT ... FOR UPDATE` on the profile row (anti race: two concurrent submissions of the same user must serialize; test with interleaved sessions).
2. **Exactly one commit per answer request, at the very end** (Rev 3, R-2). Nothing called inside the transaction may commit or roll back behind the service's back — otherwise the `FOR UPDATE` lock is released early and AC-1 is void. This is the constraint that collides with `RateLimitService.check()` (BE-7); the resolution lives in US-32 and is asserted by a dedicated test here: interleave two concurrent answers and assert the second sees the first's committed streak, never a pre-commit snapshot.
3. First-ever answer creates the profile row lazily (mirrors the legacy lazy `UserStats` creation in `routers/game.py:72-79`, which stays untouched until US-34).
4. Unit tests: reset on incorrect, best-streak preservation, lazy creation, single-commit/serialization under concurrency.

### Wave 1 — XP engine (the heart)

#### US-29: XP calculation engine [CORRECTED per PO #1 — normative formulas + tables]
**P0 | HIGH | depends: US-25 | tdd-guide writes tests FIRST, backend-dev implements | 1d**
Pure functions, no DB, no framework, in `src/services/xp_engine.py`:
```
xp_for_correct(mode, bit_width, streak) -> int
xp_penalty_for_incorrect(mode, bit_width) -> int
streak_multiplier(streak) -> Fraction            # tiers per D-3
xp_required_for_level(n) -> int                  # D-1 (could live in US-30; same file)
```
Rules (D-1..D-8, Section 4): `final_xp = base_xp * mode_mult * streak_mult` (cap x2.5, integer-exact, multiple of 4); penalty = `base_xp * mode_mult * 0.5`, never streak-scaled, never negative-streak; invalid (mode, width) combos raise. **Test spec = Section 4.6 in full** (XP-G01..G22, XP-P01..P08, XP-TP1..TP9) — tdd-guide parametrizes TP1-TP5/TP8 over the complete 16x5 matrix. OQ-1 is **resolved** (x1.25 = (5,4) tier, 4.5 final): no conditional tier, no `# OQ-1` seam in the implementation — the golden tables in 4.4/4.6 are the contract as written. AC: all Section 4.6 tests green; zero floats in the module (TP9); coverage of `xp_engine.py` >= 95%.

#### US-30: Level progression
**P0 | MEDIUM | depends: US-29 | tdd-guide -> backend-dev | 0.5d**
`level_from_xp(total_xp) -> int`, `xp_progress(total_xp) -> (xp_in_level, xp_required_next_level)` per D-1; unlimited levels; no cap at 13 (only unlocks cap). Float-free computation (Fraction or integer walk). Test spec = Section 4.8 (LV-01..LV-10). AC: profile `level` column recomputed inside the answer transaction after XP change; **level is monotonically non-decreasing** — XP loss floors at `cum(level)` (`total_xp_new = max(cum(level), total_xp - penalty)`, OQ-4 resolved), so demotion cannot occur and no sticky `max_level` state is needed (LV-08/LV-09).

#### US-31: Unlock system
**P1 | MEDIUM | depends: US-25, US-30 | tdd-guide -> backend-dev | 0.5d**
`unlocks_for_level(level)`, `newly_unlocked(old_level, new_level)`, `is_unlocked(mode, width, level)` from the D-2 seed. Enforcement point: the answer path rejects locked content (UN-05) — decision: **403 with detail `"Content locked until level X"`** (message includes the required level; frontend uses it for gating copy). Test spec = Section 4.9 (UN-01..UN-07). AC: caps 13/4 verified by UN-01; no unlock-state persisted (pure function of level); unlocks are never revoked (UN-07 — free consequence of LV-08, no extra mechanism).

#### US-32: Input validation + anti-XP-farming
**P1 | MEDIUM | depends: US-26 | security-reviewer reviews, backend-dev implements | 0.5d**
- Pydantic: `mode` Literal enum; `bit_width` validated **per mode, in bits everywhere** (OQ-7 resolved: 4/8/16/32 for conversion modes, 8/16/24/32 for unicode) -> 422 on mismatch (e.g. `unicode` + `bit_width: 4` rejected — 4 is *not* a unicode width; `unicode` + `bit_width: 8` is **valid** slot 1); `is_correct` required bool; extra fields forbidden (`model_config extra="forbid"`).
- Server never trusts client-computed XP/points/streak — the client sends **only** the three fields (D-7).
- Rate limit on `/api/game/answer` reusing `rate_limit_service` + v003 infrastructure (existing pattern from auth endpoints); sane ceiling (proposal: 120 answers/min/user, tunable constant in US-25 registry) -> 429 + `Retry-After`.
- Error responses leak nothing beyond the 422/403/429 semantics.

**Transaction-boundary constraint (Rev 3, R-2 — CRITICAL; DECIDED in Rev 4: Option A).** `RateLimitService.check()` ends with `await self.session.commit()` (BE-7). Called on the *answer* session it commits mid-request and **releases the `SELECT … FOR UPDATE` lock** that US-28 AC-1 depends on, silently voiding the atomicity guarantee and the US-41 concurrency test. The auth call sites (`auth.py:130, 264, 419`) are unaffected because those handlers have no row lock to hold.

- **Option A (CHOSEN): give the rate limiter its own short-lived session** — `async with AsyncSessionLocal() as rl_session: RateLimitService(rl_session).check(...)`, executed *before* the answer transaction opens. Zero changes to a service three auth endpoints depend on; the limiter's commit cannot touch the answer transaction. A 429 then never opens a row lock at all. Chosen over B because it leaves a security-critical shared service untouched (no re-verification of the three auth call sites) and makes the 429 path lock-free by construction — KISS.
- **Option B (rejected): refactor `check(..., commit: bool = True)`** (or split record vs. commit) so the caller owns the boundary. Cheaper at the call site, but touches a security-critical shared service and needs the three auth call sites re-verified.

Second defect in the same service: `check()` runs `DELETE FROM rate_limit_attempt WHERE expires_at < now` on **every** call. At 120 answers/min/user that is a full delete scan per answer on the hottest endpoint in the project. Move the cleanup off the request path (startup + a periodic task, or amortize it to ~1/N of calls); the answer path only needs the `COUNT`.

AC:
1. Negative tests for each validation rule (per-mode width whitelist, extra fields, non-bool `is_correct`, unknown mode).
2. **A test proving the answer transaction is not committed early**: with the limiter wired in, interleave two concurrent answers for one user and assert serialized streak/XP application (pairs with US-28 AC-2).
3. **A test proving a 429 leaves no partial state** — no profile row mutation, no streak change.
4. Rate-limit cleanup is not on the per-request path (assert `check()` issues no `DELETE` for the answer key).
5. security-reviewer APPROVE on this diff (this is the sprint's attack surface: XP is the leaderboard currency).

### Wave 2 — API surface

#### US-33: POST /api/game/answer [CORRECTED per PO #5]
**P0 | HIGH | depends: US-28, US-29, US-30, US-31, US-32 | backend-dev; tdd-guide integration tests | 1d**
Single endpoint replacing `/correct` + `/incorrect`. Auth via existing `_extract_bearer_user_id`. Request:
```json
{ "mode": "hex_to_bin", "bit_width": 8, "is_correct": true }
```
Response (200) — this example is a **normative golden fixture**, every value derived below:
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

Derivation (verify these hold in the schema test): `xp_delta` 40 = HB-8 @ streak 3 (32 x 1.25, XP-G04). `cum(6)` = 1,320 and `cum(7)` = 2,080, so `total_xp` 1,320 → `level` 6 (boundary inclusive, LV-05/LV-07). `xp_in_level` = 1,320 - `cum(6)` = **0** (the player landed exactly on the threshold). `xp_required_for_next_level` = `req(6)` = **760** (span 6→7). `newly_unlocked` = `[]` because the D-2 seed has no unlock at L6 — correct even though this *was* a level-up, which is exactly the case US-38's toast must handle without rendering an empty popup.

`newly_unlocked` element shape — **pinned in Rev 3 (R-9)**, identical to US-35's `unlocks` entries so US-31's `newly_unlocked()` and US-38's toast consume one type:
```json
"newly_unlocked": [ { "mode": "hex_to_bin", "bit_width": 16, "min_level": 8 } ]
```

> **Field naming — OQ-8 resolved (Rev 4):** the wire field is `xp_required_for_next_level`, holding the span to the *next* level (760 at L6). The internal engine function keeps its D-1 name `xp_required_for_level(n)` — only the response key is renamed. Used identically by US-33 and US-35.
Semantics: `xp_delta` is the **signed, post-clamp** delta `total_xp_new - total_xp_old` (4.1). It is +gain on correct and -penalty on incorrect — **except at the OQ-4 floor**, where a blocked penalty yields `xp_delta: 0` (LV-09b); clients must not assume a nonzero delta on `is_correct: false`. `newly_unlocked` non-empty only on level-ups (from US-31); all figures post-transaction; one HTTP call = one atomic DB transaction (US-28 locking). Status codes: 401 no/invalid token, 404 user gone, 403 locked content (UN-05), 422 invalid payload (US-32), 429 rate-limited. **Not** `{track, bits}`. AC: contract matches above byte-for-byte (schema test); happy paths for all 4 modes and both outcomes; full Section 4.6 engine cases reachable end-to-end through the endpoint for at least one representative per mode.

#### US-34: Remove legacy endpoints + migration v005 (drop `user_stats`)
**P1 | LOW | depends: US-33 merged AND US-38 deployed together | backend-dev | 0.25d**
Delete `/api/game/correct`, `/api/game/incorrect`, `GameActionRequest`, `record_*` handlers, and the old assertions in `tests/test_game.py` (rewritten to `/answer`). **Then, and only then:** delete the `UserStats` ORM class (`src/models/game.py`), fix `tests/test_user_me.py:13`, and ship **`src/migrations/v005_drop_user_stats.py`** — `DROP TABLE IF EXISTS user_stats;`, idempotent, registered in `schema_migrations`, wired into the lifespan after v004 (D-6).

Sequencing constraint: removal lands in the same release train as the frontend switch; until then the endpoints and the table co-exist, but `/answer` is the only documented endpoint. This story is the **only** place allowed to delete `UserStats` (Rev 3, R-1).

AC:
1. `grep -rn "game/correct\|game/incorrect" src/ ../hexToBinaryTrainer2Frontend/src/` empty on both sides at sprint close.
2. **`grep -rnE "submitCorrect|submitIncorrect" ../hexToBinaryTrainer2Frontend/src/` empty** (Rev 3, R-7). AC-1 alone is not sufficient: `UnicodeTrainer.tsx:67,73` and `HexBinTrainer.tsx:83,89` call the *method*, never the URL, so a URL-only grep passes while live call sites dangle.
3. `grep -rnE "\buser_stats\b|\bUserStats\b" src/ tests/` returns only `src/migrations/v002_game_stats.py` (historical record) and `src/migrations/v005_drop_user_stats.py`.
4. v005 is idempotent; after boot on a migrated DB `user_stats` is absent and `user_game_profile` intact; the full suite is green.
5. Deleting a user still cascades (the FK test from US-27 AC-4, re-run post-drop).

#### US-35: GET /api/game/profile
**P1 | MEDIUM | depends: US-33 | backend-dev | 0.25d**
Returns the caller's full economy state: `total_xp, level, xp_in_level, xp_required_for_next_level, current_streak, best_streak, correct_count, incorrect_count, unlocks: [{mode, bit_width, min_level}]` (seed export, D-2, so the client never hardcodes the table). 404/401 semantics as US-33.

`unlocks` is the **full** set for the caller's level; `newly_unlocked` (US-33) is the **delta** from this answer. Both use the identical `{mode, bit_width, min_level}` element type — pinned in Rev 3 (R-9) so US-31's two functions and US-38/US-39's consumption agree on one shape. Field naming per OQ-8 (resolved): `xp_required_for_next_level`, consistently across both endpoints.

AC:
1. Profile fields consistent with the last `/answer` response (shared fixtures).
2. `unlocks` for L1 = the four 4-bit/1-byte entries; for L13 = all 16 (mirrors UN-02 through the HTTP layer).
3. `unlocks` element shape is byte-identical to `newly_unlocked`'s (schema test).

#### US-36: Leaderboard v2 — by total XP (stays public)
**P1 | MEDIUM | depends: US-27 · ships in the US-38 release train (breaking entry shape) | backend-dev | 0.5d**
**Stays PUBLIC — no authentication (PO decision 2026-10-27, Rev 3 / R-3).** Rev 2 had "Auth required (parity with other game endpoints)" buried as a parenthetical here. That would have been a breaking change to an endpoint `README.md:689` explicitly documents as *"Public — no authentication required"*, and the frontend calls it with plain `fetch` and no `Authorization` header (FE-8) inside a handler with no try/catch that polls every 30 s — the result would have been an unhandled-rejection loop and an eternal spinner, with no story to fix it. Rationale for keeping it public: an entry carries only `username` plus game numbers, no PII and no user-controlled content; the leaderboard is a public showcase. `limit` bounds (below) are the abuse control, not auth.

`GET /api/game/leaderboard` keeps its path, its public visibility and its wrapper (`{entries: [...]}`) but sorts by `total_xp DESC`, tiebreak `incorrect_count ASC`, then `user_id ASC` (stable ordering). Entry: `{user_id, username, total_xp, level, correct_count, incorrect_count}` (drops `points`). `limit` validated `ge=1, le=100`, default 50 (fixes the unbounded-limit smell in BE-6). Reads `user_game_profile` (created additively by v004 — no dependency on v005).

**The entry-shape change is still breaking for the client** even without the auth change: FE-9 sorts by `points`, renders a "PTS" column and calls `entry.points.toLocaleString()`. So this story **lands in the same release train as US-38's leaderboard migration** (the coupling US-34 already has). Do **not** paper over it with a deprecated `points` alias field — YAGNI, and it would need removing again in US-34.

AC:
1. Seeded fixture ordering test incl. tie cases (both tiebreak levels).
2. `limit` bounds tested (0, 1, 100, 101, absent).
3. **Endpoint is reachable with no `Authorization` header** — a regression test pinning the public decision, so a future "parity" refactor cannot silently re-break it.
4. Response contains no `points` field and no email/PII field.

#### US-37: Adapt /api/user/me + delete-point cleanup
**P1 | MEDIUM | depends: US-26, US-27 · ships in the US-38 release train (breaking response shape) | backend-dev | 0.25d**
`src/routers/user.py:86, 108-120`: replace the `points` field of `UserMeResponse` with `{total_xp, level, current_streak, best_streak}` (null-safe on missing profile -> zeros/level 1, preserving the lazy-profile contract). Switch the query from `UserStats` to `UserGameProfile`. Update `tests/test_user_me.py` (which asserts `points` at `:91, 107, 118` — BE-9).

**Do not delete the `UserStats` class here** (Rev 3, R-1) — `src/routers/game.py` still imports it until US-34; only its *use in this file* is removed. `tests/test_user_me.py:13` drops its `UserStats` import as part of this story.

**Status-code alignment (Rev 3, R-10/L3):** `/api/user/me` raises **401** for a missing user (`user.py:104`) while every game endpoint raises **404** for the same condition. Align to **404** here, so the AC below ("agree field-for-field") is not contradicted by divergent error semantics on the identical condition. 401 stays reserved for missing/invalid token.

**Frontend coupling:** `UserMeResponse.points` is a second live contract (FE-3) feeding `Sidebar.tsx:246`, `AuthContext.tsx:60-66`, `scoreCache.ts:20` and the i18n strings at `translations.ts:137,284`. This story therefore lands in the **same release train as US-38**, like US-34/US-36.

AC:
1. `/api/user/me` and `/api/game/profile` agree field-for-field on economy numbers (shared fixtures).
2. Missing profile -> `total_xp` 0, `level` 1, streaks 0 (no 500).
3. Missing user -> **404**; missing/invalid token -> **401**.
4. No `points` key anywhere in the response.

### Wave 3 — Frontend, docs, gate

#### US-38: Frontend — unified submit + XP/streak/level HUD
**P1 | MEDIUM-HIGH (cross-repo) | depends: US-33 contract frozen | frontend-dev + tdd-guide (Vitest) | 1d**
**Start Day 2 against a mocked `/answer`** (Rev 3, R-8) — the contract in US-33 is already frozen in this document, so there is no reason to wait for the backend to merge. Freeze it as a typed stub + MSW/mock handler on Day 1.

**Real file surface (Rev 3, R-6/R-7 — the Rev 2 list was wrong).** All paths relative to `hexToBinaryTrainer2Frontend/`:

| Where | What changes | Evidence |
|-------|--------------|----------|
| `src/services/api.ts` | `gameApi.submitCorrect(bits)` / `submitIncorrect(bits)` (`:103`, `:123`) → one `gameApi.submitAnswer(mode, bit_width, is_correct)` hitting `/api/game/answer`; `GameSubmitResponse` (`:30-36`) → economy response type; `LeaderboardEntry` (`:23`) drops `points`, gains `total_xp`/`level` | FE-1, FE-3 |
| `src/services/authApi.ts` | `UserMeResponse.points` (`:63`) → economy fields (backend side is US-37) | FE-3 |
| `src/context/AuthContext.tsx` | `syncScoreCache` (`:60-66`) and **`applyGameResult` (`:186-198`)** — the latter copies *exactly* `correct_count`/`incorrect_count`/`points`, so `xp_delta`/`level`/`current_streak` would be **silently dropped** and a missing `points` renders `PTS 0` instead of erroring | FE-10 |
| `src/utils/scoreCache.ts` | `CachedScore.points` (`:20`) → economy shape **plus a cache-version bump**: there is no versioning today, so stale localStorage entries fail `isCachedScore` (`:80-93`) and are silently discarded at `:41` | FE-11 |
| `src/components/HexBinTrainer.tsx` | submit at `:83, 89` → `submitAnswer`; map the 6 UI directions (`:9`) to the 4 backend modes | FE-4 |
| `src/components/UnicodeTrainer.tsx` | **Keep the ×8** (OQ-7 resolved, Rev 4 — reverses the Rev 3 R-7 instruction): `bytes * 8` at `:67,73` already produces exactly the wire values the new contract wants (bytes 1–4 → `bit_width` 8/16/24/32). Only the call shape changes: `submitCorrect(bytes * 8)` → `submitAnswer("unicode", bytes * 8, isCorrect)`. The legacy problem was never the ×8 — it was that the old endpoint had no `mode` to interpret it with | FE-5 |
| `src/components/Leaderboard.tsx` | client-side sort by `points` (`:29`), "PTS" column (`:82-84`), `entry.points.toLocaleString()` (`:162`), aria-labels (`:106,152`) → XP/level columns. Preserve the `'Max'` rank-1 easter egg (`:30-40`) or drop it deliberately. **Add the missing try/catch + error state** (`:26-42`) — with a 30 s poll (`:50`) and a permanently mounted panel (`App.tsx:139-143`), any failure (500, network, CORS) currently produces an unhandled rejection loop and an eternal spinner. This bug is independent of the auth decision and must be fixed regardless. | FE-8, FE-9 |
| `src/components/Sidebar.tsx` | `user?.points \|\| 0` (`:246`), "PTS" label; `BIT_OPTIONS`/`BYTE_OPTIONS` (`:11-13`) feed US-39's gating | FE-10, FE-6 |
| `src/utils/translations.ts` | `"Points ✨"` / `"Punkte ✨"` (`:137, 284`) → XP/level/streak vocabulary (both locales) | FE-12 |
| `src/types.ts` | **Leave alone or delete.** `Score` (`:1-5`) is dead code with zero importers — do not spend effort migrating it (Rev 2 listed it as the integration contract; it is not) | FE-2 |
| new HUD component | renders `level`, `xp_in_level / xp_required_for_next_level` progress, streak flame (current + best), level-up toast consuming `newly_unlocked`. **Toast must be a no-op when `newly_unlocked` is empty** — a level-up with no unlock is a normal case (US-33's golden fixture is exactly that: L6, `[]`). Progress bar must render `xp_in_level == 0` after a floor-clamped penalty as 0%, never negative (LV-10) | US-33 |

Direction→mode mapping (6 UI directions → 3 conversion modes; direction stays UI-only per Section 1): `HexToBin`/`BinToHex` → `hex_to_bin`; `HexToDec`/`DecToHex` → `hex_to_dec`; `BinToDec`/`DecToBin` → `bin_to_dec`; plus `unicode`. Optimistic-local vs server-truth: **server response always wins**.

AC:
1. Vitest coverage for the mapper: all 6 directions → correct mode, and **bytes 1-4 → `bit_width` 8/16/24/32 for unicode (asserting the ×8 mapping is preserved — OQ-7)**; the old `submitCorrect`/`submitIncorrect` methods are gone (pairs with US-34 AC-2).
2. HUD renders from a mocked `/answer` response, including the empty-`newly_unlocked` level-up case.
3. Error paths: 403 locked (shows the required level from the detail message), 429 (backs off, no toast spam), network failure in `Leaderboard.tsx` (error state, spinner cleared, poll continues).
4. `applyGameResult` passes through **all** economy fields — a test that fails if a new response field is dropped.
5. Existing 80% coverage thresholds (`vite.config.ts:19-35`) still enforced and met; the 5 already-affected suites (`api`, `scoreCache`, `Leaderboard`, `HexBinTrainer`, `UnicodeTrainer`) updated, not skipped.

#### US-39: Frontend — unlock-gated width/mode selection
**P1 | MEDIUM | depends: US-35, US-38 | frontend-dev | 0.5d**
Render locked options from the profile `unlocks` seed (US-35) with a "Level X" badge; selecting a locked option is impossible client-side; defensive: a 403 from `/answer` still refreshes the profile and surfaces the unlock hint (server stays authoritative — US-31/UN-05 is the real gate).

**Correct file targets (Rev 3, R-6).** Rev 2 named `BitByteSelector.tsx` + "trainer mode tabs". `BitByteSelector.tsx` is a **generic** presentational component (`options: number[]`, emits whatever it is handed — FE-7) used only by the mobile sidebar; putting gating logic inside it would either hardcode game rules into a reusable widget or miss every other render path. The actual option lists are **triplicated with no shared constant** (FE-6):

| File | Constant / render path |
|------|------------------------|
| `src/components/HexBinTrainer.tsx:9, 11, 27` | `Mode` union + `BIT_OPTIONS = [4,8,16,32]` (desktop trainer) |
| `src/App.tsx:14` | `HexBinMode` union (third copy) |
| `src/components/Sidebar.tsx:11-13, 154-175, 207-221` | `HEX_BIN_MODES`, `BIT_OPTIONS`, `BYTE_OPTIONS`; renders all modes ungated and feeds `BitByteSelector` |
| `src/components/UnicodeTrainer.tsx:86, 96-111` | `byteOptions = [1,2,3,4]`, rendered ungated |

**Prerequisite step inside this story:** extract one shared module (e.g. `src/constants/gameModes.ts`) exporting the mode list, per-mode width lists and the direction→mode mapper, and have all four files consume it. Without this, gating has to be applied consistently in three places that will drift — and the mapper from US-38 becomes a fourth copy.

`BitByteSelector` then only needs an optional per-option `locked`/`minLevel` prop (still generic — it renders what it is told, the caller decides).

AC:
1. Gating matrix test per the D-2 seed: all 16 (mode, width) combos × boundary levels (L1/L3/L4/L7/L8/L12/L13 and L1-L4 for unicode) — unlocked exactly at `min_level`, locked at `min_level - 1`.
2. A locked option is not selectable (keyboard + pointer), and shows its `min_level`.
3. **Single source of truth:** no width/mode literal array remains in `HexBinTrainer.tsx`, `App.tsx` or `Sidebar.tsx` — assert via the shared module being the only importer (guards against the triplication re-growing).
4. A 403 from `/answer` refreshes the profile and surfaces the unlock hint rather than dead-ending.
5. i18n strings for lock copy in **both** locales (`translations.ts`).

#### US-40: Documentation
**P2 | LOW | depends: US-33/US-35/US-36 | doc-updater | 0.25d**
House rule #4 (README updated with every merged PR) plus the following **concrete** checklist — Rev 2 said only "README updated", which is how the stale passages below survive (Rev 3, R-10/M6):

**`README.md`** (line refs as of 2026-10-27):
- `:37-44` — the feature bullets still describe `+bits` / `-4×bits` scoring and "Leaderboard: Top N users sorted by points (DESC)".
- `:482` — "the `user_stats` row is created lazily" → `user_game_profile`.
- `:494-498`, `:669-675` — example payloads with `"points": 36` and the worked example "13 correct answers at `bits=4` → +52, one incorrect → −16" → replace with the US-33 golden fixture.
- `:647-687` — the `POST /api/game/correct` / `incorrect` sections → `POST /api/game/answer`; add `GET /api/game/profile`.
- `:689` — leaderboard: **keep "Public — no authentication required"** (the R-3 decision), but update "sorted by points (DESC)" → `total_xp`.
- `:1005` — the test-file index.

**`docs/architecture.md`** — game-community section. Note `tests/test_architecture_docs.py` asserts on this file's *content* (communities, isolated nodes, thin communities, resolved gaps, "API Endpoints" heading, README→architecture link); edits must keep those assertions passing.

**`docs/backlog.md`** — record the two items Section 1 defers as tech debt: (a) server-issued challenge nonces / HMAC-signed answers if rate limiting proves insufficient, (b) the answer-history event table. Rev 2 promised these were "tracked" but no story filed them.

**`docs/xp-economy.md`** (new) — mirror Section 4's normative tables so they are code-adjacent rather than doc rot, cross-referenced from `src/services/game_config.py` comments (US-25) and linked from README.

**OpenAPI** — verify the FastAPI annotations are complete for `/answer`, `/profile`, `/leaderboard`, including the per-mode `bit_width` documentation: **bits in every mode** (OQ-7 resolved), 4/8/16/32 for conversion modes, 8/16/24/32 for unicode.

**Changelog entry** — legacy scoring removed; two-step migration (v004 additive, v005 drop); no backfill (fresh installs only — DB was empty); leaderboard remains public.

#### US-41: End-to-end economy integration suite
**P0 | MEDIUM | depends: US-27..US-37 | tdd-guide + qa-engineer | 0.75d**
One user journey through the stack: register/login (existing fixtures) -> first answer creates the profile lazily -> streak builds 1->11 with exact XP deltas per Section 4.6 -> level-ups fire `newly_unlocked` at the D-2 boundaries (**476 XP -> L4/8-bit, 3,220 XP -> L8/16-bit, 25,752 XP -> L13/32-bit** — corrected figures, R-4) -> a level-up **without** an unlock returns `newly_unlocked: []` (the common case, e.g. L6) -> incorrect at streak 11 applies the pure penalty (no streak scaling), resets streak, keeps best -> **XP floor at `cum(level)`, never below** (OQ-4): drive a profile to exactly `cum(L)`, answer incorrectly, assert `xp_delta: 0`, level unchanged, streak reset — and assert the level is **never** lower than before any penalty sequence anywhere in the journey (no demotion, LV-08) -> leaderboard ordering reflects the journey.

Plus, explicitly:
- Rate-limit trip at 429 **and the 429 leaves no partial state** (no profile mutation, no streak change — US-32 AC-3).
- Locked-content 403 with the required level in `detail`.
- **Concurrent double-submit serialization** (US-28 AC-1/AC-2) — wired *with the rate limiter active*, which is the combination that exposes R-2 if the transaction boundary was gotten wrong.
- **`GET /api/game/leaderboard` succeeds with no `Authorization` header** (regression pin for the R-3 decision).
- `/api/user/me` and `/api/game/profile` agree after every step of the journey (US-37 AC-1).
- The US-33 golden fixture reproduces byte-for-byte through the endpoint.

AC: suite green locally (Postgres via `docker-compose.test.yaml`) **and** in CI.

#### US-42: Sprint quality gate
**P0 | LOW (coordination) | depends: all | tech-lead orchestrates | 0.25d**
Section 7 checklist executed and signed; security-reviewer APPROVE on US-32/US-33 diff; code-reviewer APPROVE on every PR; README/docs current; no TODO/OQ seam markers left in code (Rev 4: all OQs were resolved before Day 1, so any `# OQ-x` comment found in a PR is a defect — it means code was written from a stale revision).

---

## 6. Orchestration — agent plan and waves

**Rev 3 change (R-8):** the frontend no longer waits for the backend to merge. The US-33 contract is already frozen *in this document* (full JSON example + status codes + golden fixture derivation), so frontend-dev starts **Day 2** against a typed stub/mock. That pulls ~1 day off the critical path, which Rev 2 needed — its schedule had US-38 (1 d) starting Day 3 PM, ending Day 4 PM, with US-39 (0.5 d) spilling to Day 5 while US-34 waited on it.

```
Day 1 AM:   US-25 (registry) ------------------> unblocks 26,29,31
            [Rev 4: ALL OQs are resolved — no Day-1 decision gate. tdd-guide writes the COMPLETE
             golden matrix (incl. the 3-4 tier column) from the final Section 4.6 tables.]
Day 1 PM:   US-26 -> US-27 (ADDITIVE schema chain — v004 creates only, never drops)
              ||  US-29 (pure engine, parallel — only needs US-25)
              ||  frontend: freeze US-33 contract as typed stub + mock handler  <-- NEW (R-8)
Day 2 AM:   US-28 (streak tx, single-commit AC)   ||   US-30 (levels) -> US-31 (unlocks)
              ||  US-38 (frontend submit + HUD, against the mock)             <-- moved up
Day 2 PM:   US-32 (validation/anti-cheat — DECIDE the rate-limit session boundary here, R-2)
              -> US-33 (/answer)   [tdd-guide: integration RED before backend-dev GREEN]
              ||  US-38 continues
Day 3 AM:   US-35, US-36 (public!), US-37 (parallel readers of the schema)
              ||  US-38 (leaderboard columns, AuthContext, scoreCache version, i18n) -> US-39 (gating)
Day 3 PM:   US-40 (docs) ; US-39 continues ; integration of frontend against the REAL /answer
Day 4 AM:   US-41 (e2e suite) ; reviews/security gate
Day 4 PM:   RELEASE TRAIN (must ship together): US-38 frontend switch + US-34 (legacy endpoint
              removal + UserStats deletion + v005 DROP) + US-36/US-37 shape changes
            US-42 gate ; sprint review/retro (scrum-master)
```

**Release-train coupling (new in Rev 3).** Three backend changes are breaking for the client and must ship in the **same** deploy as US-38: US-34 (removes the endpoints the frontend still calls), US-36 (drops `points` from leaderboard entries), US-37 (drops `points` from `/api/user/me`). v005's `DROP TABLE user_stats` rides in that train too — it is only legal once every reader is gone. Nothing else in the sprint is deploy-blocking, because v004 is purely additive and `/answer` co-exists with the legacy endpoints.

**Buffer note:** backend load is ~7.75 person-days against ~8 available (2 backend-dev × 4 days) with a serial chain US-25→26→27→28→33 of ~2.75 d. That is feasible but has **zero slack**; the Day 3 PM slot is the only catch-up buffer. If Day 2 slips, descope **US-39** (P1, gating UI) to Sprint 5 — the server-side gate (US-31/UN-05) still holds, so shipping without client-side gating is safe, merely less polished. Do not descope US-32 or US-41.

TDD discipline (house rule #5): for every backend story, tdd-guide lands the RED tests from Sections 4.6-4.9 and the story AC lists **before** backend-dev's GREEN commit. US-29 is the canonical case: the golden matrix IS the acceptance criterion. Parallelism rule: US-29/US-30/US-31 are pure functions on separate files — safe to run concurrently with the US-26/27/28 schema chain.

Reviewer routing: security-reviewer mandatory on **US-32 and US-33** (the XP-inflation surface is the *write* path — `/answer`). US-36 gets a narrower security pass, scoped to what a **public** read-only leaderboard actually exposes (Rev 3, R-3): username enumeration via the bounded `limit`, absence of PII in the entry shape (US-36 AC-4), and no oracle for probing another user's XP. It is *not* an XP-inflation surface and should not be reviewed as one. code-reviewer on all 18 PRs; pentester (post-merge, HIGH-sprint protocol) targeted at `/api/game/answer` abuse paths — scripted farming under the rate limit, replayed/duplicated submissions against the row lock, and locked-content probing via direct API calls that bypass the client-side gate.

---

## 7. Quality gate / Definition of Done (sprint level)

1. **Every commit in the cascade is green** (Rev 3, R-1) — the suite passes and the app boots at each story boundary, not just at sprint close. This is the gate Rev 2 silently violated between US-26 and US-33.
2. `uv run pytest --cov=src --cov-fail-under=80` green; `xp_engine.py`, `game_config.py` individually >= 95%.
3. Full Section 4.6 matrix green (XP-G01..G22, XP-P01..P08, all 9 property tests); no OQ-1 Option-B seam or rounding branch anywhere in the engine (all OQs resolved pre-sprint, Rev 4).
4. All 16 (mode,width) combos round-trip through `/api/game/answer` in US-41 e2e.
5. LV-06 passes at **n = 84 / 100 / 200** (not 20/50 — see R-5); TP9 proves `int` results for all 80 combinations.
6. security-reviewer APPROVE on US-32/US-33 (incl. the R-2 transaction-boundary resolution); code-reviewer APPROVE on every PR (no CRITICAL/HIGH open).
7. `ruff check` + `ruff format` clean; zero floats in economy modules.
8. **`grep` proofs — run at sprint close, after US-34/v005** (they cannot pass earlier by design, see D-6):
   - `grep -rnE "\buser_stats\b|\bUserStats\b" src/ tests/` → only `v002_game_stats.py` and `v005_drop_user_stats.py`.
   - `grep -rnwE "points" src/` → empty. **Word-boundary (`-w`) required** (Rev 3, L4): a substring grep matches "endpoints"/"breakpoints" and would produce a false red the moment anyone writes a docstring.
   - `grep -rn "game/correct\|game/incorrect" src/ ../hexToBinaryTrainer2Frontend/src/` → empty.
   - `grep -rnE "submitCorrect|submitIncorrect" ../hexToBinaryTrainer2Frontend/src/` → empty (R-7; the URL grep alone misses `UnicodeTrainer.tsx:67,73`).
9. README + architecture doc + backlog + economy tables updated per US-40's concrete checklist.
10. Migrations v004 **and v005** verified idempotent; cascade behavior tested after each.
11. Leaderboard confirmed reachable **without** an `Authorization` header (R-3 regression pin).

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **Rate limiter commits mid-request and voids the answer transaction** (R-2 — this was a *certainty*, not a risk, in Rev 2's design) | ~~High~~ **addressed** | High (double-counted XP, failing concurrency test discovered only at US-41) | Fixed in US-32 (Rev 4): **Option A — separate short-lived limiter session, checked before the answer transaction opens.** US-28 AC-2 + US-32 AC-2/AC-3 test the combination directly. |
| **Repo red between stories** (R-1) | ~~High~~ **addressed** | High (violates house merge rules, blocks every reviewer) | v004 additive, `UserStats` deletion + v005 DROP deferred to US-34. Gate #1 now requires green at *every* commit. |
| **Frontend/backend release skew** — US-34 (endpoint removal), US-36 (leaderboard shape) and US-37 (`/me` shape) are all breaking for the client | Medium | High (leaderboard + sidebar show `PTS 0` or hang) | All three coupled to one release train with US-38 (Section 6). v004's additivity means everything *before* that train is independently deployable. |
| ~~OQ-1 unresolved stalls the golden tests~~ | **eliminated (Rev 4)** | — | RESOLVED pre-sprint: x1.25 (Section 9). The golden tables in 4.6 are final; tdd-guide writes the complete matrix on Day 1 with no held-back column. |
| ~~OQ-7 unresolved stalls US-26/US-32~~ | **eliminated (Rev 4)** | — | RESOLVED pre-sprint: bits in every mode (Section 9). Schema, validator, OpenAPI and the FE mapper are all specified against unicode 8/16/24/32; the frontend's existing `bytes*8` already matches, shrinking US-38's delta. |
| Float creep into level math | Low | High (silent corruption) | D-8 + TP9 + **LV-06 anchored at n = 84**, the first real divergence (R-5 corrected the n≥35 claim, which would have given false confidence); integer-only review rule on `xp_engine.py`. |
| XP farming via scripted `/answer` spam (leaderboard currency) | Medium | Medium | US-32 rate limit + server-side XP authority. Cleanup `DELETE` moved off the request path so the limit is cheap to enforce at 120/min. Challenge-nonce scheme parked in `docs/backlog.md` (US-40). |
| Concurrent submissions corrupt streak/level | Low | High | US-28 AC-1/AC-2 row-lock + single-commit + concurrency test in US-41, run **with the limiter active**. |
| Frontend triplication of width/mode lists re-grows after US-39 | Medium | Low | US-39 extracts one shared constants module and AC-3 asserts it is the only source. |
| Silent field dropping in `AuthContext.applyGameResult` (FE-10) | Medium | Medium (HUD shows stale/zero values with no error) | US-38 AC-4 — a test that fails when a response field is not passed through. |
| Table drift between this doc and code constants | Medium | Low | US-25 requires Section-4 table references in comments; US-40 mirrors tables into `docs/xp-economy.md`; golden tests fail loudly on drift. |
| Backend schedule has zero slack (~7.75 pd vs ~8 available) | Medium | Medium | Day 3 PM is the only buffer; descope order is US-39 first (server-side gate still holds), never US-32/US-41. See Section 6. |

---

## 9. Open Questions — ALL RESOLVED (decision record)

Every question below was decided by the PO on 2026-10-27 (Rev 4). **Nothing is open.** The record is kept so no decision is re-litigated mid-sprint; the question text is the historical Rev 2/3 wording.

| ID | Question (historical) | PO decision | Applied in |
|----|----------------------|-------------|-----------|
| OQ-1 | Streak tier 3-4 is x1.2, which breaks the "exact multiples of 4, no rounding" invariant for all 16 combos (16x1.2=19.2). Accept x1.25 (Option A) or keep x1.2 with round-half-up-to-4 (Option B)? | **Option A: x1.25** = rational pair (5,4). Proof in D-3/4.5: core values are div-16, so x5/4 lands on multiples of 4 for all 80 combinations; under x6/5 all 16 are non-integral. Balance impact +4% on one tier. No rounding path exists anywhere in the engine. | D-3, 4.4–4.6 (final tables), US-25, US-29 |
| OQ-2 | Heading says mode multipliers "NOT applied to penalty", but the formula `base_xp * mode_multiplier * 0.5` includes them. Which is canonical? | **The formula is canonical** — penalties DO scale with the mode multiplier. Consequence pinned by test: uniform **2:1** gain:penalty ratio in every mode/width (XP-P08). | D-5, 4.7, US-29 |
| OQ-3 | Does `xp_required_for_level(n)` mean the span n→n+1 (start L1/0 XP) or a cumulative attainment threshold? | **Span semantics, start at L1 with 0 XP.** | D-1, 4.8, US-30 |
| OQ-4 | XP floor `total_xp >= 0` with level demotion, or sticky max level? | **Neither as proposed — PO specified a third option:** "kein Level-Verlust soll möglich sein." XP floor is **`cum(level)`**: `total_xp_new = max(cum(level), total_xp - penalty)`. Demotion is impossible *by construction* while `level` stays a pure function of `total_xp` — no `max_level_reached` column, no sticky state, unlocks never revoked (UN-07). At L1 the floor is `cum(1) = 0`, preserving legacy behaviour for new players. Simpler than both original options. | D-1, D-5, 4.1, LV-08/09/10, US-26/30/33/41 |
| OQ-5 | Does the multiplier use the streak including the current correct answer? | **Yes — post-increment.** The 3rd consecutive correct answer earns the 3–4 tier. | D-3, 4.5, US-28/29 |
| OQ-6 | Confirm or adjust the unlock ladder (caps L13/L4 binding; intermediate steps were a proposal). | **Ladder confirmed: 1 / 4 / 8 / 13** for conversion slots 1–4; **L1–L4** for unicode slots. | D-2, 4.9, US-31 |
| OQ-7 | `bit_width` means bits for conversion modes but bytes for unicode — (a) document per-mode unit, (b) rename + `unit` field, (c) split fields, (d) normalize unicode to bits on the wire? | **Option (d): bits in every mode.** Unicode valid widths = **8/16/24/32**. Removes the valid-but-wrong bug class (`unicode + bit_width: 4` is now a loud 422). The frontend's existing `bytes * 8` is already correct — **zero client-side arithmetic change** (reverses Rev 3's R-7 prescription in US-38). Cost accepted: unicode slot labels lose the 1/2/3/4 symmetry, and 24 is not a power of two. | D-2, D-7, 4.2/4.4/4.7, UN-02/06, US-25/26/32/38/40 |
| OQ-8 | Response field `xp_required_for_level` holds the span to the *next* level but reads as a cumulative threshold. Rename? | **Rename to `xp_required_for_next_level`** on the wire (US-33 + US-35 identically, US-38 HUD). The internal engine function keeps its D-1 name `xp_required_for_level(n)`. Zero cost before release, breaking after. | US-33, US-35, US-38 |
| OQ-9 | Should `/api/game/leaderboard` require auth for parity with the other game endpoints? (Rev 2 asserted "Auth required" inside US-36.) | **NO — the leaderboard stays public** (decided already in Rev 3; R-3). Entries expose only `username` + game numbers, no PII. `README.md:689` documents it as public and the client calls it unauthenticated. Pinned by regression test (US-36 AC-3, gate #11). Abuse control is the `limit` bound, not auth. | US-36, US-38, US-40, gate #11 |

---

## 10. Sources

- PO clarifications (2026-10-27) — corrections 1-5 driving Rev 2
- PO decision (2026-10-27, Rev 3) — leaderboard stays public (OQ-9, Section 9)
- **PO decisions (2026-10-27, Rev 4) — all eight open questions OQ-1..OQ-8 closed** (decision record in Section 9, changelog in Section 0.3)
- Rev 1–Rev 3 Sprint 4 plans (superseded; kept for decision-ID continuity D-1..D-8 and OQ-1..OQ-9)
- Code facts: Section 2.1 / 2.2 tables (paths + line numbers, **re-verified against both repos 2026-10-27** for Rev 3)
- Math verification (Rev 3): all tables in Section 4 and D-1 independently recomputed with exact rational arithmetic (`fractions.Fraction`) and **confirmed correct**; the float-divergence threshold was measured empirically (first divergence n = 84, not n >= 35) — reproducible via XP-TP1..TP9 / LV-01..LV-10
- Frontend audit (Rev 3): read-only survey of `hexToBinaryTrainer2Frontend` integration surface — evidence IDs FE-1..FE-14 in Section 2.2
- House rules: `~/.claude/rules/common/{git-workflow,testing,development-workflow}.md`; prior plans `docs/sprint2-plan.md`, `docs/sprint3-plan.md`
