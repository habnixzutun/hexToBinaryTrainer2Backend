# Sprint 3: Production Readiness

> **Sprint-Ziel:** Sprint 2 zur Produktionsreife bringen — SMTP-Email-Service aktivieren, Test-Coverage auf 80%+ heben, Migration-Tests auf In-Memory-DB portieren. TDD-getrieben, 80%+ Coverage, Multi-Agent-Review.

**Sprint-Dauer:** 2 Tage (geplant)
**Team:** 5 Agents (Product Owner, Scrum Master, 2x Developer, Code Reviewer)

---

## Team-Rollen

| Rolle | Agent-Typ | Verantwortung |
|-------|-----------|---------------|
| **Product Owner** | `product-owner` | Backlog-Priorisierung, Acceptance Criteria, Sprint-Abnahme |
| **Scrum Master** | `scrum-master` | Sprint-Planning, Daily Standups, Blocker-Entfernung |
| **Backend Developer 1** | `backend-dev` | SMTP-Initialisierung, Coverage-Tests für `user.py` (US-20, US-21) |
| **Backend Developer 2** | `backend-dev` | Migration-Tests auf In-Memory-DB, Coverage-Tests für `auth.py` (US-22, US-21) |
| **Code Reviewer** | `code-reviewer` | PR-Reviews, Security-Checks, Merge-Approval |

---

## Sprint-Gate (Erfolgsdefinition)

1. ✅ Alle 3 User Stories abgeschlossen und gemerged (US-20, US-21, US-22)
2. ✅ `pytest --cov=src --cov-fail-under=80` grün → Gesamt-Coverage ≥ 80%
3. ✅ Alle Einzeldateien ≥ 80% Coverage (kein File unter 80%)
4. ✅ Migration-Tests laufen gegen PostgreSQL (lokal via Docker, in CI via GitHub Actions Postgres-Service)
5. ✅ `SMTPEmailService` wird bei Startup initialisiert (Config + Wiring in `main.py`)
6. ✅ `ruff check` fehlerfrei
7. ✅ Security-Review bestanden (keine CRITICAL/HIGH Issues)

---

## Aktueller Befund (aus `pytest --cov`)

| Datei | Coverage | Fehlende Zeilen | Kritikalität |
|-------|----------|-----------------|--------------|
| `src/routers/user.py` | **52%** | 29, 53, 87-107, 139-173, 185-231, 254-292 | 🔴 Kritisch |
| `src/routers/auth.py` | 75% | 70-72, 127-137, 151-156, 193-235, 273-294, 317, 346, 355 | 🟡 Mittel |
| `src/migrations/v001_auth_schema.py` | < 80% | Migration-Tests brauchen echte DB (Port 5434) | 🟡 Mittel |
| `src/services/user_service.py` | 89% | 39, 69-70 | 🟢 OK |
| `src/services/password_reset_service.py` | 89% | 79, 93, 95, 101, 111, 122-123 | 🟢 OK |
| `src/utils/password.py` | 92% | 30, 34 | 🟢 OK |
| `src/services/auth_service.py` | 94% | 82, 91, 98 | 🟢 OK |

**Gesamt-Coverage:** 77.73% (Ziel: ≥ 80%)
**Migration-Tests:** 11 ERRORs (DB-Connection-Fehler, Port 5434 nicht erreichbar)

---

## User Stories

### US-20: SMTP-Initialisierung + Integration-Test

**Priorität:** P0 | **Komplexität:** LOW | **Dependencies:** keine (unabhängig)
**Assignee:** Backend Developer 1
**Schätzung:** 0.5 Tag

**Story:**
> Als Betreiber möchte ich, dass beim App-Start der `SMTPEmailService` mit den SMTP-Env-Variablen initialisiert wird, damit echte Verifikations-, Reset- und Security-Emails über Strato SMTP verschickt werden.

**Acceptance Criteria:**
1. `src/config.py` wird um SMTP-Settings erweitert:
   - `smtp_host: str = "smtp.strato.de"`
   - `smtp_port: int = 587`
   - `smtp_user: str = ""`
   - `smtp_password: str = ""`
   - `smtp_from_email: str = ""`
   - `base_url: str = "http://localhost:8000"` (für Verifikations-Links)
