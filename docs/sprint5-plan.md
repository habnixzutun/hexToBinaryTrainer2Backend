# Sprint 5 Plan (Rev 1) — Visuelle Nacharbeit & Onboarding-Fix aus Sprint 4

> **Status:** ENTWURF — alle vier Stories sind spezifiziert, keine Open Questions.
> **Sprint-Ziel:** Der Sprint-4-Schmerz wird sichtbar behoben: ein Spieler ab Level 1 startet auf einem **freigeschalteten** Tier (keine 403-Falle bei der ersten Antwort), das Leaderboard zeigt nur noch **Name, Stufe, XP** und der eigene Eintrag aktualisiert sich **ohne Request**, und die obere HUD-Leiste weicht — ohne dabei den Level-Up-Toast zu verlieren.
> **Charakter:** Stabilisierungs-Sprint. Kein neues Feature, keine Migration, keine Economy-Änderung. Alle vier Stories zahlen Tech-Debt aus Sprint 4 zurück (Sprint-Gate Regel 4 ist damit strukturell erfüllt).
> **Repo-Scope:** US-43 und US-44 sind **cross-repo** (Backend `hexToBinaryTrainer2Backend` + Frontend `hexToBinaryTrainer2Frontend`); US-45 und US-46 sind rein frontend.

---

## 0. Sprint-Gate-Vorprüfung (PO, 2026-11-01)

