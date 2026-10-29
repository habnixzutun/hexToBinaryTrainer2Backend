# Architektur-Dokumentation

> Stand: 2026-10-29 · Sprint-1-Community-Sicht (US-08), ergänzt um die Sprint-2/3/4-Communities
> — neu in Sprint 4: **Game Economy (Community 6)** inkl. Zwei-Migrations-Strategie.

## Überblick

Die hexToBinaryTrainer2Backend ist eine FastAPI-Anwendung mit PostgreSQL-Datenbank, die in Docker-Containern deployt wird. Die Architektur folgt dem Repository-Pattern mit klarer Trennung zwischen API-Layer, Business-Logic und Datenzugriff.

## Community-Struktur (Knowledge Graph)

Basierend auf dem Knowledge Graph (graphify-out/GRAPH_REPORT.md) gliederte sich das Projekt
zum Stand Sprint 1 in die Communities 1 – 5. **Community 6 (Game Economy) ist seit Sprint 4
aus dem Code ergänzt** — der Graph-Lauf von 2026-10-16 kennt sie noch nicht.

### 1. API Endpoints & Request Types (Community 1)
**Hauptknoten:** `AnswerRequest`, `UserRequest`, `UserType`, `ResultResponse`, `UserList`

**Responsibility:** HTTP-API-Schnittstelle, Request/Response-Validierung

**Nachbarn:** `main.py` Endpoints, `pydantic_types.py`

**Status:** ✅ Konsolidiert (US-04: UserType.model_validate)

---

### 2. User Data Model & Repository (Community 2)
**Hauptknoten:** `User` (SQLAlchemy), `get_user`, `get_users`, `update_user_data`

**Responsibility:** Domänenmodell, Datenbankzugriff, Business-Logik

**Nachbarn:** `user_repository.py`, `database/engine.py`

**Status:** ✅ DTO-frei (US-03: Repository entkoppelt)

---

### 3. Configuration & Settings (Community 3)
**Hauptknoten:** `Settings`, `get_settings`, `config.py`

**Responsibility:** Anwendungskonfiguration, Environment-Variablen, Datenbank-URLs

**Nachbarn:** `main.py`, `engine.py`, `init_db.py`

**Status:** ✅ Factory-Pattern (US-05: get_settings mit lru_cache)

---

### 4. Database Initialization (Community 4)
**Hauptknoten:** `init_db`, `wait_for_db`, `init_models`

**Responsibility:** Datenbank-Schema-Erstellung, Connection-Management, Timeout-Handling

**Nachbarn:** `engine.py`, `config.py`

**Status:** ✅ Robust (US-07: Timeout + Logging)

---

### 5. Testing Infrastructure (Community 5)
**Hauptknoten:** `conftest.py`, `test_db_config`, `async_engine`, `session`

**Responsibility:** Test-Fixtures, In-Memory-DB, Dependency-Override

**Nachbarn:** Alle Test-Dateien

**Status:** ✅ Vollständig (US-01: Testfundament)

---

### 6. Game Economy (Community 6 — Sprint 4, NEU)

**Hauptknoten:** `UserGameProfile`, `AnswerRequest`/`AnswerResponse`, `ProfileResponse`,
`LeaderboardEntryV2`, `game_config`, `xp_engine`, `level_service`, `unlock_service`,
`answer_service`, `game_service`, `profile_service`, `leaderboard_service`

**Responsibility:** Die komplette Sprint-4-XP-Economy — XP-Berechnung, Level-Kurve,
Streaks, Unlocks, Anti-Farming — sowie deren drei HTTP-Flächen.

**Nachbarn:** `src/routers/game.py` (Gate-Reihenfolge), `src/models/game_schemas.py`
(Drahtvertrag), `src/middleware/body_size.py` (4-KiB-Deckel auf dem Answer-Pfad),
`src/services/rate_limit_service.py` (wiederverwendet aus Sprint 3), Migrationen
`v004`/`v005`.

**Status:** ✅ Vollständig (US-25 … US-37), Dokumentation US-40

**Warum eine eigene Community:** Die Economy ist ein reiner Funktionskern (kein I/O, keine
DB, keine Settings) um einen eingefrorenen Konstanten-Registry herum, der mit Auth und
User-Repository nur die Datenbank teilt.

#### Schichten