2. `Settings` validiert beim Init: Wenn `smtp_user` gesetzt ist, müssen `smtp_password` und `smtp_from_email` auch gesetzt sein (sonst `ValueError`)
3. `src/main.py` initialisiert im `lifespan`-Context **vor** dem `yield`:
   - Wenn SMTP-Config vorhanden: `set_email_service(SMTPEmailService(...))`
   - Wenn SMTP-Config fehlt: Fallback auf `MockEmailService` (Development)
   - Log-Meldung: `"SMTP email service initialized"` bzw. `"Using mock email service (development)"`
4. `src/routers/auth.py` und `src/routers/user.py`: Die globale `_default_email_service` Variable bleibt als Fallback, wird aber von `main.py` überschrieben
5. Integration-Test `tests/test_smtp_integration.py`:
   - `test_settings_validates_smtp_config_complete`: Alle SMTP-Felder gesetzt → kein Fehler
   - `test_settings_validates_smtp_config_missing_password`: `smtp_user` gesetzt, `smtp_password` leer → `ValueError`
   - `test_lifespan_initializes_smtp_when_configured`: Mock `SMTPEmailService`, verifiziere `set_email_service` wurde aufgerufen
   - `test_lifespan_falls_back_to_mock_when_no_smtp`: Leere SMTP-Config → `MockEmailService`
   - `test_smtp_service_constructed_with_correct_params`: Verifiziere, dass `SMTPEmailService` mit Settings-Werten erstellt wird
6. **Sicherheits-Check:**
   - SMTP-Credentials werden nicht geloggt
   - `smtp_password` wird in `Settings` als `secret` markiert (kein Leak in Error-Messages)
7. `pytest tests/test_smtp_integration.py` grün, Coverage für neue Code-Pfade ≥ 90%

**TDD-Workflow:**
```python
# Phase 1: Settings (RED → GREEN)
def test_settings_validates_smtp_config_complete():
    settings = Settings(smtp_host="...", smtp_user="...", smtp_password="...", smtp_from_email="...")
    # keine Exception

def test_settings_validates_smtp_config_missing_password():
    with pytest.raises(ValueError, match="smtp_password"):
        Settings(smtp_user="user", smtp_password="")

# Phase 2: Lifespan-Wiring (RED → GREEN)
def test_lifespan_initializes_smtp_when_configured(monkeypatch):
    # Arrange: Settings mit SMTP-Config, SMTPEmailService mocken
    # Act: lifespan context aufrufen
    # Assert: set_email_service wurde mit SMTPEmailService-Instanz aufgerufen

def test_lifespan_falls_back_to_mock_when_no_smtp(monkeypatch):
    # Arrange: Settings ohne SMTP-Config
    # Act: lifespan context aufrufen
    # Assert: set_email_service wurde mit MockEmailService aufgerufen
```

**Definition of Done:**
- [ ] `config.py` um SMTP-Settings erweitert
- [ ] `main.py` initialisiert Email-Service in `lifespan`
- [ ] Alle Tests grün (≥ 90% Coverage für neuen Code)
- [ ] Code Review bestanden (Security-Fokus: Credential-Leaks)
- [ ] PR gemerged

---

### US-21: Test-Coverage auf 80%+ anheben

**Priorität:** P1 | **Komplexität:** HIGH | **Dependencies:** US-20 (braucht SMTP-Initialisierung für vollständige Tests)
**Assignee:** Backend Developer 1 + 2 (parallel, getrennte Dateien)
**Schätzung:** 1 Tag

**Story:**
> Als Entwickler möchte ich fehlende Test-Coverage für `routers/user.py` (52% → 80%) und `routers/auth.py` (75% → 80%) hinzufügen, damit das Sprint-Gate erfüllt ist und die Auth-Endpunkte verlässlich getestet sind.

**Acceptance Criteria:**

#### Teil 1: `routers/user.py` (52% → 80%+)
Fehlende Zeilen abdecken:

**Zeilen 29, 53** (Get-User Endpoints):
- `test_get_me_authenticated`
- `test_get_me_unauthenticated_returns_401`
- `test_get_me_returns_user_with_stats`

**Zeilen 87-107** (`PATCH /api/user/email`):
- `test_change_email_authenticated_success`
- `test_change_email_invalid_format_returns_422`
- `test_change_email_duplicate_returns_409`
- `test_change_email_same_as_current_returns_409`
- `test_change_email_unauthenticated_returns_401`

