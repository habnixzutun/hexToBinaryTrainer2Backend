# Sprint-Backlog — hexToBinaryTrainer2Backend

> Stand: 2026-10-16 · Quelle: Knowledge Graph (`graphify-out/GRAPH_REPORT.md`, 59 Nodes / 99 Edges / 11 Communities) + Code-Audit
> Sprint-Ziel: **Ein verifizierter, getesteter Kern** — God-Nodes ≥ 80 % Coverage, Repository-Schicht frei von API-DTO-Abhängigkeiten, robuste Container-Init. Kein neues Feature vor dieser Grundlage.

## Befund-Lage (harte Fakten aus dem Code)

| Befund | Beleg |
|---|---|
| 0 % Test-Coverage, kein `tests/`-Verzeichnis, kein pytest in `pyproject.toml` | Projektroot |
| Repository importiert API-DTO: `user_repository.py` → `from src.pydantic_types import AnswerRequest` | Gott-Node `AnswerRequest`, Betweenness 0.053, 3 Communities |
| `UserType`-Mapping 3× dupliziert in `main.py` (Zeilen 54–59, 64–69, 76–81) | `User`-Domänenmodell vs. DTO |
| Debug-Rest: `print(request)` in `incorrect_answer`, fehlende Validierung von `bits` (negative Werte → Punkte-Manipulation) | `main.py` Z. 74, `pydantic_types.py` |
| Schema-Init doppelt: `lifespan` (`create_all`) UND `init_db.py` — zwei Sources of Truth | Community "Database Initialization" |
| `wait_for_db()` endet nie (kein Timeout), `print` statt `logging`, `DB_PORT` aus Env wird ignoriert (hart 5432) | `init_db.py` |
| `Settings`-Singleton mit Import-Side-Effect (`settings = Settings()`) — Bridge-Node, Betweenness 0.060 | `config.py` |
| `ruff` als Runtime-Dependency in `[project.dependencies]` | `pyproject.toml` |
| Isolierte Nodes: `hextobinarytrainer2backend` (Package-Root), `postgres_data` Volume; 7 dünne Communities | GRAPH_REPORT Knowledge Gaps |

---

## US-01: Testfundament und God-Node-Coverage ≥ 80 %

**Priorität:** P0 · **Komplexität:** MEDIUM · **Dependencies:** keine (Sperrtor für alle Folge-US)

Als Entwickler möchte ich ein pytest-Fundament mit deterministischen Tests für die acht God-Nodes, damit jede anschließende Refaktorierung (US-03 bis US-05) durch ein grünes Netz abgesichert ist.

**Akzeptanzkriterien:**
1. `pytest`, `pytest-asyncio` und `pytest-cov` sind als Dev-Dependencies in `pyproject.toml` konfiguriert; `uv run pytest --cov=src --cov-report=term-missing` läuft fehlerfrei.
2. Unit-Tests existieren für `AnswerRequest`, `UserType`, `UserRequest`, `ResultResponse`, `UserList` inkl. `__str__` von `AnswerRequest`.
3. Repository-Tests (gegen SQLite/aiosqlite oder Testcontainers) existieren für `get_user`, `get_users`, `update_user_data` — je mindestens: Happy Path, unkannter Nutzer (Auto-Create), Punktelogik (`+bits` bei korrekt, `−4·bits` bei inkorrekt), Leaderboard-Sortierung (points DESC, incorrect ASC, Limit 10).
4. Endpoint-Tests via `httpx.AsyncClient`/`TestClient` für `/api/leaderboard`, `/api/user`, `/api/correct`, `/api/incorrect` (Statuscode, Response-Schema, DB-Seiteneffekt).
5. **Cross-Community-Integrationstest** (verbessert Punkt 10): ein Test fährt kompletten Flow `POST /api/user` → `POST /api/correct` → `GET /api/leaderboard` über drei Communities hinweg (API → Repository → Modell).
6. Gesamt-Coverage ≥ 80 %, God-Nodes (`AnswerRequest`, `User`, `get_user`, `correct_answer`, `incorrect_answer`, `UserType`, `update_user_data`, `get_users`) einzeln ≥ 80 % (per `--cov`-Report belegt).
7. Die in US-02 gefundenen Debug-Reste (`print(request)`) sind durch Tests ersetzt bzw. entfernt — *nur falls US-02 vor US-01 landet; sonst US-03*.

