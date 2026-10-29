# Sprint-Backlog — hexToBinaryTrainer2Backend

> Stand: 2026-10-16 · Quelle: Knowledge Graph (`graphify-out/GRAPH_REPORT.md`, 59 Nodes / 99 Edges / 11 Communities) + Code-Audit
> Sprint-Ziel: **Ein verifizierter, getesteter Kern** — God-Nodes ≥ 80 % Coverage, Repository-Schicht frei von API-DTO-Abhängigkeiten, robuste Container-Init. Kein neues Feature vor dieser Grundlage.
>
> **Historischer Stand (Sprint 1, erledigt):** die US-01 … US-08 dieses Dokuments sind der
> Konsolidierungs-Backlog von vor der Auth-Ära und **abgearbeitet**. Wo sie noch die
> Sprint-1-Punktelogik (`+bits` / `−4·bits`), ihre zwei Scoring-Routen oder die
> Legacy-Statistiktabelle erwähnen, ist das **Zeitgeschichte** — US-34 (Sprint 4) hat Ende
> Oktober 2026 beides entfernt und durch die XP-Economy ersetzt
> ([`docs/xp-economy.md`](xp-economy.md)). Aktuelle offene Punkte: **TD-01/TD-02** unten
> sowie die `US-34-Nachzug`-Story.

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
4. Endpoint-Tests via `httpx.AsyncClient`/`TestClient` für `/api/leaderboard`, `/api/user` und die beiden Sprint-1-Scoring-Routen (`correct`, `incorrect` — in US-34 entfernt) (Statuscode, Response-Schema, DB-Seiteneffekt).
5. **Cross-Community-Integrationstest** (verbessert Punkt 10): ein Test fährt kompletten Flow `POST /api/user` → Score-Antwort über die Legacy-Scoring-Route → `GET /api/leaderboard` über drei Communities hinweg (API → Repository → Modell).
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

## TD-01: Server-issued Challenge-Nonces / HMAC-signierte Antworten

**Priorität:** Niedrig (P3) · **Komplexität:** HIGH · **Dependencies:** Produktionsdaten
**Quelle:** `docs/sprint4-plan.md` Section 1 (Out of Scope) + Risk Table · Wave-2a-Security-Review, akzeptiertes Risiko **T-01** (PO-Entscheidung 2026-10-27) · bewusst hier hinterlegt statt im Sprint 4 gelöst (US-40)

**Kontext — welches Loch offen bleibt.** `POST /api/game/answer` nimmt `is_correct` als
Behauptung des Clients entgegen. Der Server hat **kein Oracle**, das er dagegen prüfen
könnte: kein Challenge-Nonce, keine Answer-History. Jede XP-Zahl wird also folgerichtig aus
einer Prämisse berechnet, die der Client selbst gewählt hat — ein Client, der ausschließlich
`is_correct: true` sendet, farmt XP, ohne eine einzige gültige Frage beantwortet zu haben.
Die im Schema festhaltene Probe ist
`tests/test_answer_endpoint.py::test_t01_is_correct_is_client_asserted_with_no_oracle`.

Die **einzige** Gegenmaßnahme im Sprint 4 ist das Rate-Limit (120 Antworten / 60 s,
`game_config.ANSWER_RATE_LIMIT`), und das ist eine **DoS-Control, kein XP-Deckel**: es
begrenzt die Request-Rate, nicht den Wahrheitsgehalt einer Antwort. Ein Farmer, der unter
dem Limit bleibt, steigt im Level trotzdem.

**Beschreibung.** Der Server generiert pro gestellter Frage eine einmalige Challenge
(Nonce + TTL, bound an `user_id` und die konkrete Aufgabe). Der Client muss das Urteil
signiert zurückliefern — z. B. `HMAC(server_secret, user_id | challenge_id | is_correct)` —
und der Server verwirft die Antwort ohne gültige Signatur, ohne Nonce-Redeem oder mit
abgelaufenem Nonce. XP gibt es dann nur noch für eine Antwort, deren Urteil der Server
selbst ausgestellt hat.

**Wann das angefasst wird (Trigger, nicht "irwann").** Rate Limiting erweist sich als
unzureichend — messbar an Leaderboard-Anomalien (XP-Deltas pro `user_id` außerhalb der
§4.6-Matrix aus [`docs/xp-economy.md`](xp-economy.md)), Häufung von Rate-Limit-Hits ohne
passende Spielsitzung, oder einem Support-Fall, in dem ein redlicher Spieler gegen einen
Farming-Account reklamiert. **Ohne diese Daten ist der Bau YAGNI.**

**Trade-offs, die vorher entschieden sein müssen.**

1. **Zustand:** Nonce-Tabelle mit TTL-Sweep (zweite heiße Tabelle, zweiter Cleanup-Pfad)
   oder stateless über ein kurzlebiges signiertes Token (kein Sweep, aber Revocation ist
   schwieriger). Der bestehende `rate_limit_attempt`-Sweep ist die Referenz für Variante A.