**Zeilen 139-173** (`POST /api/auth/verify-email-change`):
- `test_verify_email_change_success`
- `test_verify_email_change_invalid_token_returns_400`
- `test_verify_email_change_expired_token_returns_400`
- `test_verify_email_change_already_used_token_returns_400`
- `test_verify_email_change_updates_email_in_db`
- `test_verify_email_change_updates_identity_provider_user_id`

**Zeilen 185-231** (`PATCH /api/user/username`):
- `test_change_username_authenticated_success`
- `test_change_username_invalid_length_returns_422`
- `test_change_username_invalid_chars_returns_422`
- `test_change_username_duplicate_returns_409`
- `test_change_username_within_7_days_returns_429`
- `test_change_username_after_7_days_succeeds`
- `test_change_username_unauthenticated_returns_401`
- `test_change_username_response_contains_next_change_at_on_429`

**Zeilen 254-292** (`POST /api/user/change-password`):
- `test_change_password_success`
- `test_change_password_wrong_old_password_returns_401`
- `test_change_password_weak_new_password_returns_400`
- `test_change_password_same_as_old_returns_400`
- `test_change_password_invalidates_all_refresh_tokens`
- `test_change_password_unauthenticated_returns_401`

#### Teil 2: `routers/auth.py` (75% → 80%+)
Fehlende Zeilen abdecken:

**Zeilen 70-72** (Rate Limit Response):
- `test_register_rate_limit_returns_429_with_retry_after`

**Zeilen 127-137** (Login Edge Cases):
- `test_login_unverified_account_returns_403`
- `test_login_wrong_password_returns_401`

**Zeilen 151-156** (Refresh Edge Cases):
- `test_refresh_with_revoked_token_returns_401`

**Zeilen 193-235** (Registration Happy Path + Verifikations-Email):
- `test_register_sends_verification_email`
- `test_register_duplicate_email_returns_409`

**Zeilen 273-294** (Verify Email Edge Cases):
- `test_verify_email_already_verified_returns_message`
- `test_verify_email_invalid_type_returns_400`

**Zeilen 317, 346, 355** (Logout Edge Cases):
- `test_logout_with_unknown_token_returns_200_idempotent`

#### Ziel-Coverage nach US-21:
- `routers/user.py`: ≥ 85% (von 52%)
- `routers/auth.py`: ≥ 85% (von 75%)
- Gesamt: ≥ 80%

**TDD-Workflow:**
```python
# RED: Test schreiben (z.B. für change_email)
def test_change_email_authenticated_success(async_client, db_session):
    # Arrange: registrierter, verifizierter User, access_token
    # Act: response = client.patch("/api/user/email", json={"new_email": "new@example.com"}, headers={auth_header})
    # Assert: status 200
    # Assert: verification email in mock_email_service.sent
    # Assert: change token in password_reset_tokens

def test_change_email_duplicate_returns_409(async_client, db_session):
    # Arrange: User A registriert mit email_a@example.com
    #          User B registriert, eingeloggt
    # Act: User B change email zu email_a@example.com
    # Assert: status 409

# GREEN: Falls Tests scheitern wegen Bug im Endpoint → Bug fixen
# REFACTOR: Shared Fixtures in conftest.py auslagern
```

**Definition of Done:**
- [ ] Alle fehlenden Zeilen in `routers/user.py` getestet
- [ ] Alle fehlenden Zeilen in `routers/auth.py` getestet
- [ ] `pytest --cov=src --cov-fail-under=80` grün
- [ ] Kein Einzel-File unter 80%
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-22: Migration-Tests reparieren (Postgres-DB)

**Priorität:** P2 | **Komplexität:** MEDIUM | **Dependencies:** keine (unabhängig)
**Assignee:** Backend Developer 2
**Schätzung:** 0.5 Tag

**Story:**
> Als Entwickler möchte ich, dass die Migration-Tests gegen die PostgreSQL-Datenbank laufen, damit sie das tatsächliche Produktionsverhalten validieren. Aktuell schlagen alle 11 Tests fehl, weil der Test-Port (5434) nicht mit der laufenden Postgres-Instanz (Port 5432) übereinstimmt.

**Befund:**
- Tests versuchen: `Connect call failed ('::1', 5434)`
- Docker-Compose läuft auf: Port 5432
- Test-Config nutzt: Port 5434 (wahrscheinlich falsch in `conftest.py` oder `test_db_config`)