---

## US-02: Graph-Hygiene — Dangling-, Collapsed- und INFERRED-Edges triagieren

**Priorität:** P0 · **Komplexität:** LOW · **Dependencies:** keine (read-only Analyse, kann parallel zu US-01)

Als Entwickler möchte ich die 28 Dangling-, 13 Collapsed- und 8 INFERRED-Edges des Knowledge Graphs klassifiziert und bereinigt haben, damit der Graph als verlässliche Grundlage für spätere Architekturentscheidungen dient.

**Akzeptanzkriterien:**
1. Jede der 28 Dangling-Edges ist in einer Tabelle in `docs/backlog.md` (oder folgendem Doc) klassifiziert als: (a) echter Code-Gap, (b) External/Stdlib-Referenz (akzeptabel), (c) Graph-Artefakt. Kategorie (a) muss einen konkreten Fix-Beleg (Datei/Zeile) enthalten.
2. Jeder der 13 Collapsed-Edges ist aufgelöst oder als bewusste Zusammenfassung dokumentiert (z. B. mehrere Endpoint-Kanten auf denselben Knoten).
3. Alle 8 INFERRED-Edges sind gegen den Quellcode verifiziert: `get_user→UserRequest`, `get_user→UserType`, `correct_answer→AnswerRequest`, `correct_answer→UserType`, `AnswerRequest`-Kanten (3×) und die Hyperedge der Deploy-Flow (0.85). Verifizierte werden im nächsten `graphify`-Lauf als EXTRACTED bestätigt, falsche entfernt; Rest-Confidenz im Report ≥ 0.95, 0 verbleibende UNVERIFIED.
4. Als echter Code-Gap dokumentiert und als Story angelegt: (a) `print(request)`-Debug-Rest in `incorrect_answer` (`main.py` Z. 74), (b) fehlende Randwert-Validierung `bits > 0` in `AnswerRequest`, (c) doppelte Schema-Init `lifespan` vs. `init_db.py`. Fixes selbst liegen in US-03/US-07.
5. Nach Bereinigung wird der Graph neu extrahiert; Dangling-Count ≤ 5 (nur noch belegte External-Referenzen), Bericht ist unter `graphify-out/` versioniert.

---

## US-03: AnswerRequest entkoppeln — Repository-Schicht DTO-frei

**Priorität:** P1 · **Komplexität:** MEDIUM · **Dependencies:** US-01 (grüne Tests vor Umbau)

Als Entwickler möchte ich, dass die Repository-Schicht keine Pydantic-API-Types importiert, damit die Communities "API Endpoints & Request Types" und "User Data Model & Repository" sauber getrennt sind und das Repository unabhängig vom Web-Framework wiederverwendbar bleibt.

**Akzeptanzkriterien:**
1. `src/repositories/user_repository.py` enthält keinen Import aus `src.pydantic_types`; `update_user_data` übernimmt `(db, name: str, bits: int, is_correct: bool)` oder einDomänen-Dataclass (`@dataclass(frozen=True) AnswerOutcome`).
2. Die Anpassung `AnswerRequest → Repository-Argumente` passiert ausschließlich in `main.py` (Systemgrenze).
3. `AnswerRequest.bits` validiert `gt=0` via `Field(gt=0)` (Punkte-Manipulation durch negative Werte ist gesperrt); ein Test prüft 422 bei `bits = 0` und `bits = -5`.
4. `print(request)` in `incorrect_answer` ist ersatzlos entfernt (Throughput/Kosten; Logging über `logging`- Modul falls nötig).
5. Neuextrahierter Graph: Betweenness von `AnswerRequest` ≤ 0.01, keine Kante Repository↔pydantic_types. Alle Tests aus US-01 bleiben grün (Verhaltensgleichheit außer 422-Fall).

---

## US-04: User-Domänenmodell und UserType DTO konsolidieren