2. **API-Bruch:** ein Additive-Field-Wechsel am Answer-Body bricht jeden aktuellen Client —
   `extra="forbid"` macht das explizit. Braucht denselben Frontend-Release-Zug wie v005.
3. **UX:** Retry nach Disconnect/Timeout braucht entweder dasselbe Nonce (Replay-Fenster)
   oder eine neue Aufgabe (Strafe für Infrastruktur-Pech). Beides ist Produktentscheidung.
4. **Was es nicht löst:** ein kompromittierter Client, der echte Aufgaben löst, bleibt
   ununterscheidbar — dafür ist TD-02 nötig.

---

## TD-02: Answer-History als Event-Tabelle

**Priorität:** Niedrig (P3) · **Komplexität:** MEDIUM · **Dependencies:** Produktionsdaten
**Quelle:** `docs/sprint4-plan.md` Section 1 (Out of Scope) + Risk Table · Wave-2a-Security-Review (Forensik-Lücke neben T-01) · hinterlegt von US-40

**Kontext — welche Spur fehlt.** Es gibt **keinen Forensik-Trail** für XP-Farming-Ermittlungen.
`user_game_profile` speichert nur die Aggregatwerte (`total_xp`, `level`, `current_streak`,
`best_streak`, `correct_count`, `incorrect_count`); aus ihnen lässt sich rückwärts nicht
ableiten, *welche* Antworten den Stand erzeugt haben. Der **einzige** Beleg ist der
Rate-Limit-Counter in `rate_limit_attempt` — und der **expiriert mit seinem Fenster**, ist
also eine flüchtige Spur, die genau dann verschwindet, wenn der Schaden auffällt (ein
Quarter nach dem Farming).

**Beschreibung.** Append-only-Event-Tabelle, ein Row pro beantworteter Frage, geschrieben in
derselben Transaktion wie das Economy-Update (US-28 macht beides bereits atomar — ein
`COMMIT` pro Antwort, siehe [`docs/architecture.md`](architecture.md) Community 6):

| Spalte | Bedeutung |
|---|---|
| `user_id` | Fremdschlüssel auf `users` |
| `mode` | `hex_to_bin` · `hex_to_dec` · `bin_to_dec` · `unicode` |
| `bit_width` | Bits gemäß OQ-7 (vgl. [`docs/xp-economy.md`](xp-economy.md), Abschnitt 12) |
| `is_correct` | das vom Client behauptete Urteil (ungeprüft — gerade der Punkt) |
| `xp_delta` | das **angewandte**, signierte Post-Clamp-Delta (Abschnitt 1 der Referenz) |
| `timestamp` | Serverzeit der Antwort |

Nutzung: Verteilungsanalyse pro Account (Anteil `is_correct`, XP-pro-Modus, Modus-Mix,
Antworten-Timing), Reklamationen, und als Nachweis-Kette für TD-01, falls dessen Bau
entschieden wird.

**Trade-offs.**

1. **Schreiblast auf dem heißesten Pfad:** ein Insert pro Antwort neben dem ohnehin
   gelockten Update der Economy-Zeile. Bei 120 Antworten / 60 s pro User ist das ein
   bewusster Trade, keine Nebensache — Partitionierung oder Retention-Policy (z. B. 90 Tage,
   dann Aggregate) gehören mitentschieden.
2. **Datenschutz / Retention:** eine Antwort-History ist Nutzungsprofil. Löschpflicht und
   Auskunftspflicht (GDPR) mitdenken — eine künftige Account-Löschung (Roadmap-Punkt)
   muss diese Tabelle miträumen.
3. **Redundanz:** dupliziert, was `user_game_profile` bereits aggregiert. Der Wert liegt in
   der *Verteilung*, nicht im Stand — wer nur den Stand braucht, hat von TD-02 nichts.
4. **Keine Prävention:** TD-02 sieht Farming nur, verhindert es nicht. TD-01 verhindert, sieht
   aber weniger. Die beiden komplementären Maßnahmen sind absichtlich getrennt gehalten.

**Wann das angefasst wird.** Dasselbe Signal wie TD-01 — ohne Produktionsdaten ist auch das
YAGNI. Reihenfolge-Empfehlung: erst TD-02 (Observability, damit man messen kann, ob TD-01 sich
lohnt), dann TD-01 (Prävention), niemals umgekehrt.

---

## TD-03: OpenAPI-Annotations für die Game-Endpoints vervollständigen

**Priorität:** Niedrig (P3) · **Komplexität:** LOW · **Dependencies:** keine
**Quelle:** US-40 (Doku-Durchgang, 2026-10-29) — per Generation gemessen, nicht geraten