**Acceptance Criteria:**
1. Test-DB-Config nutzt korrekt die Postgres-Connection:
   - Lokal: Port 5432 (Docker-Compose) oder aus `.env.test`
   - CI: GitHub Actions Postgres-Service auf `localhost:5432`
2. Test-Datenbank wird pro Test-Session isoliert (eigene DB oder Schema-Cleanup)
3. Alle 11 Tests grün:
   - `test_migration_creates_users_table`
   - `test_migration_creates_user_identities_table`
   - `test_migration_creates_user_refresh_tokens_table`
   - `test_migration_creates_password_reset_tokens_table`
   - `test_migration_creates_user_audit_log_table`
   - `test_migration_drops_old_users_table`
   - `test_migration_runs_exactly_once`
   - `test_migration_creates_indexes`
   - `test_migration_creates_foreign_keys`
   - `test_migration_creates_unique_constraints`
   - `test_migration_cascade_delete`
4. `conftest.py` stellt Test-DB-Fixture bereit:
   - Nutzt PostgreSQL via Docker (lokal) oder GitHub Actions Service (CI)
   - Cleanup nach Tests (DROP TABLE oder separate Test-DB)
5. CI-Konfiguration `.github/workflows/deploy.yml`:
   - Postgres-Service im Test-Job:
   ```yaml
   services:
     postgres:
       image: postgres:15-alpine
       env:
         POSTGRES_USER: test
         POSTGRES_PASSWORD: test
         POSTGRES_DB: test_db
       ports:
         - 5432:5432
       options: >-
         --health-cmd pg_isready
         --health-interval 10s
         --health-timeout 5s
         --health-retries 5
   ```
6. `pytest tests/test_migrations.py` grün, Coverage für Migration-Code ≥ 80%

**TDD-Workflow:**
```python
# RED: Aktueller Test schlägt fehl (Connect call failed auf Port 5434)
# → Test-Fixture reparieren:
@pytest.fixture
async def db_session():
    engine = create_async_engine(settings.async_database_url)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()

# GREEN: Migration-Logik validieren gegen echte Postgres
# REFACTOR: Shared Postgres-Fixtures in conftest.py auslagern
```

**Definition of Done:**
- [ ] Test-Config nutzt korrekte Postgres-Connection (Port 5432 oder `.env.test`)
- [ ] Alle 11 Tests grün
- [ ] Migration-Code ≥ 80% Coverage
- [ ] CI-Konfiguration: GitHub Actions Postgres-Service hinzugefügt
- [ ] Code Review bestanden
- [ ] PR gemerged

---

## Sprint-Ablauf

### Tag 1: Fundament (parallel)

**Vormittag:**
- **Dev 1:** US-20 (SMTP-Initialisierung) — Tests schreiben, dann implementieren
- **Dev 2:** US-22 (Migration-Tests) — In-Memory-DB-Fixture schreiben, dann Migration-Code anpassen

**Nachmittag:**
- **Dev 1:** US-20 PR erstellen
- **Dev 2:** US-22 PR erstellen
- **Code Reviewer:** Beide PRs parallel reviewen
- **Dev 1 + 2:** Feedback einarbeiten, PRs mergen

**Abend:**
- Daily Standup: Fortschritt besprechen
- **US-20 und US-22 müssen am Ende von Tag 1 gemerged sein** (Sprint-Gate)

---

### Tag 2: Coverage-Push (parallel)

**Vormittag:**
- **Dev 1:** US-21 Teil 1 (routers/user.py 52% → 80%)
  - Tests für Email/Username/Password Change schreiben
  - Parallel: Dev 2 beginnt mit routers/auth.py
- **Dev 2:** US-21 Teil 2 (routers/auth.py 75% → 80%)
  - Tests für Login/Register/Refresh/Logout-Edge-Cases

**Nachmittag:**
- Coverage-Check: `pytest --cov=src --cov-report=term-missing`
- Falls < 80%: fehlende Zeilen gezielt nachtesten
- US-21 PR erstellen
- **Code Reviewer:** PR reviewen
- **Dev 1 + 2:** Feedback einarbeiten, PR mergen

**Abend:**
- **Sprint-Review:** Product Owner prüft Sprint-Gate
- **Sprint-Retro:** Team bespricht Verbesserungen

---

## Agent-Workflows

### Backend Developer Workflow