| Schicht | Module | Verantwortung |
|---------|--------|---------------|
| Registry (eingefroren) | `services/game_config.py` | Single Source of Truth: Modus-Enum, gültige Bitbreiten je Modus, Basis-XP, Modus-Multiplikatoren, Streak-Tiers als rationale Paare, Penalty-Faktor, Unlock-Seed, Rate-Limit. Keine Dezimalzahlen, keine Mutable-Objekte, keine Import-Side-Effects. |
| Pure Layer | `services/xp_engine.py` (US-29), `services/level_service.py` (US-30), `services/unlock_service.py` (US-31) | Arithmetik ohne Zustand: Zuspruch/Strafe, Level-Kurve + `cum(level)`-Boden und Clamp, Unlock-Leiter. Keine Datenbank, kein `await`. |
| Transaktion | `services/game_service.py` (US-28), `services/answer_service.py` (US-33) | Row-Lock (`SELECT … FOR UPDATE`), ein `COMMIT` pro Antwort, Streak-Lifecycle, Komposition der Economy-Naht, Draht-Zusammenbau. |
| Read-Seite | `services/profile_service.py` (US-35/US-37), `services/leaderboard_service.py` (US-36) | Eine gespeicherte Zeile, zwei Sichten (`/profile`, `/api/user/me`) plus die öffentliche Rangliste — damit dieselbe Zeile nicht zwei unterschiedliche Geschichten erzählt. |
| API | `routers/game.py`, `models/game_schemas.py` | Hält nur die **Gate-Reihenfolge** und ihre HTTP-Abbildungen; jede Zahl kommt aus einem Service. |

#### Endpunkte (`/api/game`)

Die beiden Legacy-Writer von Sprint 1 sind weg (US-34); das Leaderboard wurde auf die neue
Quelle umgeschrieben (US-36). Übrig ist eine Schreib- und zwei Leseflächen, alle auf
`user_game_profile`:

| Endpunkt | Auth | Gate-Reihenfolge | Story |
|----------|------|------------------|-------|
| `POST /api/game/answer` | ja | `401` authenticate → `422` validate body → `429` rate-limit (auf eigener Session/Transaktion) → `403` unlock gate (auf dem **Pre-Answer**-Level unter Row-Lock) → `200`/`404` transact | US-32/US-33 |
| `GET /api/game/profile` | ja | `401` → `404` → Read (liest die D-2-Leiter mit, schreibt nie) | US-35 |
| `GET /api/game/leaderboard` | **nein — bewusst öffentlich** (Rev 3 R-3 / OQ-9) | `422` auf `limit` außerhalb 1…100; Missbrauchskontrolle ist das begrenzte `limit`, nicht Auth. Kein PII in den Einträgen. | US-36 |

Repräsentative Datenmodelle: `AnswerRequest` verbietet Extrafelder (`extra="forbid"`),
`is_correct` ist strikt ein JSON-Boolean und `bit_width` wird **pro Modus** geprüft — ein
client-seitig berechnetes `total_xp` oder ein `user_id` im Body können also nicht
eingeschmuggelt werden. `bit_width` trägt in **jedem** Modus Bits (OQ-7): 4/8/16/32 für
die drei Konvertierungs-Modi, 8/16/24/32 für `unicode` (= 1…4 Bytes).

Der Trust-Boundary endet beim **Urteil**: `is_correct` ist die Behauptung des Clients und
der Server hat kein Oracle, das es prüfen könnte (kein Challenge-Nonce, keine
Answer-History) — das Rate-Limit ist die einzige Gegenmaßnahme und begrenzt die
Request-Rate, nicht das XP. Als Tech-Debt hinterlegt:
[backlog TD-01 — Challenge-Nonces/HMAC](backlog.md) und
[backlog TD-02 — Answer-History-Event-Tabelle](backlog.md).

Normative Zahlen (XP-Kurven, Tiers, Golden Matrix, Penalty-, Level- und Unlock-Tabellen):
**[docs/xp-economy.md](xp-economy.md)** — gespiegelt aus `sprint4-plan.md` Section 4 und
durch die Golden-Tests von US-29/US-41 festgehalten.

#### Datenhaltung: `user_game_profile`