**Priorität:** P1 · **Komplexität:** MEDIUM · **Dependencies:** US-01, US-03 (gleiche Dateien, sequenziell um Konflikte zu vermeiden)

Als Entwickler möchte ich eine einzige, zentrale Mapping-Stelle zwischen `User` (SQLAlchemy) und `UserType` (Pydantic), damit die dreifach kopierte Feldzuordnung in `main.py` nicht bei jedem neuen Feld zu stillen Bugs führt.

**Akzeptanzkriterien:**
1. `UserType` erhält `model_config = ConfigDict(from_attributes=True)`; die Konvertierung `User → UserType` passiert genau einmal pro Endpoint via `UserType.model_validate(user)` — keine manuelle Feldliste mehr in `main.py`.
2. Es existiert höchstens eine Mapping-Implementierung im gesamten `src/` (grep-Beleg: `UserType(` mit vier Feldzuweisungen kommt in `main.py` nicht mehr vor).
3. Ein Regressionstest vergleicht für einen Nutzer mit `correct=3, incorrect=1, points=17` die API-Antwort feldgenau gegen die Erwartungen (schützt vor Vertippern bei Refaktorierung).
4. Alle Tests aus US-01 bleiben grün; Response-Schema unverändert (kein Breaking Change für den Client).

---

## US-05: Settings-Bridge-Node auflösen —konfigurierbare App ohne Import-Side-Effects

**Priorität:** P1 · **Komplexität:** LOW · **Dependencies:** US-01

Als Entwickler möchte ich, dass `Settings` und `engine` nicht beim Import instanziiert werden, damit Tests, `init_db.py` und die App dieselbe Konfiguration ohne Umwege nutzen und der Bridge-Node (Betweenness 0.060) keine implizite Kopplung mehr trägt.

**Akzeptanzkriterien:**
1. `Settings()` wird nicht mehr auf Modul-Ebene von `config.py` instantiiert; Bereitstellung über eine Factory-Funktion `get_settings()` mit `functools.lru_cache` (oder FastAPI-`Depends`-Muster).
2. `src/database/engine.py` erzeugt den Engine innerhalb einer Funktion `create_engine_from_settings(settings)`; das globale `engine`-Objekt bleibt alskompatibler Default erhalten oder wird konsequent ersetzt (entscheiden, Begründung in Commit).
3. Tests können die DB-URL per Env-Override setzen, ohne Datei-Mocks.
4. Neuextrahierter Graph: `Settings`-Betweenness ≤ 0.02 oder dokumentierte Akzeptanz mit Begründung in `docs/` (Bridge darf bleiben, wenn sie nur noch explizite Injektion bedient).

---

## US-06: Containerized Deployment härten + CI-Testgate

**Priorität:** P2 · **Komplexität:** MEDIUM · **Dependencies:** US-01 (Gate braucht Tests), US-07 (Init-Reihenfolge im CMD)

Als Betreiber möchte ich ein reproduzierbares, abgesichertes Deployment mit automatischem Test-Gate, damit kein Deploy auf der Produktivmaschine stattfindet, solange die God-Node-Tests rot sind.

**Akzeptanzkriterien:**
1. `.github/workflows/deploy.yml` führt vor dem SSH-Deploy einen Job `test` aus: `uv sync` → `ruff check` → `pytest --cov=src --cov-fail-under=80`. Der Deploy-Job hat `needs: test`; grüner Build ist Merge-/Deploy-Voraussetzung (Sprint-Gate automatisiert).
2. `ruff` wandert aus `[project.dependencies]` in eine Dev-Dependency-Gruppe; das Produktions-Image enthält es nicht mehr (`uv sync --no-dev` bleibt wie gehabt, Beleg: Image-Größe/Vendor-Check).
3. Dockerfile: Container läuft als Non-Root (`USER appuser` nach `RUN adduser`), `uv`-Version ist gepinnt (`COPY --from=ghcr.io/astral-sh/uv:X.Y.Z`) statt `:latest`.
4. `docker-compose.yaml`: api-Service erhält einen `healthcheck` (z. B. `GET /api/leaderboard` oder ein leichtes `/healthz` — falls `/healthz` neu ist, fällt er unter diese US als einziger neuer Endpoint, mit Test); CPU-/Memory-Limits (`mem_limit`) für api und db sind gesetzt.
5. `docker compose config` validiert fehlerfrei; alle Env-Variablen des Compose-Files sind in `README.md` (oder `.env.example`) dokumentiert.