```bash
# 1. Branch erstellen (von main)
git checkout main && git pull
git checkout -b feature/us-XX-kurzbeschreibung

# 2. Tests schreiben (RED)
uv run pytest tests/test_neu.py -v

# 3. Implementieren (GREEN)
# Code schreiben, bis Tests grün

# 4. Coverage prüfen
uv run pytest --cov=src --cov-report=term-missing

# 5. Linting
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# 6. Commit
git add .
git commit -m "feat: implementiere US-XX — Kurzbeschreibung"

# 7. PR erstellen
gh pr create --base main --head feature/us-XX-kurzbeschreibung

# 8. Code Review abwarten
# Feedback einarbeiten, pushen

# 9. PR mergen (nach Approval)
gh pr merge <nr> --merge
```

### Code Reviewer Workflow

```bash
# 1. PR reviewen
gh pr view <nr>
gh pr diff <nr>

# 2. Lokal auschecken
gh pr checkout <nr>

# 3. Tests + Coverage lokal ausführen
uv run pytest --cov=src --cov-fail-under=80
uv run ruff check src/ tests/

# 4. Security-Check (für US-20)
# - SMTP-Credentials werden nicht geloggt
# - Kein Leak in Error-Messages
# - Settings-Validierung prüft alle required fields

# 5. Coverage-Check (für US-21)
# - Kein File unter 80%?
# - Edge-Cases getestet?
# - Happy Path + Error Path abgedeckt?

# 6. Feedback geben
gh pr review <nr> --request-changes --body "Feedback: ..."

# 7. Approval nach Fix
gh pr review <nr> --approve
```

---

## Risk Management

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|--------|-------------------|--------|-----------|
| Coverage-Ziel 80% nicht erreichbar | Niedrig | Hoch | Frühzeitig Coverage-Check (Tag 2 Vormittag); bei < 75% Scrum Master alarmieren |
| SMTP-Credentials leak | Niedrig | Kritisch | Code Review mit Security-Fokus; `Settings` nutzt `secret`-Markierung |
| Flaky tests in US-21 | Mittel | Mittel | Tests isoliert, keine externen Dependencies; deterministische Fixtures |

---

## Offene Fragen (vor Sprint-Start geklärt)

1. ✅ **SMTP-Credentials vorhanden?** `.env` hat `SMTP_USER=erik@albecker.eu`, `SMTP_PASSWORD=...` → ja
2. ✅ **Strato SMTP erreichbar?** Port 587 (TLS) → sollte klappen, ggf. Firewall prüfen
3. ✅ **Base URL für Verifikations-Links?** → **Hybrid: Env-Variable `BASE_URL`**
   - Default: `http://localhost:8000` (Development)
   - Production: `BASE_URL=https://deine-domain.de` in `.env`
   - Konsequent in allen Email-Services verwenden
4. ✅ **CI-Strategie für PostgreSQL-Tests?** → **GitHub Actions Postgres-Service**
   - Native Integration: `services: postgres` in `.github/workflows/deploy.yml`
   - Läuft auf `localhost:5432`
   - Einfach zu konfigurieren, schnell, keine zusätzliche Library
   - Lokal: Docker-Compose (Port 5432)

---

## Definition of Done (Sprint-Level)

**Alle User Stories müssen:**
- ✅ Implementiert und gemerged
- ✅ Tests grün (≥ 80% Coverage)
- ✅ Code Review bestanden (keine CRITICAL/HIGH Issues)
- ✅ `ruff check` fehlerfrei
- ✅ Security-Review bestanden (für US-20)

**Sprint-Gate (Product Owner-Abnahme):**
- ✅ Alle 3 User Stories abgeschlossen
- ✅ Gesamt-Coverage ≥ 80%
- ✅ Kein Einzel-File unter 80%
- ✅ Migration-Tests laufen ohne externe DB
- ✅ SMTP-Service in Produktion aktiv
- ✅ Security-Review bestanden

---

## Nächste Schritte nach Sprint 3

**Sprint 4 (geplant, nach Sprint 3):**
- OAuth-Provider-Integration (GitHub, Google)
- Telefon-Auth (SMS-Code)
- Account-Deletion (GDPR)
- Multi-Device-Management
- HTML-Email-Templates (statt plain text)

**Hinweis:** Username-Änderung (1x pro 7 Tage) ist bereits in Sprint 2 implementiert (`routers/user.py:192-216`).

---

## Quellen

- Sprint 2 Backlog: `docs/sprint2-plan.md`
- Aktueller Coverage-Report: `pytest --cov=src`
- Architektur: `docs/architecture.md`