Die Economy-Zeile pro Benutzer (`src/models/game.py`): `user_id` (PK **und** FK auf
`users.id` mit `ON DELETE CASCADE`), `total_xp`, `level`, `current_streak`, `best_streak`,
`correct_count`, `incorrect_count`, `updated_at`. Vier `CHECK`-Constraints
(`total_xp >= 0`, `level >= 1`, `current_streak >= 0`, `best_streak >= 0`) plus der Index
`ix_user_game_profile_total_xp (total_xp DESC)`, der das Leaderboard stützt.

**`level` ist gespeichert, aber nirgends die Quelle** (Phase-2b-Befund H1): die Spalte ist
eine Denormalisierung für Roh-SQL-Leser. Jeder Report-Pfad — `/answer`, `/profile`, `/me`
und das Leaderboard — leitet sie mit `level_service.level_from_xp()` aus `total_xp` ab und
liest die Spalte nie. Genau ein Level, genau eine Quelle.

Die Zeile entsteht **lazy** beim ersten `POST /api/game/answer` (US-28 AC-3) — kein Insert
bei Registrierung, keins bei `GET /profile`. `total_xp` ist immer ein Vielfaches von 4
(D-8), der XP-Boden ist `cum(level)`, nicht 0 (OQ-4): `total_xp >= cum(level)` ist als
geometrische Summe **nicht** als SQL-`CHECK` ausdrückbar, deshalb ist der Constraint oben
nur ein Backstop und der echte Clamp lebt in der Economy-Schicht
(`level_service.clamped_total_xp()`, angewandt von US-33; festgehalten durch LV-08/LV-09
und US-41).

#### Migrations-Strategie (D-6 / Rev 3 R-1)

Das Schema kam in **zwei** Migrationen statt einer, damit kein Commit das Repo rot
hinterlässt — ein einzelnes `DROP` am Tag-1-Nachmittag hätte die noch lebenden Legacy-Router
und `tests/test_user_me.py` gebrochen (App-Start und Pytest-Collection wären ~1,5 Tage rot
gewesen):

| Migration | Geliefert mit | Aktion |
|-----------|---------------|--------|
| **v004** (additiv) | US-27 | `CREATE TABLE IF NOT EXISTS user_game_profile …` — die Legacy-Zähler-Tabelle bleibt **liegen**, die alten Router dienen weiter, die Suite bleibt grün. |
| **v005** (destruktiv) | US-34, Sprint-Close — **gleicher Release-Zug wie der Frontend-Wechsel** | `DROP TABLE IF EXISTS …` über die Konstante `LEGACY_TABLE` in der v005-Migration unter [`src/migrations/`](../src/migrations/) — erst zulässig, nachdem US-33/US-36/US-37 jeden Leser und jeden Writer entfernt hatten. Der Tabellenname lebt nur noch im Code; dieses Dokument nennt ihn bewusst nicht, damit er nicht als existent gelesen wird. |

Beide sind idempotent über das bestehende `migration_already_applied` /
`schema_migrations`-Muster und in `src/main.py` Lifespan nach `v003` registriert.
**Kein Backfill, keine Dual-Write von Werten** (D-6): die Datenbank war leer, XP wird
ausschließlich nach `user_game_profile` geschrieben. Das Coexistence-Fenster zwischen v004
und v005 war ein explizit akzeptierter, dokumentierter Zwischenzustand.

---

## Isolierte Nodes

### `postgres_data` Volume
**Typ:** Docker-Volume (kein Code-Knoten)

**Zweck:** Persistente Speicherung der PostgreSQL-Daten

**Status:** ✅ Dokumentiert (kein Code, nur Infrastruktur)

**Entscheidung:** Kein Merge in andere Community, da Volume ein reines Deployment-Artefakt ist.

---

### Package-Root `hextobinarytrainer2backend`
**Typ:** Python-Package-Init (`src/__init__.py`)

**Zweck:** Markiert `src/` als Python-Package für Imports

**Status:** ✅ Dokumentiert (Standard-Python-Pattern)

**Entscheidung:** Bewusst isoliert, da `__init__.py` keine Logik enthält.

---

## Dünne Communities (≤ 2 Nodes)

| Community | Inhalt | Entscheidung |
|-----------|--------|--------------|
| Deployment | `Dockerfile`, `docker-compose.yaml` | ✅ Bewusst klein: Nur 2 Infra-Dateien, keine Code-Logik |
| CI/CD | `.github/workflows/deploy.yml` | ✅ Bewusst klein: Single-File-Workflow, Test-Gate + Deploy |
| Environment | `.env.example`, `.env` | ✅ Bewusst klein: Nur Konfig-Dokumentation |
| Graphify-Output | `graphify-out/*` | ✅ Verschwindet bei nächstem Rebuild: Generierte Artefakte, kein Source-Code |