---

## US-07: Database-Initialization robust und eindeutig machen

**Priorität:** P2 · **Komplexität:** MEDIUM · **Dependencies:** US-05 (Settings-Wiederverwendung), US-06 (CMD-Reihenfolge)

Als Betreiber möchte ich ein `init_db.py` mit Timeout, echtem Logging und einer einzigen Schema-Source-of-Truth, damit ein nicht erreichbares PostgreSQL den Container nicht ewig blockiert und niemand mehr raten muss, wer Tabellen anlegt.

**Akzeptanzkriterien:**
1. `wait_for_db()` bricht nach konfigurierbarer Höchstzeit (Standard 60 s, Env `DB_WAIT_TIMEOUT`) mit eindeutigem Fehlercode (`sys.exit(1)` bzw. Raise) ab; ein Test simuliert permanente `PostgresError` und prüft den Timeout (kein Endlos-Loop).
2. Alle `print()`-Aufrufe in `init_db.py` sind durch `logging`-Aufrufe mit Level-Information ersetzt (Warnung bei Warte-Schleife, Info bei Erfolg).
3. `DB_PORT` aus der Umgebung wird respektiert (Fallback 5432 bleibt); die harte Klammer `@{DB_HOST}:5432/` verschwindet.
4. Genau eine Stelle besitzt das Schema: `init_db.py` ist die Single Source of Truth; der `create_all`-Aufruf in `main.lifespan` wird entfernt ODER auf ein sicheres `checkfirst`-Verhalten mit klarem Kommentar umgestellt — Entscheidung + Begründung im Commit, Verhalten dokumentiert in `README.md`.
5. `init_db.py` nutzt `Settings`/`get_settings()` aus US-05 statt paralleler `os.getenv`-Logik (Doppeldefinition der DB-URL in `init_db.py` entfällt).
6. Unit-Tests für `wait_for_db` (Erfolg, Timeout, URL-Konstruktion) existieren und zählen zur Coverage aus US-01.

---

## US-08: Isolierte Nodes und dünne Communities dokumentieren

**Priorität:** P3 · **Komplexität:** LOW · **Dependencies:** US-02 (Classification liegt vor), US-03/US-04/US-05 (Architekturänderungen müssen eingepflegt sein)

Als neuer Teammitglied möchte ich eine aktuelle Architektur-Doku, die isolierte Graph-Knoten und dünne Communities erklärt, damit ich ohne Reverse-Engineering verstehe, warum `postgres_data`, das Package-Root und sieben dünne Communities existieren.

**Akzeptanzkriterien:**
1. `docs/architecture.md` (neu) beschreibt die Community-Struktur nach dem Refactoring: jede der ≥ 4 signifikanten Communities mit Responsibility, Hauptknoten und Nachbarn.
2. Die beiden isolierten Nodes sind aufgelöst oder begründet: `postgres_data` Volume (Daten-Persistenz, kein Code-Knoten — Doku genügt) und Package-Root `hextobinarytrainer2backend` (`src/__init__.py`-Zweck dokumentiert).
3. Jede der 7 dünnen Communities (≤ 2 Nodes) ist in einer Tabelle mit einer Zeile versehen: Name, Inhalt, Entscheidung ("merge in Community X", "bewusst klein", "verschwindet durch US-0x").
4. Nach einem frischen `graphify`-Lauf ist die Zahl dünner Communities ≤ 4 oder jede Ausnahme ist begründet; `README.md` verlinkt auf `docs/architecture.md`.
5. Die drei in US-02 dokumentierten Code-Gaps (print-Rest, `bits`-Validierung, doppelte Schema-Init) sind in der Doku als BEHOBEN (mit Story-Referenz) markiert.

---

## US-34-Nachzug: Leaderboard-Index und Schutz des öffentlichen Endpoints