| # | Gate-Kriterium | Ergebnis | Konsequenz |
|---|---|---|---|
| 1 | Alle AC aus Sprint 4 erfüllt? | ✅ Sprint 4 komplett gemerged (PR #46, inkl. Security-Fixes). Offene Punkte sind bewusst ins Backlog verschoben: **US-34-Nachzug**, **TD-01**, **TD-02**, **TD-03** — keine davon blockiert Sprint 5. | — |
| 2 | Alle Tests grün? | ⚠️ **Teilprüfung:** Backend-Collection fehlerfrei (2000 Tests, kein Import-Bruch). Der **vollständige Suite-Lauf** (pytest 80 %-Gate, ruff, Vitest, tsc) wurde vom PO **nicht** ausgeführt und ist **Bedingung vor Day 1** — QA/tech-lead führen ihn aus. | Gate-Lauf ist Sprint-Aufgabe US-47 #1. |
| 3 | Review/Retro gehalten? | ❌ **Nein.** Es gibt kein `docs/sprint4-review.md`. | **Sprint-4-Retro ist vor Sprint-5-Start nachzuholen** (scrum-master). Formale Gate-Verletzung, wenn Sprint 5 ohne startet. |
| 4 | Tech Debt getrackt? | ✅ Die vier visuellen Bugs sind genau der Debt-Ausgleich; zusätzlich werden **TD-04** (Gast-Refetch) und **TD-05** (tote i18n-Keys nach US-43) filetiert. Siehe `docs/backlog.md`. | — |
| 5 | Velocity / Capacity | Sprint 4 wurde in Person-Tagen geschätzt (≈ 9,25 pd, 18 Stories, alle geliefert). Ein Story-Point-Maß existiert nicht und wird nicht eingeführt (kein Bedarf, zwei Sprints Historie). | Sprint 5 Umfang: **≈ 2,0 pd Dev + 0,25 pd Gate** — bewusst leichter Stabilisierungssprint. |

---

## 1. Scope

**In scope:**
- US-43: Leaderboard-Response und -UI auf `(name, level, xp)` reduzieren (cross-repo, ein Release-Zug)
- US-44: Eigenen Leaderboard-Eintrag lokal updaten, ohne Backend-Request pro Antwort
- US-45: `GameHud` entfernen, `LevelUpToast` nach `App.tsx` umsiedeln, toten Context-State aufräumen
- US-46: Default-Tier beim Login = erster freigeschalteter Tier (4 Bit), keine 403-Falle für Level-1-Spieler
- US-47: Sprint-Qualitätsgate (Suite-Lauf, Retro-Nachholung, Doku)

**Out of scope (YAGNI / kein Gold-Plating):**
- Persistierte Tier-Präferenz pro Nutzer (ein „merke meine letzte Auswahl" ist nicht gefordert — Default ist bewusst immer der niedrigste freigeschaltete Tier)
- WebSocket/Server-Push für Leaderboard-Live-Updates (Projekt bleibt request/response, siehe Backlog-Fußnote zu Sprint 1)
- Neue Statistik-Anzeige für Streak/XP-Fortschritt als HUD-Ersatz (der Wunsch ist explizit *weniger* Anzeige; Streak bleibt nur noch auf dem Server)
- Verschlankung von `/api/user/me` oder `ProfileResponse` (die Sidebar-Statistik OK/ERR/XP bleibt ausdrücklich erhalten — Anforderung 3 sagt sogar, sie sei der Grund für die HUD-Entfernung)
- Leaderboard-Auth, `limit`-Änderungen, Index-Arbeiten (bleiben bei US-34-Nachzug)

---

## 2. Befund — verifizierter Ist-Zustand (2026-11-01)

`BE-` = Backend-Repo, `FE-` = `hexToBinaryTrainer2Frontend`.

| Fakt | Beleg |
|---|---|
| `LeaderboardEntryV2` sendet 6 Felder: `user_id, username, total_xp, level, correct_count, incorrect_count` | BE-1 `src/models/game_schemas.py:270-288` |
| Sortierung `total_xp DESC, incorrect_count ASC, user_id ASC` passiert **serverseitig im SQL** — die Felder sind nicht für die Sortierung im Client nötig | BE-2 `src/services/leaderboard_service.py:97-105` |
| Leaderboard-Tests asserten die Entry-Form als **exaktes Dict** (inkl. `correct_count`/`incorrect_count`) | BE-3 `tests/test_game.py:462-483` (`test_leaderboard_entry_shape_has_no_points_and_no_pii`), `tests/test_game.py:704+`, E2E-Suite |
| FE-Typ `LeaderboardEntry` dupliziert die sechs Felder; ✓/✗-Spalten werden gerendert | FE-1 `src/services/api.ts:27-33`; `src/components/Leaderboard.tsx:114-119` (Header), `:184-191` (Zellen) |
| Client sortiert selbst nach `total_xp` (stabil) und promotet Haus-Legende `'Max'` auf Platz 1 | FE-2 `src/components/Leaderboard.tsx:31-48` |
| **Jede Antwort löst ein volles Leaderboard-Refetch aus:** `handleGameResult` bumppt `refreshKey` → `refreshTrigger`-Effekt ruft `refreshData()` | FE-3 `src/App.tsx:58-61`; `src/components/Leaderboard.tsx:75-79` |
| 30-s-Poll existiert unabhängig davon | FE-4 `src/components/Leaderboard.tsx:13,81-84` |
| `GameHud` ist in `App.tsx` mountet — und **mounted darin den `LevelUpToast`** | FE-5 `src/App.tsx:11,117`; `src/components/GameHud.tsx:126` |
| `LevelUpToast` ist `fixed bottom-4 right-4` → positionsunabhängig, 1:1 umhängbar | FE-6 `src/components/LevelUpToast.tsx:48` |
| `lastAnswer` im AuthContext hat **nur** einen Consumer: `GameHud` | FE-7 `src/contexts/AuthContext.tsx:66-67,118,152-155,292-306` |
| HUD-i18n-Keys (`hudLabel`, `xpProgress`, `streak`, `bestStreak`) werden nur von GameHud + Tests benutzt | FE-8 `src/i18n/translations.ts:108-113,287-292,466-471`; `test/i18n/translations.test.ts:39-40,48-49` |
| Default-Bits sind hart `8`: App-State UND Trainer-Fallback | FE-9 `src/App.tsx:36`; `src/components/HexBinTrainer.tsx:73` |
| `tierGates`/`isUnlocked` (Unlock-Seed 1/4/8/13) existieren bereits als Single Source im Client | FE-10 `src/constants/gameModes.ts:82-143` |
| **Der Default-Bug ist funktional, nicht optisch:** locked Tier vorausgewählt → `handleCheck` sendet `bit_width=8` → Unlock-Gate wirft **403** | FE-11 `src/components/HexBinTrainer.tsx:138,123-146`; BE-4 `src/routers/game.py:164-208` |
| Unicode-Default (`bytes=1` → 8 Bit = Slot 1, Level 1) ist bereits korrekt — **nicht** betroffen | FE-12 `src/App.tsx:37`; Seed `unicode 8 → min_level 1` in `gameModes.ts:86` |
| Vitest-Schwellen 80 % erzwungen; betroffene Suites existieren | FE-13 `vite.config.ts:19-35`; `test/components/{GameHud,Leaderboard,HexBinTrainer}.test.tsx`, `test/App.test.tsx`, `test/contexts/AuthContext.test.tsx`, `test/i18n/translations.test.ts`, `test/helpers/auth.ts` |

---

## 3. User Stories

Reihenfolge der IDs folgt den Anforderungen; die **Priorität** (Business-Value) steht unter each Story separat.

---

### US-43: Leaderboard reduziert sich auf (Name, Stufe, XP)

**Priorität:** P1 · **Komplexität:** MEDIUM (cross-repo, Breaking Change im Payload) · **Dependencies:** keine · **Welle 1** · **Aufwand:** 0,5 d · **Shares:** `Leaderboard.tsx` mit US-44 → US-44 **nach** US-43 merge­ sequenzieren

> **Als** Spieler **möchte ich** im Leaderboard nur noch Name, Stufe und XP sehen, **damit** die Rangliste ruhig bleibt und nicht mit Statistiken zuballert wird, die mich hinter meinem Platz nicht interessieren.

**Akzeptanzkriterien:**
1. `LeaderboardEntryV2` (`src/models/game_schemas.py:270`) enthält nur noch `user_id`, `username`, `total_xp`, `level`. `correct_count` und `incorrect_count` werden **ersatzlos** aus dem Response-Schema entfernt — **kein** Deprecated-Alias, **kein** optionales Feld (Rev-3-Präzedenzfall: Aliasse müssen später wieder entfernt werden).
2. Die SQL-Sortierung bleibt unverändert: `total_xp DESC, incorrect_count ASC, user_id ASC` (`leaderboard_service.py:97-105`). Das Tiebreak-Feld wird weiterhin aus der DB gelesen, nur **nicht serialisiert**. AC: ein Test mit zwei gleich starken XP-Werten und unterschiedlichen `incorrect_count`-Werten pinnt die Reihenfolge.
3. FE-Typ `LeaderboardEntry` (`src/services/api.ts:27`) verliert die zwei Felder; `Leaderboard.tsx` entfernt ✓/✗-Spalten (Header `:114-119`, Zellen `:184-191) inkl. ihrer aria-labels. Übrig bleiben: Rang/Medaille, Name (+ „ich"-Badge, + 'Max'-Tooltip), Stufe, XP.
4. **Der 'Max'-Easter-Egg und die Client-Sortierung bleiben unverändert** (`Leaderboard.tsx:31-48`). Bei XP-Gleichstand gilt weiterhin die Server-Reihenfolge (Array-`sort` ist stabil, die Basis kommt sortiert vom Server) — ein Test pinnt das.
5. Bestehende Backend-Tests werden auf die neue Form gebracht: `tests/test_game.py:462-483` (exaktes Dict-Assertion), `tests/test_game.py:704+` (Divergent-Level-Test), E2E-Suite, `tests/test_game_profile_models.py` (Contract-Tests gegen `app.openapi()`). Das Response-Wrapper-Feld `{entries: […]}` bleibt.
6. FE-Testfixtures (`test/components/Leaderboard.test.tsx:39-43`) verlieren die zwei Felder; die Suite prüft, dass ✓/✗ **nicht** mehr im DOM auftaucht.
7. Grep-Beleg im PR: `correct_count|incorrect_count` kommt in `src/components/Leaderboard.tsx` und `src/services/api.ts` nicht mehr vor; in `src/routers/`/`src/services/` nur noch, wo es für Sort/Profil legitim ist (Profile-Endpoints behalten die Felder — Out-of-Scope-Grenze).
8. README (Backend: Leaderboard-Shape-Doku, `:689`-Bereich; Frontend: Falls vorhanden) und `docs/xp-economy.md` (§ Leaderboard-Sektion) werden auf die reduzierte Form aktualisiert.

**Release-Zug:** BE-Änderung und FE-Änderung **müssen zusammen** deployen (alt-FE + neu-BE → `undefined`-Zellen; das ist der R-3-Präzedenzfall aus Sprint 4, hier bewusst antizipiert). Kein Migrationsbedarf.

**Definition of Done:** tdd-guide schreibt die neuen Form-Tests (BE: exakte Entry-Keys, Sort-Tiebreak; FE: keine ✓/✗-Zellen) **vor** der Implementierung · `pytest --cov=src --cov-fail-under=80` grün · Vitest + `tsc --noEmit` grün, 80 %-Schwellen halten · code-reviewer APPROVE · README/`xp-economy.md` aktualisiert · Branch `feature/us-43-leaderboard-reduce` (Cascade).

---

### US-44: Eigener Leaderboard-Eintrag aktualisiert sich ohne Backend-Request

**Priorität:** P2 · **Komplexität:** MEDIUM (State-Merge + Race-Disziplin) · **Dependencies:** US-43 (gleiche Datei; reduzierte Form macht den Merge trivial) · **Welle 2** · **Aufwand:** 0,75 d

> **Als** Spieler **möchte ich**, dass mein eigener Eintrag im Leaderboard sofort springt, wenn ich richtig antworte, **damit** ich ohne Lade-Ruckeln sehe, was meine Antwort wert war.

**Akzeptanzkriterien:**
1. Nach einer beantworteten Antwort ( korrekte **und** falsche Fälle) zeigt der eigene Eintrag (`user_id === user.id`) die **neuen** Werte `total_xp`/`level` aus dem `/answer`-Response — **ohne** dass ein `GET /api/game/leaderboard` abgeht. AC im Test: Spy auf `gameApi.getLeaderboard` — Aufrufzähler bleibt nach `applyGameResult` unverändert.
2. `handleGameResult` (`src/App.tsx:58-61`) ruft **kein** `setRefreshKey` mehr auf. Der 30-s-Poll (`Leaderboard.tsx:81-84`) bleibt die einzige Quelle für fremde Einträge. Network-Beweillace im PR: eine Antwort erzeugt genau **einen** Request (`POST /api/game/answer`) — weder Leaderboard noch `/api/user/me`.
3. **Merge-Regel (Race-Schutz):** Der Overlay passiert **im Render**, nicht als State-Write: `entries.map(e → e.user_id === user?.id ? {…e, total_xp: user.total_xp, level: user.level} → e)`, daraus folgt `rankEntries`. Damit kann ein **in-flight** alter Poll (Start vor der Antwort, Eintrag nach ihr) die frischen lokalen Zahlen nicht überdecken — der Kontext ist immer aktuell. Ein Test simuliert genau diesen Fall (verzögerter Poll resolve nach Antwort) und pinnt: eigene Reihe behält die neuen Werte.
4. Pipeline-Reihenfolge ist normativ: **Fetch → Overlay (eigene Reihe) → rankEntries (Sort + 'Max'-Promotion)**. Der Easter-Egg funktioniert mit Overlay unverändert weiter.
5. **Fehlende eigene Reihe wird nicht erfunden:** Ist der eigene `user_id` nicht unter den gelieferten Top-N, wird **keine** Zeile lokal angehängt (die Server-Rangliste ist die Wahrheit; der nächste Poll bringt den Spieler rein, wenn er qualifiziert ist). Als akzeptierte Grenze dokumentiert.
6. Der Poll ersetzt weiterhin die Basisdaten; der Overlay legt sich bei jedem Render erneut darüber → nach einem Penalty-Fall (XP sinkt) zeigt der nächste Poll den korrigierten Serverstand.
7. Gäste (`user === null`) bleiben unberührt: Overlay ist No-op, bestehendes Verhalten unverändert (das Gast-`updateUserData`-Bumping ist bewusst **nicht** Teil dieser Story → **TD-04**).
8. Tests: `Leaderboard.tsx`-Suite deckt ab — Sofort-Update nach Mock-`/answer`, kein Refetch, In-Flight-Poll verliert nicht, XP-Gleichstand-Order, Gästefall. `App.test.tsx` pinnt den unterbliebenen Refetch.

**Definition of Done:** RED-Tests zuerst (tdd-guide) · alle vier Cases grün · `tsc --noEmit` · Coverage-Schwellen halten · code-reviewer APPROVE (Fokus: keine State-Mutation, Immutability-Regel) · `docs/BugTracker.md` erhält einen Vermerk, dass der „überflüssige `/api/user/me`"-Bug (Behandlung in `:141`/`:146` bereits gefixt) und dieser Refetch erledigt sind · Branch `feature/us-44-leaderboard-live`.

---

### US-45: GameHud entfernen — ohne den Level-Up-Toast zu verlieren

**Priorität:** P1 · **Komplexität:** MEDIUM (Toast-Umsiedlung + toter Context/I18n) · **Dependencies:** keine (teilt `App.tsx` mit US-46 → kaskadierende Branches) · **Welle 2** · **Aufwand:** 0,5 d

> **Als** Spieler **möchte ich**, dass die Info-Leiste ganz oben verschwindet, **damit** mein Trainingsbereich aufgeräumt ist — meine Zahlen stehen bereits unten links in der Sidebar.

**Akzeptanzkriterien:**
1. `src/App.tsx:11` (Import) und `:117` (`<GameHud />`) entfernt; Komponente `src/components/GameHud.tsx` und ihr Test `test/components/GameHud.test.tsx` gelöscht. Der Layout-Container (`:116-118`) behält seine Flex-Struktur (Trainer + rechte Spalte unverändert).
2. **`LevelUpToast` überlebt:** Er wird direkt in `App.tsx` (AuthenticatedApp) gemountet, gespeist aus `useAuth()` (`user.level`, `pendingUnlocks`, `dismissLevelUp`). Da er `fixed bottom-4 right-4` ist (FE-6), ist die Umsiedlung positionsneutral. AC: Bestehende `test/components/LevelUpToast.test.tsx` bleibt grün; **ein neuer Fall** im App-Kontext pinnt: Level-Up mit nicht-leerem `newly_unlocked` zeigt den Toast, leerer Fall zeigt keinen (No-op-Regel aus US-33/38).
3. `lastAnswer` wird **toten-State-bereinigt**: Feld aus `AuthContextType` (`:66-67`), State (`:118`), `clearAnswerState` (`:152-155`) und `applyGameResult` (`:292-306`) entfernt; `pendingUnlocks`/`dismissLevelUp` **bleiben** (Toast-Consumer). `economyOf` bleibt unverändert. Test-Bausteine (`test/helpers/auth.ts`, `test/contexts/AuthContext.test.tsx`) angepasst; kein TS-Unused-Fehler.
4. Tote i18n-Keys in **beiden** Locales löschen: `hudLabel`, `xpProgress`, `streak`, `bestStreak` (`translations.ts:108-113` DE, `:287-292` EN, Interface `:466-471`) **und** die zugehörigen Assertions in `test/i18n/translations.test.ts:39-40,48-49`. **Unangetastet** (andere Consumer): `xp`, `levelLabel`, `levelUpToast`, `modeNames`, `bits`, `dismissNotice`, `lockedUntilLevel`, `rateLimited`, `correct`, `wrong`, `correctAnswer` (Trainer-Feedback!).
5. Sidebar bleibt wie sie ist (OK/ERR/XP, `Sidebar.tsx:246-268`) — sie ist laut Anforderung der Grund der Entfernung. **Kein** Ersatz für Streak-/XP-Fortschrittsanzeige (bewusster Trade-off: Streak lebt nur noch auf dem Server; falls Demand entsteht, separates Backlog-Item, nicht hier).
6. Grep-Belege im PR: `GameHud` kommt in `src/` und `test/` nicht mehr vor; `lastAnswer` nicht mehr in `src/contexts/`; `hudLabel|xpProgress` nicht mehr in `src/`.

**Definition of Done:** Toast-Regressionstest existiert **vor** dem Löschen des HUD-Tests (RED) · Vitest + `tsc --noEmit` grün · Schwellen halten · code-reviewer APPROVE · Changelog-Eintrag im README (HUD entfernt, Toast umgesiedelt) · Branch `feature/us-45-remove-gamehud`.

---

### US-46: Default-Tier beim Login = erster freigeschalteter Tier (4 Bit)

**Priorität:** **P0** · **Komplexität:** LOW · **Dependencies:** keine (teilt `App.tsx` mit US-45 → kaskadieren) · **Welle 1** · **Aufwand:** 0,25 d

> **Als** neuer Spieler (Level 1) **möchte ich**, dass der Trainer mich auf einem freigeschalteten Tier starten lässt, **damit** meine allererste Antwort nicht mit einer Fehlermeldung („bis Level 4 gesperrt") abstürzt.

**Begründung der P0:** Das ist kein Optik-Bug. Heute ist `bits=8` vorausgewählt (`App.tsx:36`) → `handleCheck` sendet `bit_width=8` (`HexBinTrainer.tsx:138`) → das Unlock-Gate (`src/routers/game.py:164-208`, UN-05) antwortet **403**. Jeder Level-1-Spieler scheitert an seiner **ersten** Antwort. Das ist die schlechteste erdenkliche Onboarding-Erfahrung und der einzige Funke-Schaden dieses Sprints.

**Akzeptanzkriterien:**
1. Neue reine Hilfsfunktion in `src/constants/gameModes.ts`: `defaultTierFor(mode: GameMode, playerLevel: number | null): number` → liefert den **niedrigsten** Tier, der für das Level freigeschaltet ist (`tierGates(…).find(g → g.isUnlocked).bitWidth`); bei `playerLevel === null` (Gast/kein Profil) gilt die bestehende „alles freigeschaltet"-Semantik → ebenfalls Slot 1. Die Funktion ist total: für jeden Modus und jedes Level 1-13+ existiert immer ein Ergebnis (Slot 1 ist ab Level 1/1 freigeschaltet).
2. `src/App.tsx:36`: `useState(8)` → `useState(defaultTierFor('hex_to_bin', user?.level ?? null))`. `src/components/HexBinTrainer.tsx:73` (interne Fallback-Komponente) erhält denselben Default (auf `playerLevel ?? null` basierend), damit Einzelkomponenten-Tests konsistent sind.
3. **Matrix-Test (parametriert):** Für Levels 1, 3 → Default `4`; Level 4, 7 → Default `4` (bewusst: kein Hochspringen auf 8, KISS, keine persistierte Präferenz); Level 8, 12, 13+ → Default `4`. Immer gilt: **der vorausgewählte Tier ist nie gesperrt** (`isUnlocked(level, mode, default) === true` für alle Kombinationen). Unicode: `bytes=1` bleibt unverändert (Slot 1, min_level 1 — FE-12), durch einen Fall mitgepinnt, dass `defaultTierFor('unicode', 1) === 8` und die Byte-Anzeige `1` bleibt.
4. Kein Zwangswechsel während einer Session: ein Level-Up (L3 → L4 durch Antwort) ändert die **aktuelle** Auswahl **nicht**; der Spieler entscheidet selbst, wann er hochwechselt (AC: Test — Antwort hoch auf Level 4, `bits` bleibt beim bisherigen Wert).
5. Mobile-Schiene ohne Sonderfall: `Sidebar.tsx:221-229` (`BitByteSelector value={bits}`) erbt den Fix automatisch; ein Test für den mobilen Pfad (Bits=4 ausgewählt bei L1) pinnt das.
6. Bestehende Tests, die den 8-Bit-Default festzurren, werden auf den neuen Default gezogen: `test/components/HexBinTrainer.test.tsx:99` („8 bits selected by default"), `test/App.test.tsx:89` (8-Bit-Frage per Math.random-Mock → 4-Bit-Frage). Kein Test wird übersprungen oder gelöscht, nur korrigiert.
7. Grep-Beleg: `useState(8)` existiert in `src/` nicht mehr (Ausnahme: `Math.pow`-Kram ist irrelevant).

**Definition of Done:** Matrix-RED-Tests zuerst · Vitest + `tsc --noEmit` grün · Schwellen halten · code-reviewer APPROVE · Hand-check im PR-Text beschrieben (L1-Login → 4-Bit-Frage → Antwort → 200, kein 403) · Branch `feature/us-46-default-tier-unlocked`.

---

### US-47: Sprint-Qualitätsgate & Nacharbeit

**Priorität:** P0 (Koordination) · **Komplexität:** LOW · **Dependencies:** alle · **Welle 3** · **Aufwand:** 0,25 d

1. **Gate-Lauf nachholen (aus Gate-Tabelle #2):** `uv run pytest --cov=src --cov-fail-under=80` + `ruff check src/ tests/` (BE); `vitest run --coverage` + `tsc --noEmit` (FE). Beide Repos grün = Sprint-Abnahme.
2. **Sprint-4-Retro nachholen** (scrum-master) und Ergebnis als `docs/sprint4-review.md` festhalten — ohne dieses Dokument gilt Gate #3 als verletzt (Rule: kein Sprint startet ohne gehaltene Retro).
3. `docs/BugTracker.md`: offenen Eintrag („überflüssiger `/api/user/me`-Request") auf **erledigt** stellen — der Fix (`onGameResult !== undefined`-Guard, FE `HexBinTrainer.tsx:141`/`UnicodeTrainer.tsx:146`) ist längst im Code, der Tracker lügt.
4. Backlog-Pflege: US-34-Nachzug bleibt P1-open; **TD-04** und **TD-05** sind von diesem Sprint bereits filed (siehe `docs/backlog.md`).

---

## 4. Priorisierung nach Business-Value

| Rang | Story | Warum diese Reihenfolge |
|---|---|---|
| 1 | **US-46** (P0) | Einziger **funktionaler** Schaden: erste Antwort jedes neuen Spielers endet mit 403. Kleinster Aufwand (0,25 d), größter Hebel auf Onboarding. |
| 2 | **US-45** (P1) | Expliziter Produktwunsch („Leiste soll weg"). Risiko-arm, weil der einzige Fallstrick (Toast) spezifiziert ist. Macht die UI sofort ruhiger. |
| 3 | **US-43** (P1) | Produktwunsch, kleine mechanical Cross-Repo-Änderung. Muss vor US-44 (gleiche Datei, reduzierte Form vereinfacht Overlay). |
| 4 | **US-44** (P2) | Fühlt sich gut an, ist aber Verfeinerung: das Feature „aktualisiert sich" existiert heute schon (nur per Request). Höchste technische Sorgfalt (Race-Regel), darum zuletzt und mit klarem Fundament aus US-43. |

**Wellenplan (Branch-Kaskade nach Hausregel — pro US ein Branch, Folge-US zweigt vom gemergten Vorgänger ab):**

```
Welle 1 (parallel):   US-46 (App.tsx:36, gameModes.ts)   ·   US-43 (BE schemas/service + FE Leaderboard/api)
Welle 2 (parallel):   US-45 (App.tsx + AuthContext + i18n, nach US-46 wegen App.tsx)   ·   US-44 (Leaderboard.tsx, nach US-43)
Welle 3:              US-47 (Gate-Lauf, Retro, Doku)
```

Gesamtaufwand ≈ **2,0 pd Dev + 0,25 pd Gate**. Zwei Dev-Instanzen (1 backend-dev für US-43 BE-Hälfte, 1 frontend-dev für FE-Hälften + US-45/46) ⇒ kalendermäßig **1–2 Tage**. Der Sprint ist bewusst dünn — er konsolidiert, er baut nicht.

---

## 5. Technische Risiken

| Risiko | Entry | Impact | Mitigation |
|---|---|---|---|
| **Toast-Verlust beim HUD-Löschen** — blinde Entfernung von `GameHud.tsx` reißt `LevelUpToast` (`GameHud.tsx:126`) mit; Unlock-Feedback wäre spurlos weg | Mittel (vergessen) | **Hoch** (stummer Feature-Verlust, genau das, was Sprint 4 aufgebaut hat) | US-45 AC 2 erzwingt Umsiedlung + App-Kontext-Test **bevor** der HUD-Test gelöscht wird (RED-Regel) |
| **Breaking Change im Leaderboard-Payload** (US-43): alt-FEE + neu-BE rendern `undefined`-Zellen | Sicher (Release-Schieflage) | Mittel | Release-Zug-Regel in US-43 (BE+FE gemeinsam deployen); kein Alias-Feld (Rev-3-Präzedenz); Grep-Belege |
| **Race zwischen Overlay und in-flight Poll** (US-44): verspäteter Poll könnte lokale Zahlen überdecken | Mittel | Mittel (flackernde eigene Reihe) | AC 3 macht den **Render-Overlay** (keine State-Mutation) zur Norm — der Context ist immer frisch; Test simuliert den verzögerten Poll |
| **Dead-Code-Stillstand** nach US-45: `lastAnswer` + 4 i18n-Keys leben tot im Code weiter | Mittel | Niedrig (Wartungsgeruch) | AC 3+4 erzwingen ersatzlose Bereinigung inkl. Interface + Translations-Test; Grep-Belege |
| **Level-Up-Auswahl-Sprung** (US-46-Überimplementierung): automatische Hochstufung bei Level-Up nervt oder kollidiert mit Nutzerwahl | Niedrig | Niedrig | AC 4 verbietet Zwangswechsel explizit; Default ist bewusst *immer* Slot 1 (keine persistierte Präferenz, YAGNI) |
| **Bestehende Tests fälschlich gelöscht statt korrigiert** (8-Bit-Default, GameHud-Suite, Translations-Assertions) | Mittel | Mittel (Coverage-Falle, 80 %-Schwelle) | DoD jeder Story: „angepasst, nicht übersprungen"; Matrix-Tests ersetzen Einzelfälle |
| **Gastpfad wird unabsichtlich mitangepasst** (TD-04 versucht, das im Sprint zu reparieren) | Niedrig | Niedrig | US-44 AC 7 grenzt explizit aus; TD-04 im Backlog |
| Sprint 5 startet ohne gehaltene Sprint-4-Retro (Gate #3) | **Hoch** (aktuell Fakt) | Formals | US-47 AC 2: Retro **vor** Day 1 nachholen und dokumentieren |

---

## 6. Qualitätssicherung / Abnahmekriterien auf Sprint-Ebene

1. US-46 Handnachweis im PR: frischer L1-Account → Trainer startet mit 4 Bit → erste Antwort → `200`, kein `403` (BE-Log/Network-Tab als Screenshot im PR-Text).
2. Beide Repos: Suite grün + Coverage-Gates (BE 80 %, FE 80 % Schwellen in `vite.config.ts`) — **kein** Test wurde übersprungen oder aus Bequemlichkeit gelöscht.
3. Grep-Belege aus den Story-ACs sind im jeweiligen PR-Text ausgeführt und leer (`GameHud`, `lastAnswer`, `useState(8)`, `correct_count|incorrect_count` in Leaderboard-Dateien).
4. `ruff check` + `tsc --noEmit` fehlerfrei.
5. Leaderboard verhält sich im Browser sichtbar korrekt: keine ✓/✗-Spalten, eigene Reihe springt sofort nach Antwort, kein extra Request im Network-Tab pro Antwort, Poll bleibt 30 s.
6. code-reviewer APPROVE auf jedem PR (Hausregel #3), security-reviewer nur dort, wo ein Diff es verlangt (US-43 berührt ein öffentliches Schema → schmaler Pass wie in Sprint 4 US-36: keine PII im Entry, `limit`-Bounds unverändert).
7. `docs/sprint4-review.md` existiert (Retro nachgeholt), `docs/BugTracker.md`-Eintrag erledigt gestellt.

---

## 7. Quellen

- Anforderungen (visuelle Bugs) aus Sprint-5-Planning, 2026-11-01
- Code-Audit beider Repos am 2026-11-01 (Befund-Tabelle Section 2, alle Zeilenangaben verifiziert)
- `docs/sprint4-plan.md` (Rev 4) — ÖQ-Entscheidungen (bits in jedem Modus), R-3-Präzedenzfall Release-Zug, UN-05 403-Mechanik
- `docs/backlog.md` — TD-01/TD-02/TD-03 bleiben offen; US-34-Nachzug unberührt
- Hausregeln: `~/.claude/rules/common/{git-workflow,testing,development-workflow}.md`