**Status nach US-08:** 4 dünne Communities (alle begründet)

**Sprint-Gate erfüllt:** ≤ 4 dünne Communities ✓

---

## BEHOBENE Code-Gaps

### 1. Debug-Print in incorrect_answer
**Status:** ✅ BEHOBEN (US-03)

**Fix:** `print(request)` in `incorrect_answer` entfernt (main.py Zeile 74)

**Referenz:** US-03 Acceptance Criteria #4

**Historie (Sprint 4):** der Handler selbst existiert nicht mehr — US-34 hat die beiden
Sprint-1-Writers samt ihrer Tabelle entfernt. Dieser Eintrag dokumentiert den Fix von
2026-10-16, nicht eine aktuelle Code-Stelle.

---

### 2. Fehlende bits-Validierung
**Status:** ✅ BEHOBEN (US-03)

**Fix:** `Field(gt=0)` zu `AnswerRequest.bits` hinzugefügt

**Referenz:** US-03 Acceptance Criteria #3

**Nachfolger (Sprint 4):** Das Feld heißt heute `bit_width` und trägt in **jedem** Modus
Bits (OQ-7). Die Validierung ist eine pro-Modus-Whitelist aus
`game_config.VALID_WIDTHS_BY_MODE` (4/8/16/32 für die Konvertierungs-Modi, 8/16/24/32 für
`unicode`), plus `strict=True` gegen String-/Float-Koerzition und `extra="forbid"` gegen
eingeschmuggelte Felder — also deutlich mehr als `gt=0`. Festgehalten in US-32 und
`tests/test_input_validation.py`.

---

### 3. Doppelte Schema-Init
**Status:** ✅ BEHOBEN (US-07)

**Fix:** `init_db.py` ist Single Source of Truth, `main.lifespan` erstellt nur bei Bedarf

**Referenz:** US-07 Acceptance Criteria #4

---

## Architektur-Entscheidungen

### Repository-Pattern
**Entscheidung:** Repository-Schicht arbeitet mit primitiven Typen (str, int), nicht mit Pydantic-DTOs

**Begründung:** Entkopplung von Web-Framework, bessere Testbarkeit, Wiederverwendbarkeit

**Referenz:** US-03

---

### Factory-Pattern für Settings
**Entscheidung:** `get_settings()` mit `@lru_cache` statt Modul-Level-Singleton

**Begründung:** Bessere Testbarkeit, explizite Abhängigkeiten, kein Import-Side-Effect

**Referenz:** US-05

---

### Model-Validate für ORM-Mapping
**Entscheidung:** `UserType.model_validate(user)` statt manueller Feldzuordnung

**Begründung:** DRY, weniger Fehler, automatische Validierung

**Referenz:** US-04

---

### CI-Test-Gate
**Entscheidung:** Deploy nur nach erfolgreicher Test-Pipeline

**Begründung:** Verhindert kaputte Deployments, automatisiert Quality-Gate

**Referenz:** US-06

---

## Weitere Informationen

- **XP-Economy-Nachschlagewerk (Sprint 4):** [`docs/xp-economy.md`](xp-economy.md) — die normativen Tabellen (XP-Kurven, Streak-Tiers, Golden Matrix, Strafen, Level, Unlocks) ohne den Rev-Entstehungskontext
- **Sprint-4-Entscheidungsaufzeichnung:** [`docs/sprint4-plan.md`](sprint4-plan.md) — Section 3 (D-1 … D-8), Section 4 (Tabellen), Section 5 (User Stories), Section 9 (Open-Questions-Entscheidungen)
- **Backlog (inkl. Economy-Tech-Debt):** [`docs/backlog.md`](backlog.md)
- **Auth-Hardening-Entscheidungen:** [`docs/adr/001-auth-hardening.md`](adr/001-auth-hardening.md)
- **Knowledge Graph:** `graphify-out/GRAPH_REPORT.md` (Stand Sprint 1; die Game-Community ist dort noch nicht enthalten — Quelle dieser Sektion ist der Code, nicht der Graph)
- **README:** [`README.md`](../README.md)