**Priorität:** P1 · **Komplexität:** LOW · **Dependencies:** US-34 (v005 liegt in dieser Story)
**Quelle:** Sprint-4-Code-Review Phase 2b, Befund **M3** (US-35/36/37, PR zu `d17c10b`) — bewusst **nicht** in diesem PR gefixt, weil die Migration (v005) zu US-34 gehört.

Als Betreiber möchte ich, dass `GET /api/game/leaderboard` auch bei wachsender `user_game_profile`-Tabelle ohne Sort-Schritt bedienbar bleibt, und dass der öffentliche Endpoint (Rev 3 R-3 / OQ-9: keine Auth) gegen Request-Serien abgesichert ist, damit das 30-Sekunden-Polling der Frontends kein DoS-Vektor wird.

**Akzeptanzkriterien:**

1. v005 ersetzt den Ein-Spalten-Index `ix_user_game_profile_total_xp (total_xp DESC)` aus v004 durch den dreispaltigen Index **`(total_xp DESC, incorrect_count ASC, user_id ASC)`** — exakt die Totalordnung, nach der `leaderboard_service.leaderboard_entries()` sortiert. Beleg im PR: `DROP INDEX IF EXISTS` + `CREATE INDEX IF NOT EXISTS`, beides idempotent und über `schema_migrations` registriert; `Index(...)` in `src/models/game.py` analog angepasst (eine Quelle für Migration und ORM).
2. Der Query-Plan ist vor und nach der Migration mit `EXPLAIN (ANALYZE, BUFFERS)` gemessen und im PR-Text dokumentiert: kein `Sort`-Node mehr auf dem Leaderboard-Zweig, Index-Scan statt Seq-Scan + Sort. (Der Dreier-Index deckt auch die Tiebreaks; ein reiner `total_xp`-Index muss die Zeilen trotzdem noch sortieren.)
3. **Optional, PO-Entscheidung (OQ-9):** `Cache-Control`/`ETag` **oder** ein Per-IP-Rate-Limit für den öffentlichen Leaderboard-Endpoint. Begründung: Das validierte `limit` (1..100) begrenzt die **Zeilenmenge** einer Antwort, nicht die **Request-Rate** — ein anonymer Caller kann die teure Sortierung beliebig oft auslösen. Variante A (HTTP-Caching) ist billiger und greift genau beim wiederholten Polling derselben Client-Seite; Variante B schützt die Datenbank, braucht aber eine zweite Begrenzung neben der bestehenden per-identity.
4. Bestehende Tests bleiben grün; `tests/test_migrations.py` deckt die v005-Idempotenz und die Anwesenheit des neuen Index ab (Spiegelbild zu AC „`total_xp`-Index“ aus US-27).

---

## Sprint-Reihenfolge und Gate

```
Welle 1 (parallel):   US-01 ──────────────┐   US-02 (read-only)
Welle 2:              US-03 → US-04       │   US-05
Welle 3:              US-07 (braucht 05)  │   US-06 (braucht 01+07)
Welle 4:              US-08 (braucht 02–07)
```

**Sprint-Gate (vor Abnahme prüfen):**
1. Alle Akzeptanzkriterien US-01…US-08 erfüllt? (Kriterien sind testbar — Abnahme per Test-Run/Grep-Beleg, nicht per Gefühl)
2. `pytest --cov=src --cov-fail-under=80` grün? God-Nodes einzeln ≥ 80 %?
3. `ruff check` fehlerfrei (inkl. Ausstieg aus Runtime-ruff)?
4. Frischer `graphify`-Lauf: 0 UNVERIFIED INFERRED-Edges, Dangling ≤ 5, `AnswerRequest`-Betweenness ≤ 0.01?
5. Review/Retro gehalten?

**Bewusst NICHT im Sprint (kein Gold-Plating):** Auth/JWT, Rate-Limiting, neue Live-Feature-Kategorien (Quizzes/Word-Clouds), WebSocket-Hub. Diese gehören in das MentimeterKlon-Backlog, nicht in die Konsolidierung dieses Trainers — getrennte Projekte, getrennte Backlogs.