**Kontext.** Task 5 von US-40 lautete, die FastAPI-Annotations für `/answer`, `/profile`
und `/leaderboard` zu **prüfen**. Die Prüfung ist durchgeführt; die beiden gefundenen
Lücken sind reine Code-Änderungen und damit außerhalb des US-40-Rahmens
("Keine Code-Änderungen — nur docs/ und README.md"). Hinterlegt statt ignoriert.

**Was bereits vollständig ist (gemessen, 2026-10-29):** `summary` auf allen drei
Endpoints, `response_model` gesetzt, und der wichtigste Vertrag dokumentiert — das
`bit_width`-Feld trägt seine Per-Mode-Whitelist (Bits in jedem Modus, OQ-7) aus
`game_schemas.AnswerRequest` direkt im generierten Schema, ebenso `is_correct`
(strikt Boolean, Trust-Modell T-01) und `mode`. Das `limit`-Query-Param von
`/leaderboard` ist mit `ge`/`le`/`description` abgesichert.

**Lücke 1 — Error-Responses sind nicht deklariert.** Die Decorators sagen nur
`status_code=200`, also zeigt Swagger UI für `/answer` ausschließlich `200` und `422`
und für `/profile` nur `200` — obwohl beide Handler `401`, `403`, `404` und (`/answer`)
`429` werfen. Die Reihenfolge steht im Code-Docstring und im README, aber generierte
Clients und die interaktive Doku sehen sie nicht.

**Akzeptanzkriterien:**
1. `responses={...}` auf `submit_answer`, `get_profile` und `get_leaderboard` mit den
   tatsächlich möglichen Statuscodes, jeweils mit `description` — für `/answer` mindestens
   `401`, `403`, `404`, `422`, `429` (inkl. `Retry-After`-Header in der Doku), für
   `/profile` `401`/`404`, für `/leaderboard` `422`.
2. Ein Contract-Test (Analogie zu `tests/test_game_profile_models.py`) liest
   `app.openapi()` und asserted für jeden der drei Endpoints die Statuscode-Menge gegen
   die im Handler wirklich geworfenen `HTTPException`s — damit die Deklaration nicht
   wieder hinter den Code fällt.
3. README und [`docs/xp-economy.md`](xp-economy.md) behalten ihre Gültigkeit; die
   Doku-Ergänzung in README ("Two gaps are known … filed as TD-03") wird auf *erledigt*
   gestellt.

**Lücke 2 — Feldbeschreibungen fehlen.** In den generierten Schemas ohne `description`
stehen: alle acht Zahlenfelder von `ProfileResponse`, `user_id`/`username`/`total_xp`/
`level`/`correct_count` in `LeaderboardEntryV2`, `correct_count`/`incorrect_count` in
`AnswerResponse`, `UnlockEntry.mode` und `LeaderboardResponseV2.entries`
(gemessen 2026-10-29 über `app.openapi()["components"]["schemas"]`).

**Akzeptanzkriterien:**
1. Jedes Feld der fünf Schemas bekommt ein `Field(description=…)` — die Beschreibung
   nennt Einheit und Herkunft, z. B. `total_xp`: "Lifetime XP, multiple of 4 (D-8);
   floored at `cum(level)` (OQ-4)", und `level`: "Derived from `total_xp` on every
   report path; the stored column is a denormalization nothing reads (Phase 2b H1)".
2. `examples` (oder `json_schema_extra`) für die goldenen Werte aus
   [`docs/xp-economy.md`](xp-economy.md) Abschnitt 10, damit Swagger UI den
   US-33-Fixture zeigt statt leerer Strings.
3. Kein `any`-ähnliches Fallback-Verhalten: der Contract-Test aus Punkt 2 von Lücke 1
   deckt auch ab, dass kein Feld ohne Beschreibung bleibt.

**Warum P3 / Niedrig.** Die HTTP-Semantik ist für Menschen im README und in
`docs/xp-economy.md` vollständig dokumentiert, und kein Client scheitert heute an
fehlenden Annotationen. Der Wert ist generierte-Client-Treue und Self-Service im
Swagger UI — real, aber nicht blockierend.

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

**Bewusst NICHT im Sprint (kein Gold-Plating):** Auth/JWT, Rate-Limiting, neue Live-Feature-Kategorien (Quizzes/Word-Clouds), WebSocket-Hub. Diese gehören in ein anderes Projekt-Backlog, nicht in die Konsolidierung dieses Trainers — getrennte Projekte, getrennte Backlogs.

> **Überholt seit Sprint 2/3/4:** zwei der damals ausgeklammerten Punkte sind längst
> Realität — **Auth/JWT** (Sprint 2, kompletter Auth-Stack inkl. Refresh-Rotation) und
> **Rate-Limiting** (Sprint 3 DB-persistant, seit Sprint 4 auch auf
> `POST /api/game/answer`). Weiterhin bewusst nicht vorhanden: Quizzes/Word-Clouds und
> ein WebSocket-Hub — dieser Trainer ist request/response, der Client hält keine
> Leitung. Neue offene Punkte stehen in **TD-01**/**TD-02** oben.
