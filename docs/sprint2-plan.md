# Sprint 2: Authentication System

> **Sprint-Ziel:** Passwort-basierte Authentifizierung mit Email-Verifizierung, JWT Access/Refresh Tokens, Multi-Provider-Vorbereitung und Rate Limiting. Security-first, TDD-getrieben, 80%+ Coverage.

**Sprint-Dauer:** 5 Tage (geplant)
**Team:** 6 Agents (Product Owner, Scrum Master, 3x Developer, Code Reviewer)

**Update:** 10 User Stories (US-09 bis US-19) — Password Change als eigene US-19 hinzugefügt, Audit-Log-Infrastruktur integriert.

**Status Update (2026-10-19):**
- ✅ **SPRINT 2 ABGESCHLOSSEN** - Alle 11 User Stories erfolgreich implementiert!
- ✅ US-09 bis US-19: Alle abgeschlossen und gemerged
- ✅ US-17: Integration Tests (PR #23)
- ✅ US-18: CI/CD + Deployment Updates (PR #24)
- ✅ US-19: Password Change
- **Test-Status:** 121 Tests passing ✅
- **Coverage:** 80%+ enforced ✅
- **Production-Ready:** ✅

---

## Team-Rollen

| Rolle | Agent-Typ | Verantwortung |
|-------|-----------|---------------|
| **Product Owner** | `product-owner` | Backlog-Priorisierung, Acceptance Criteria, Sprint-Abnahme |
| **Scrum Master** | `scrum-master` | Sprint-Planning, Daily Standups, Blocker-Entfernung |
| **Backend Developer 1** | `backend-dev` | DB-Migration, Auth-Models, Utilities (US-09, US-11, US-12) |
| **Backend Developer 2** | `backend-dev` | Auth-Endpoints: Register, Login, Refresh, Logout (US-13, US-14) |
| **Backend Developer 3** | `backend-dev` | User-Management: Password-Reset, Email/Username-Änderung (US-15, US-16) |
| **Code Reviewer** | `code-reviewer` | PR-Reviews, Security-Checks, Merge-Approval |

---

## Sprint-Gate (Erfolgsdefinition)

**MVP-Release-Kriterien:**
1. ✅ Alle 10 User Stories abgeschlossen und gemerged (US-09 bis US-19)
2. ✅ `pytest --cov=src --cov-fail-under=80` grün
3. ✅ Alle Auth-Flows funktionieren: Register → Verify → Login → Refresh → Logout
4. ✅ Password-Reset-Flow funktioniert: Request → Token → Reset
5. ✅ Password-Change-Flow funktioniert: Login → Change Password → Logout → Login mit neuem Passwort
6. ✅ Email-Verifizierung bei Registrierung und Email-Änderung
7. ✅ Multi-Provider-Schema vorbereitet (user_identities)
8. ✅ Rate Limiting aktiv: 5 Login-Versuche/Minute, 3 Registrierungen/Stunde
9. ✅ Username-Änderung limitiert auf 1x pro 7 Tage
10. ✅ Alle Sessions werden bei Password-Änderung/Reset invalidiert
11. ✅ Audit-Log aktiv für alle kritischen Aktionen (password_change, password_reset, email_change, username_change)
12. ✅ Security-Benachrichtigungen per Email bei kritischen Aktionen
13. ✅ `ruff check` fehlerfrei
14. ✅ Security-Review bestanden (keine CRITICAL/HIGH Issues)

---

## User Stories

### US-09: Database Schema Migration (Clean Break)

**Priorität:** P0 | **Komplexität:** MEDIUM | **Dependencies:** keine
**Assignee:** Backend Developer 1

**Story:**
> Als Entwickler möchte ich das alte User-Schema durch das neue Auth-Schema ersetzen, damit die Infrastruktur für Email-Login, Multi-Provider und Refresh Tokens bereitsteht.

**Acceptance Criteria:**
1. Alte `users`-Tabelle wird gedroppt (Clean Break, keine Daten-Migration)
2. Neue Tabellen erstellt: `users`, `user_identities`, `user_refresh_tokens`, `password_reset_tokens`, `user_audit_log`
3. UUID7 als Primary Key für alle Tabellen
4. Foreign Keys mit `ON DELETE CASCADE` konfiguriert
5. Indexes für Performance: `users(username)`, `users(email)`, `user_identities(provider, provider_user_id)`, `user_refresh_tokens(token_hash)`, `password_reset_tokens(token_hash)`, `user_audit_log(user_id, timestamp)`
6. `users`-Tabelle enthält `last_username_change_at TIMESTAMP WITH TIME ZONE` (für 7-Tage-Limitierung)
7. `user_audit_log`-Tabelle: `id UUID7`, `user_id UUID7 FK`, `action VARCHAR(50)`, `timestamp TIMESTAMP WITH TIME ZONE`, `ip_address VARCHAR(45)`, `user_agent TEXT`, `metadata JSONB` (für zusätzliche Details)
8. Migration-Script `src/migrations/001_auth_schema.py` existiert und ist idempotent
9. Integration-Test: Migration läuft erfolgreich auf leerer DB und auf DB mit alten Daten
10. `pytest tests/test_migrations.py` grün, Coverage ≥ 80%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_migration_creates_users_table():
    # Arrange: leere DB
    # Act: Migration ausführen
    # Assert: users-Tabelle existiert mit allen Spalten

def test_migration_drops_old_users_table():
    # Arrange: alte users-Tabelle mit Daten
    # Act: Migration ausführen
    # Assert: alte Tabelle existiert nicht mehr, neue Tabelle leer

def test_migration_is_idempotent():
    # Arrange: Migration bereits einmal ausgeführt
    # Act: Migration erneut ausführen
    # Assert: keine Fehler, Schema unverändert
```

**Definition of Done:**
- [ ] Migration-Script implementiert
- [ ] Alle Tests grün (≥ 80% Coverage)
- [ ] Code Review bestanden (Code Reviewer)
- [ ] PR gemerged in `main`

---

### US-10: Email Service Interface

**Priorität:** P0 | **Komplexität:** LOW | **Dependencies:** keine
**Assignee:** Backend Developer 1

**Story:**
> Als Entwickler möchte ich ein abstraktes Email-Service-Interface mit SMTP-Implementierung und einen Audit-Service für Security-Benachrichtigungen, damit Emails direkt vom Strato-Server verschickt werden können und kritische Account-Aktionen geloggt und den Usern mitgeteilt werden.

**Acceptance Criteria:**
1. `src/services/email_service.py` definiert `EmailService` Protocol (ABC)
2. Methode: `async send_email(to: str, subject: str, body: str) -> bool`
3. Mock-Implementierung `MockEmailService` für Tests (loggt Emails, speichert sie in `sent_emails: list`)
4. Concrete Implementierung `SMTPEmailService` für Produktion (Strato SMTP)
5. SMTP-Konfiguration via Env-Variablen: `SMTP_HOST=smtp.strato.de`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`
6. Factory-Funktion `get_email_service() -> EmailService` mit Dependency-Injection (FastAPI `Depends`)
7. `src/services/audit_service.py` mit Funktionen:
   - `log_audit_action(user_id, action, ip_address, user_agent, metadata=None)` — schreibt in `user_audit_log`
   - `send_security_notification(email, action, timestamp, ip_address)` — sendet Email: "Your [action] was performed on [timestamp] from IP [ip]"
   - Aktionen: `password_change`, `password_reset`, `email_change`, `username_change`
8. Unit-Tests:
   - `test_mock_email_service_stores_emails`
   - `test_smtp_email_service_sends_email`
   - `test_log_audit_action_inserts_row`
   - `test_send_security_notification_sends_email`
9. `pytest tests/test_email_service.py` und `pytest tests/test_audit_service.py` grün, Coverage ≥ 80%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_mock_email_service_stores_emails():
    # Arrange: MockEmailService erstellen
    # Act: send_email("test@example.com", "Subject", "Body")
    # Assert: email in sent_emails-Liste

def test_email_service_returns_true_on_success():
    # Arrange: MockEmailService
    # Act: result = send_email(...)
    # Assert: result is True
```

**Definition of Done:**
- [ ] EmailService Protocol definiert
- [ ] MockEmailService implementiert
- [ ] Factory-Funktion implementiert
- [ ] Alle Tests grün (≥ 80% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-11: Password Hashing Utility

**Priorität:** P1 | **Komplexität:** LOW | **Dependencies:** US-09
**Assignee:** Backend Developer 1

**Story:**
> Als Entwickler möchte ich eine Password-Hashing-Utility mit bcrypt, damit Passwörter sicher gehasht und validiert werden können.

**Acceptance Criteria:**
1. `src/utils/password_hashing.py` mit Funktionen:
   - `hash_password(password: str) -> str` (bcrypt cost 12)
   - `verify_password(password: str, hashed: str) -> bool`
   - `validate_password_strength(password: str) -> bool` (prüft Komplexität)
2. Cost-Faktor 12 hardcoded (nicht konfigurierbar für MVP)
3. Input-Validierung: Passwort muss ≥ 8 Zeichen, ≤ 128 Zeichen, mindestens 1 Großbuchstabe, mindestens 1 Zahl
4. Password-Strength-Regex: `^(?=.*[A-Z])(?=.*\d).{8,128}$`
5. Edge-Cases behandelt: leeres Passwort → `ValueError`, Passwort > 128 Zeichen → `ValueError`, zu schwaches Passwort → `ValueError`
6. Unit-Tests:
   - `test_hash_password_returns_hash` (Hash beginnt mit `$2b$12$`)
   - `test_verify_password_correct` (richtiges Passwort → True)
   - `test_verify_password_incorrect` (falsches Passwort → False)
   - `test_hash_password_too_short_raises` (< 8 Zeichen)
   - `test_hash_password_too_long_raises` (> 128 Zeichen)
   - `test_hash_password_no_uppercase_raises` (keine Großbuchstaben)
   - `test_hash_password_no_digit_raises` (keine Zahlen)
   - `test_hash_password_deterministic` (gleiches Passwort → unterschiedliche Hashes wegen Salt)
   - `test_validate_password_strength_valid` ("Password123" → True)
   - `test_validate_password_strength_weak` ("password" → False)
7. `pytest tests/test_password_hashing.py` grün, Coverage 100%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_hash_password_returns_hash():
    # Arrange: password = "securepassword123"
    # Act: hashed = hash_password(password)
    # Assert: hashed.startswith("$2b$12$")

def test_verify_password_correct():
    # Arrange: password = "securepassword123", hashed = hash_password(password)
    # Act: result = verify_password(password, hashed)
    # Assert: result is True

def test_hash_password_too_short_raises():
    # Arrange: password = "short"
    # Act & Assert: pytest.raises(ValueError)
```

**Definition of Done:**
- [ ] Password-Hashing-Utility implementiert
- [ ] Alle Tests grün (100% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-12: JWT Token Utilities

**Priorität:** P1 | **Komplexität:** LOW | **Dependencies:** US-09
**Assignee:** Backend Developer 1

**Story:**
> Als Entwickler möchte ich JWT-Token-Utilities für Access und Refresh Tokens, damit die Auth-Endpoints signierte Tokens generieren und validieren können.

**Acceptance Criteria:**
1. `src/utils/token_utils.py` mit Funktionen:
   - `create_access_token(user_id: UUID7, expires_delta: timedelta = 15 minutes) -> str`
   - `create_refresh_token(user_id: UUID7, expires_delta: timedelta = 7 days) -> str`
   - `decode_token(token: str) -> dict` (wirft `InvalidTokenError` bei ungültigem Token)
2. JWT Secret aus `settings.jwt_secret` (Env-Variable `JWT_SECRET`)
3. Algorithmus: `HS256`
4. Token-Payload: `{"sub": user_id, "exp": expires_at, "iat": issued_at, "jti": token_id (UUID7)}`
5. Unit-Tests:
   - `test_create_access_token_valid` (Token ist valid, enthält user_id)
   - `test_create_access_token_expires` (Token nach 15 Min. abgelaufen)
   - `test_decode_token_valid` (gültiger Token → Payload)
   - `test_decode_token_expired` (abgelaufener Token → `InvalidTokenError`)
   - `test_decode_token_invalid_signature` (manipulierter Token → `InvalidTokenError`)
   - `test_create_refresh_token_valid` (Token ist valid, enthält user_id)
6. `pytest tests/test_token_utils.py` grün, Coverage ≥ 90%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_create_access_token_valid():
    # Arrange: user_id = UUID7()
    # Act: token = create_access_token(user_id)
    # Assert: decode_token(token)["sub"] == user_id

def test_decode_token_expired():
    # Arrange: token = create_access_token(user_id, expires_delta=timedelta(seconds=-1))
    # Act & Assert: pytest.raises(InvalidTokenError)
```

**Definition of Done:**
- [ ] Token-Utilities implementiert
- [ ] Alle Tests grün (≥ 90% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-13: Registration + Email Verification

**Priorität:** P1 | **Komplexität:** HIGH | **Dependencies:** US-09, US-10, US-11, US-12
**Assignee:** Backend Developer 2

**Story:**
> Als User möchte ich mich mit Email + Passwort registrieren und eine Verifizierungs-Email erhalten, damit ich meinen Account aktivieren kann.

**Acceptance Criteria:**
1. `POST /api/auth/register` Endpoint:
   - Request: `{"email": "user@example.com", "username": "max", "password": "secure123"}`
   - Response: `{"message": "Registration successful. Please verify your email.", "user_id": UUID7}`
   - Status: 201 Created
2. Validierung:
   - Email: valides Format, unique in `users.email`
   - Username: 3-50 Zeichen, alphanumerisch + `_`, unique in `users.username`
   - Passwort: 8-128 Zeichen (siehe US-11)
3. Registrierung-Flow:
   - Erstelle `users`-Eintrag (id, email, username, created_at)
   - Erstelle `user_identities`-Eintrag (provider='email', provider_user_id=email, password_hash, verified_at=NULL)
   - Generiere Verifizierungs-Token (JWT, 24h gültig, `type='email_verification'`)
   - Sende Verifizierungs-Email via `EmailService` (Body: Link mit Token)
4. `POST /api/auth/verify-email` Endpoint:
   - Request: `{"token": "jwt-token"}`
   - Response: `{"message": "Email verified successfully"}`
   - Status: 200 OK
   - Setze `user_identities.verified_at = NOW()`
5. **Rate Limiting:** 3 Registrierungen pro Stunde pro IP-Adresse
   - Response bei Limit: `{"detail": "Too many registration attempts. Try again later."}`
   - Status: 429 Too Many Requests
   - Header: `Retry-After: 3600`
6. Edge-Cases:
   - Email bereits registriert → 409 Conflict
   - Username bereits vergeben → 409 Conflict
   - Ungültiger Verifizierungs-Token → 400 Bad Request
   - Abgelaufener Verifizierungs-Token → 400 Bad Request
   - Rate Limit überschritten → 429 Too Many Requests
7. Integration-Test:
   - `test_register_creates_user_and_sends_email`
   - `test_verify_email_activates_account`
   - `test_register_duplicate_email_returns_409`
   - `test_register_duplicate_username_returns_409`
   - `test_verify_email_invalid_token_returns_400`
   - `test_register_rate_limit_returns_429`
8. `pytest tests/test_auth_register.py` grün, Coverage ≥ 85%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_register_creates_user_and_sends_email():
    # Arrange: client, mock_email_service
    # Act: response = client.post("/api/auth/register", json={...})
    # Assert: response.status_code == 201
    # Assert: user in DB
    # Assert: identity in DB (verified_at == None)
    # Assert: email in mock_email_service.sent_emails

def test_verify_email_activates_account():
    # Arrange: user registriert, token aus email extrahiert
    # Act: response = client.post("/api/auth/verify-email", json={"token": token})
    # Assert: response.status_code == 200
    # Assert: identity.verified_at is not None
```

**Definition of Done:**
- [ ] Register-Endpoint implementiert
- [ ] Verify-Email-Endpoint implementiert
- [ ] Alle Tests grün (≥ 85% Coverage)
- [ ] Code Review bestanden (Security-Fokus: Input-Validierung, Token-Generierung)
- [ ] PR gemerged

---

### US-14: Login + Refresh + Logout

**Priorität:** P1 | **Komplexität:** HIGH | **Dependencies:** US-09, US-11, US-12
**Assignee:** Backend Developer 2

**Story:**
> Als User möchte ich mich mit Email + Passwort einloggen, einen Access Token erhalten, und bei Ablauf automatisch einen neuen Token via Refresh Token bekommen, damit ich nicht ständig neu login machen muss.

**Acceptance Criteria:**
1. `POST /api/auth/login` Endpoint:
   - Request: `{"email": "user@example.com", "password": "secure123"}`
   - Response: `{"access_token": "jwt", "token_type": "bearer"}`
   - Set-Cookie: `refresh_token=opaque-string; HttpOnly; Secure; SameSite=Strict; Path=/api/auth/refresh`
   - Status: 200 OK
2. Login-Flow:
   - Validiere Email + Passwort via `verify_password`
   - Prüfe `user_identities.verified_at IS NOT NULL` (Account aktiv)
   - Generiere Access Token (15 Min.)
   - Generiere Refresh Token (opaque random string, 64 Bytes)
   - Speichere Refresh Token in `user_refresh_tokens` (token_hash, expires_at=7 days)
3. `POST /api/auth/refresh` Endpoint:
   - Request: Cookie `refresh_token=opaque-string`
   - Response: `{"access_token": "new-jwt"}`
   - Status: 200 OK
   - Prüfe Token in DB, gültig, nicht revoked
   - Generiere neuen Access Token
4. `POST /api/auth/logout` Endpoint:
   - Request: Cookie `refresh_token=opaque-string`
   - Response: `{"message": "Logged out successfully"}`
   - Status: 200 OK
   - Setze `user_refresh_tokens.revoked_at = NOW()`
   - Lösche Cookie (Set-Cookie mit `Max-Age=0`)
5. **Rate Limiting:** 5 Login-Versuche pro Minute pro Email-Adresse
   - Response bei Limit: `{"detail": "Too many login attempts. Try again later."}`
   - Status: 429 Too Many Requests
   - Header: `Retry-After: 60`
   - Zähler wird bei erfolgreichem Login zurückgesetzt
6. Edge-Cases:
   - Falsches Passwort → 401 Unauthorized
   - Account nicht verifiziert → 403 Forbidden
   - Ungültiger Refresh Token → 401 Unauthorized
   - Revoked Refresh Token → 401 Unauthorized
   - Rate Limit überschritten → 429 Too Many Requests
7. Integration-Test:
   - `test_login_returns_access_token_and_sets_cookie`
   - `test_login_wrong_password_returns_401`
   - `test_login_unverified_account_returns_403`
   - `test_login_rate_limit_returns_429`
   - `test_refresh_returns_new_access_token`
   - `test_refresh_invalid_token_returns_401`
   - `test_logout_revokes_refresh_token`
8. `pytest tests/test_auth_session.py` grün, Coverage ≥ 85%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_login_returns_access_token_and_sets_cookie():
    # Arrange: user registriert und verifiziert
    # Act: response = client.post("/api/auth/login", json={...})
    # Assert: response.status_code == 200
    # Assert: response.json()["access_token"] is valid JWT
    # Assert: response.cookies["refresh_token"] is set

def test_logout_revokes_refresh_token():
    # Arrange: user eingeloggt, refresh_token aus cookie
    # Act: response = client.post("/api/auth/logout", cookies={...})
    # Assert: response.status_code == 200
    # Assert: token in DB has revoked_at set
```

**Definition of Done:**
- [ ] Login-Endpoint implementiert
- [ ] Refresh-Endpoint implementiert
- [ ] Logout-Endpoint implementiert
- [ ] Alle Tests grün (≥ 85% Coverage)
- [ ] Code Review bestanden (Security-Fokus: Token-Handling, Cookie-Flags)
- [ ] PR gemerged

---

### US-15: Password Reset Flow

**Priorität:** P2 | **Komplexität:** MEDIUM | **Dependencies:** US-09, US-10, US-11
**Assignee:** Backend Developer 3

**Story:**
> Als User möchte ich mein Passwort zurücksetzen können, wenn ich es vergessen habe, damit ich wieder Zugriff auf meinen Account bekomme. Alle Sessions werden invalidiert und eine Security-Benachrichtigung gesendet.

**Acceptance Criteria:**
1. `POST /api/auth/request-password-reset` Endpoint:
   - Request: `{"email": "user@example.com"}`
   - Response: `{"message": "If the email exists, a reset link has been sent."}`
   - Status: 200 OK (Security: keine Information über Existenz der Email)
2. Request-Flow:
   - Suche User via Email
   - Generiere Reset-Token (opaque random string, 64 Bytes)
   - Speichere Token in `password_reset_tokens` (token_hash, expires_at=1h, used_at=NULL)
   - Sende Reset-Email via `EmailService` (Body: Link mit Token)
3. `POST /api/auth/reset-password` Endpoint:
   - Request: `{"token": "reset-token", "new_password": "newsecure123"}`
   - Response: `{"message": "Password reset successfully. All sessions have been invalidated."}`
   - Status: 200 OK
   - Prüfe Token in DB, gültig, nicht verwendet, nicht abgelaufen
   - Update `user_identities.password_hash` mit neuem Hash
   - Setze `password_reset_tokens.used_at = NOW()`
   - **Invalide alle Refresh Tokens:** `UPDATE user_refresh_tokens SET revoked_at = NOW() WHERE user_id = ?`
   - **Audit-Log:** `log_audit_action(user_id, "password_reset", ip_address, user_agent)`
   - **Security-Benachrichtigung:** `send_security_notification(email, "password reset", timestamp, ip_address)`
4. Edge-Cases:
   - Email existiert nicht → 200 OK (keine Information)
   - Ungültiger Reset-Token → 400 Bad Request
   - Abgelaufener Reset-Token → 400 Bad Request
   - Bereits verwendeter Reset-Token → 400 Bad Request
   - Neues Passwort zu schwach → 400 Bad Request
5. Integration-Test:
   - `test_request_password_reset_sends_email`
   - `test_reset_password_updates_hash`
   - `test_reset_password_invalidates_all_sessions`
   - `test_reset_password_logs_audit`
   - `test_reset_password_sends_security_notification`
   - `test_reset_password_invalid_token_returns_400`
   - `test_reset_password_expired_token_returns_400`
   - `test_reset_password_used_token_returns_400`
6. `pytest tests/test_auth_password_reset.py` grün, Coverage ≥ 85%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_request_password_reset_sends_email():
    # Arrange: user registriert und verifiziert
    # Act: response = client.post("/api/auth/request-password-reset", json={"email": ...})
    # Assert: response.status_code == 200
    # Assert: reset_token in DB
    # Assert: email in mock_email_service.sent_emails

def test_reset_password_updates_hash():
    # Arrange: user registriert, reset-token generiert
    # Act: response = client.post("/api/auth/reset-password", json={"token": ..., "new_password": ...})
    # Assert: response.status_code == 200
    # Assert: password_hash in DB updated
    # Assert: reset_token.used_at is not None
```

**Definition of Done:**
- [ ] Request-Password-Reset-Endpoint implementiert
- [ ] Reset-Password-Endpoint implementiert
- [ ] Alle Tests grün (≥ 85% Coverage)
- [ ] Code Review bestanden (Security-Fokus: Token-Handling, keine Information über Email-Existenz)
- [ ] PR gemerged

---

### US-16: Email + Username Change

**Priorität:** P2 | **Komplexität:** MEDIUM | **Dependencies:** US-09, US-10, US-13
**Assignee:** Backend Developer 3

**Story:**
> Als User möchte ich meine Email-Adresse und meinen Username ändern können, damit meine Profildaten aktuell bleiben. Username-Änderung ist auf 1x pro 7 Tage limitiert. Kritische Aktionen werden geloggt und per Email benachrichtigt.

**Acceptance Criteria:**
1. `PATCH /api/user/email` Endpoint:
   - Request: `{"new_email": "newemail@example.com"}`
   - Response: `{"message": "Verification email sent to new address"}`
   - Status: 200 OK
   - Erfordert Auth (Access Token in Header)
2. Email-Change-Flow:
   - Validiere neue Email (valides Format, unique)
   - Generiere Verifizierungs-Token (wie bei Registrierung, 24h gültig)
   - Sende Verifizierungs-Email an neue Adresse (Body: Link mit Token)
   - Speichere Token in `password_reset_tokens` (token_hash, expires_at, used_at=NULL) mit `type='email_change'`
   - **Audit-Log:** `log_audit_action(user_id, "email_change_requested", ip_address, user_agent, {"new_email": new_email})`
3. `POST /api/auth/verify-email-change` Endpoint:
   - Request: `{"token": "email-change-token"}`
   - Response: `{"message": "Email changed successfully"}`
   - Status: 200 OK
   - Prüfe Token in DB, gültig, nicht verwendet, nicht abgelaufen
   - Update `users.email` und `user_identities.provider_user_id` (where provider='email')
   - Setze `password_reset_tokens.used_at = NOW()`
   - **Audit-Log:** `log_audit_action(user_id, "email_change_completed", ip_address, user_agent)`
   - **Security-Benachrichtigung:** `send_security_notification(new_email, "email change", timestamp, ip_address)`
4. `PATCH /api/user/username` Endpoint:
   - Request: `{"new_username": "newname"}`
   - Response: `{"message": "Username changed successfully"}`
   - Status: 200 OK
   - Erfordert Auth (Access Token in Header)
5. Username-Change-Flow:
   - Prüfe `users.last_username_change_at`: wenn `NOT NULL` und `last_username_change_at + 7 days > NOW()` → 429 Too Many Requests
   - Validiere neuen Username (3-50 Zeichen, alphanumerisch + `_`, unique)
   - Update `users.username` und `users.last_username_change_at = NOW()`
   - **Audit-Log:** `log_audit_action(user_id, "username_change", ip_address, user_agent, {"old_username": old_username, "new_username": new_username})`
   - **Security-Benachrichtigung:** `send_security_notification(email, "username change", timestamp, ip_address)`
   - Response bei Limit: `{"detail": "Username can only be changed once per 7 days.", "next_change_at": "2026-10-26T14:00:00Z"}`
6. Edge-Cases:
   - Neue Email bereits registriert → 409 Conflict
   - Neuer Username bereits vergeben → 409 Conflict
   - Username-Änderung innerhalb 7 Tage → 429 Too Many Requests
   - Ungültiger Token → 400 Bad Request
   - Abgelaufener Token → 400 Bad Request
   - Nicht authentifiziert → 401 Unauthorized
7. Integration-Test:
   - `test_change_email_sends_verification_email`
   - `test_verify_email_change_updates_email`
   - `test_verify_email_change_logs_audit`
   - `test_verify_email_change_sends_security_notification`
   - `test_change_username_updates_username`
   - `test_change_username_logs_audit`
   - `test_change_username_sends_security_notification`
   - `test_change_username_within_7_days_returns_429`
   - `test_change_username_after_7_days_succeeds`
   - `test_change_email_duplicate_returns_409`
   - `test_change_username_duplicate_returns_409`
8. `pytest tests/test_user_management.py` grün, Coverage ≥ 85%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_change_email_sends_verification_email():
    # Arrange: user eingeloggt, access_token
    # Act: response = client.patch("/api/user/email", json={"new_email": ...}, headers={"Authorization": f"Bearer {access_token}"})
    # Assert: response.status_code == 200
    # Assert: email_change_token in DB
    # Assert: email in mock_email_service.sent_emails

def test_verify_email_change_updates_email():
    # Arrange: user, email_change_token generiert
    # Act: response = client.post("/api/auth/verify-email-change", json={"token": token})
    # Assert: response.status_code == 200
    # Assert: users.email updated
```

**Definition of Done:**
- [ ] Change-Email-Endpoint implementiert
- [ ] Verify-Email-Change-Endpoint implementiert
- [ ] Change-Username-Endpoint implementiert
- [ ] Alle Tests grün (≥ 85% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-19: Password Change (eingeloggt)

**Priorität:** P2 | **Komplexität:** MEDIUM | **Dependencies:** US-09, US-10, US-11, US-14, US-16
**Assignee:** Backend Developer 3

**Story:**
> Als eingeloggter User möchte ich mein Passwort ändern können, damit ich die Sicherheit meines Accounts regelmäßig aktualisieren kann. Alle Sessions werden dabei invalidiert und eine Security-Benachrichtigung gesendet.

**Acceptance Criteria:**
1. `POST /api/user/change-password` Endpoint:
   - Request: `{"old_password": "OldPass123", "new_password": "NewPass456"}`
   - Response: `{"message": "Password changed successfully. All sessions have been invalidated."}`
   - Status: 200 OK
   - Erfordert Auth (Access Token in Header)
2. Password-Change-Flow:
   - Validiere `old_password` gegen `user_identities.password_hash` via `verify_password`
   - Validiere `new_password` gegen Password-Strength-Requirements (US-11)
   - Update `user_identities.password_hash` mit neuem Hash
   - Invalide **alle** Refresh Tokens: `UPDATE user_refresh_tokens SET revoked_at = NOW() WHERE user_id = ?`
   - **Audit-Log:** `log_audit_action(user_id, "password_change", ip_address, user_agent)`
   - **Security-Benachrichtigung:** `send_security_notification(email, "password change", timestamp, ip_address)`
3. Edge-Cases:
   - Falsches `old_password` → 401 Unauthorized
   - `new_password` zu schwach → 400 Bad Request (mit Details: "Password must contain at least 1 uppercase letter and 1 digit")
   - `new_password` == `old_password` → 400 Bad Request ("New password must be different from old password")
   - Nicht authentifiziert → 401 Unauthorized
4. **Security:**
   - Alle Sessions werden invalidiert (User muss sich auf allen Geräten neu einloggen)
   - Password-Änderung wird geloggt (Audit Trail: `user_id`, `timestamp`, `ip_address`, `user_agent`)
   - User erhält Email-Benachrichtigung mit Timestamp und IP-Adresse
5. Integration-Test:
   - `test_change_password_success`
   - `test_change_password_invalidates_all_sessions`
   - `test_change_password_logs_audit`
   - `test_change_password_sends_security_notification`
   - `test_change_password_wrong_old_password_returns_401`
   - `test_change_password_weak_new_password_returns_400`
   - `test_change_password_same_as_old_returns_400`
   - `test_change_password_unauthenticated_returns_401`
6. `pytest tests/test_password_change.py` grün, Coverage ≥ 85%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_change_password_success():
    # Arrange: user eingeloggt, access_token
    # Act: response = client.post("/api/user/change-password", json={"old_password": "Old123", "new_password": "New456"}, headers={"Authorization": f"Bearer {access_token}"})
    # Assert: response.status_code == 200
    # Assert: password_hash in DB updated

def test_change_password_invalidates_all_sessions():
    # Arrange: user eingeloggt, 2 refresh_tokens in DB
    # Act: response = client.post("/api/user/change-password", ...)
    # Assert: response.status_code == 200
    # Assert: all refresh_tokens have revoked_at set

def test_change_password_logs_audit():
    # Arrange: user eingeloggt
    # Act: response = client.post("/api/user/change-password", ...)
    # Assert: audit_log entry exists with action="password_change"

def test_change_password_sends_security_notification():
    # Arrange: user eingeloggt
    # Act: response = client.post("/api/user/change-password", ...)
    # Assert: security notification email sent via mock_email_service
```

**Definition of Done:**
- [ ] Change-Password-Endpoint implementiert
- [ ] Session-Invalidation implementiert
- [ ] Audit-Log implementiert
- [ ] Security-Benachrichtigung implementiert
- [ ] Alle Tests grün (≥ 85% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-17: Integration Tests + Cross-Community Flows

**Priorität:** P2 | **Komplexität:** MEDIUM | **Dependencies:** US-13, US-14, US-15, US-16, US-19
**Assignee:** Backend Developer 1

**Story:**
> Als Entwickler möchte ich umfassende Integration-Tests für die kompletten Auth-Flows, damit die Interaktion zwischen allen Komponenten verifiziert ist.

**Acceptance Criteria:**
1. Integration-Test: Kompletter Registrierung-Flow
   - Register → Verify Email → Login → Refresh → Logout
2. Integration-Test: Kompletter Password-Reset-Flow
   - Register → Verify Email → Request Reset → Reset Password → Login mit neuem Passwort
3. Integration-Test: Kompletter Email-Change-Flow
   - Register → Verify Email → Change Email → Verify New Email → Login mit neuer Email
4. Integration-Test: Kompletter Password-Change-Flow
   - Register → Verify Email → Login → Change Password → Logout → Login mit neuem Passwort
   - Validierung: Nach Password-Change sind alle Refresh-Tokens invalidiert
5. Integration-Test: Multi-Device-Scenario
   - Login auf Device A → Login auf Device B → Logout auf Device A → Refresh auf Device B (sollte noch funktionieren)
   - Password-Change auf Device A → Refresh auf Device B (sollte fehlschlagen, 401)
6. Edge-Case-Tests:
   - Token-Replay-Angriff (abgelaufener Refresh Token)
   - Concurrent Login/Logout (Race Conditions)
   - Invalid Token Signatures
7. Alle Tests in `tests/test_integration_auth.py`
8. `pytest tests/test_integration_auth.py` grün, Coverage ≥ 90%

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_complete_registration_flow():
    # Arrange: client, mock_email_service
    # Act: register → verify email → login → refresh → logout
    # Assert: jeder Schritt erfolgreich, final state korrekt

def test_complete_password_reset_flow():
    # Arrange: user registriert und verifiziert
    # Act: request reset → reset password → login mit neuem passwort
    # Assert: jeder Schritt erfolgreich
```

**Definition of Done:**
- [ ] Alle Integration-Tests geschrieben
- [ ] Alle Tests grün (≥ 90% Coverage)
- [ ] Code Review bestanden
- [ ] PR gemerged

---

### US-18: CI/CD + Deployment Updates

**Priorität:** P3 | **Komplexität:** LOW | **Dependencies:** US-17
**Assignee:** Backend Developer 2

**Story:**
> Als Betreiber möchte ich die CI/CD-Pipeline und Docker-Konfiguration aktualisieren, damit das Auth-System korrekt deployed wird und alle Env-Variablen dokumentiert sind.

**Acceptance Criteria:**
1. `.github/workflows/deploy.yml` aktualisiert:
   - Neue Env-Variablen: `JWT_SECRET`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`
   - Coverage-Gate bleibt bei 80%
2. `docker-compose.yaml` aktualisiert:
   - Neue Env-Variablen in `api`-Service: `JWT_SECRET`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`
   - `db`-Service: Port 5432 exposed (für lokale Entwicklung)
3. `.env.example` aktualisiert:
   - Neue Variablen: `JWT_SECRET`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`
   - Dokumentation für jede Variable inkl. Beispielwerte
4. `README.md` aktualisiert:
   - Neue API-Endpoints dokumentiert
   - Auth-Flows erklärt
   - Env-Variablen dokumentiert
   - Rate Limiting erklärt (5 Login/Min, 3 Register/Std)
   - Username-Änderung: 1x pro 7 Tage
5. Health-Check-Endpoint: `GET /healthz` → `{"status": "ok", "database": "connected"}`
   - Test: `test_healthz_endpoint`
6. `pytest tests/test_healthz.py` grün

**TDD-Workflow:**
```python
# RED: Test schreiben
def test_healthz_endpoint():
    # Arrange: client
    # Act: response = client.get("/healthz")
    # Assert: response.status_code == 200
    # Assert: response.json()["status"] == "ok"
```

**Definition of Done:**
- [ ] CI/CD-Pipeline aktualisiert
- [ ] Docker-Konfiguration aktualisiert
- [ ] README aktualisiert
- [ ] Health-Check-Endpoint implementiert
- [ ] Alle Tests grün
- [ ] Code Review bestanden
- [ ] PR gemerged

---

## Sprint-Ablauf

### Tag 1: Fundament (Welle 1)

**Morgen:**
- Product Owner: Sprint-Planning mit Team, User Stories vorstellen
- Scrum Master: Board aufsetzen, Assignees festlegen

**Vormittag:**
- Backend Dev 1: US-09 (DB-Migration) — Tests schreiben, dann implementieren
- Backend Dev 1: US-10 (Email Service) — parallel zu US-09

**Nachmittag:**
- Backend Dev 1: PRs für US-09 und US-10 erstellen
- Code Reviewer: PRs reviewen, Feedback geben
- Backend Dev 1: Feedback einarbeiten, PRs mergen

**Abend:**
- Daily Standup: Fortschritt besprechen, Blocker identifizieren

---

### Tag 2: Utilities (Welle 2)

**Vormittag:**
- Backend Dev 1: US-11 (Password Hashing) — Tests schreiben, dann implementieren
- Backend Dev 1: US-12 (JWT Tokens) — parallel zu US-11

**Nachmittag:**
- Backend Dev 1: PRs für US-11 und US-12 erstellen
- Code Reviewer: PRs reviewen, Feedback geben
- Backend Dev 1: Feedback einarbeiten, PRs mergen

**Abend:**
- Daily Standup: Fortschritt besprechen, Blocker identifizieren

---

### Tag 3: Auth-Endpoints (Welle 3, parallel)

**Vormittag:**
- Backend Dev 2: US-13 (Registration + Email Verification) — Tests schreiben, dann implementieren
- Backend Dev 2: US-14 (Login + Refresh + Logout) — parallel zu US-13 (andere Endpoints)

**Nachmittag:**
- Backend Dev 2: PRs für US-13 und US-14 erstellen
- Code Reviewer: PRs reviewen (Security-Fokus!), Feedback geben
- Backend Dev 2: Feedback einarbeiten, PRs mergen

**Abend:**
- Daily Standup: Fortschritt besprechen, Blocker identifizieren

---

### Tag 4: User Management + Password Change (Welle 4, parallel)

**Vormittag:**
- Backend Dev 1: US-15 (Password Reset) — Tests schreiben, dann implementieren
- Backend Dev 3: US-16 (Email/Username Change) + US-19 (Password Change) — parallel

**Nachmittag:**
- Backend Dev 1: PR für US-15 erstellen
- Backend Dev 3: PRs für US-16 und US-19 erstellen
- Code Reviewer: PRs reviewen (Security-Fokus bei US-15 und US-19!), Feedback geben
- Backend Dev 1 + 3: Feedback einarbeiten, PRs mergen

**Abend:**
- Daily Standup: Fortschritt besprechen, Blocker identifizieren

**Hinweis:** US-15 und US-19 sind ähnlich (beide Password-Handling) — Backend Dev 1 kann Code aus US-15 für US-19 wiederverwenden.

---

### Tag 5: Integration + Deployment (Welle 5)

**Vormittag:**
- Backend Dev 1: US-17 (Integration Tests) — Tests schreiben
- Backend Dev 2: US-18 (CI/CD + Deployment) — Env-Variablen, Health-Check

**Nachmittag:**
- Backend Dev 1: PR für US-17 erstellen
- Backend Dev 2: PR für US-18 erstellen
- Code Reviewer: PRs reviewen, Feedback geben
- Backend Dev 1 + 2: Feedback einarbeiten, PRs mergen

**Abend:**
- Sprint-Review: Product Owner prüft Sprint-Gate
- Sprint-Retro: Team bespricht Verbesserungen

---

## Agent-Workflows

### Backend Developer Workflow

```bash
# 1. Branch erstellen
git checkout main
git pull
git checkout -b feature/us-XX-kurzbeschreibung

# 2. Tests schreiben (RED)
uv run pytest tests/test_*.py -v

# 3. Implementieren (GREEN)
# Code schreiben, bis Tests grün

# 4. Coverage prüfen
uv run pytest --cov=src --cov-report=term-missing

# 5. Linting
uv run ruff check src/
uv run ruff format src/

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

# 2. Checkout PR-Branch
gh pr checkout <nr>

# 3. Tests lokal ausführen
uv run pytest --cov=src --cov-fail-under=80

# 4. Linting
uv run ruff check src/

# 5. Security-Check (für Auth-PRs)
# - Input-Validierung
# - Token-Handling
# - Password-Hashing
# - SQL-Injection-Schutz
# - XSS-Schutz

# 6. Feedback geben
gh pr comment <nr> --body "Feedback: ..."

# 7. Approval oder Request Changes
gh pr review <nr> --approve
# oder
gh pr review <nr> --request-changes
```

---

## Offene Entscheidungen

### ✅ Email-Service Provider (entschieden)
**Entscheidung:** Strato SMTP (externer Relay)
**Begründung:** Strato SMTP ist beim Domain-Paket enthalten, SPF/DKIM bereits konfiguriert (bessere Deliverability). Eigener SMTP-Server wäre zu aufwändig (SPF/DKIM/DMARC, IP-Reputation).
**Konsequenz:** SMTP-Env-Variablen in `.env`: `SMTP_HOST=smtp.strato.de`, `SMTP_PORT=587`, `SMTP_USER=<email>`, `SMTP_PASSWORD=<password>`, `SMTP_FROM_EMAIL=<email>`

### ✅ Rate Limiting Library (entschieden)
**Entscheidung:** slowapi
**Begründung:** Standard-Library für FastAPI, einfach zu implementieren, In-Memory-Backend für MVP ausreichend.
**Konsequenz:** `slowapi` zu `pyproject.toml` hinzufügen, Decorator-Syntax für Endpoints.

### ✅ Username-Änderung Limit (entschieden)
**Entscheidung:** 1x pro 7 Tage
**Begründung:** Verhindert häufige Änderungen, aber flexibel genug für legitime Fälle.
**Konsequenz:** `last_username_change_at` Feld in `users`-Tabelle, Validierung bei Username-Änderung.

### ✅ Password Requirements (entschieden)
**Entscheidung:** 8-128 Zeichen + mindestens 1 Großbuchstabe + 1 Zahl (Mittel)
**Begründung:** Ausreichend sicher für MVP, Standard-Pattern (GitHub, Google), Balance zwischen Security und UX.
**Konsequenz:** Regex-Validierung: `^(?=.*[A-Z])(?=.*\d).{8,128}$`, klare Fehlermeldung bei Verletzung.

### ✅ Token-Invalidation bei Password-Änderung/Reset (entschieden)
**Entscheidung:** Alle bestehenden Sessions (Refresh Tokens) werden bei Password-Änderung oder Password-Reset invalidiert.
**Begründung:** Security-first. Wenn Passwort kompromittiert oder geändert, sollen alle Geräte ausgeloggt werden. Standard-Pattern (Google, GitHub).
**Konsequenz:** `UPDATE user_refresh_tokens SET revoked_at = NOW() WHERE user_id = ?` bei jedem Password-Change/Reset.

### ✅ Audit-Log + Benachrichtigung (entschieden)
**Entscheidung:** Audit-Log-Tabelle `user_audit_log` + Email-Benachrichtigung bei kritischen Aktionen (Password-Änderung, Email-Änderung, Username-Änderung, Password-Reset).
**Begründung:** Security-Best-Practice (SOC 2, GDPR Compliance), detaillierte Historie für Debugging und Security-Analyse, User wird informiert.
**Konsequenz:**
- Neue Tabelle `user_audit_log`: `id`, `user_id`, `action`, `timestamp`, `ip_address`, `user_agent`, `metadata` (JSON)
- Utility: `src/services/audit_service.py` mit `log_audit_action()` und `send_security_notification()`
- Email-Benachrichtigung: "Your password was changed" (mit Timestamp, IP-Adresse)
- US-15, US-16, US-19 nutzen Audit-Service

### 🔮 Zukünftige Erweiterungen (nicht in Sprint 2)
**Passkey (WebAuthn/FIDO2):** Biometrische Auth (Face ID, Touch ID) oder Hardware-Token (YubiKey).
**Sprint-Kandidat:** Sprint 3 oder später, erfordert zusätzliche Libraries (`webauthn`, `pydantic` Extensions).
**Vorbereitung:** `user_identities`-Schema unterstützt bereits Multi-Provider → `provider='passkey'` kann später hinzugefügt werden.

---

## Risk Management

**Risiken:**
1. **Email-Service-Integration:** Mock-Service für MVP ausreichend, aber späterer Wechsel zu echtem Provider erfordert refactoring
   - **Mitigation:** EmailService Protocol abstrahiert Provider, Wechsel ist einfach

2. **Security-Schwachstellen:** Password-Hashing, Token-Handling, Input-Validierung
   - **Mitigation:** Code Review mit Security-Fokus, bcrypt cost 12, JWT-Best-Practices

3. **Multi-Provider-Extension:** Schema vorbereitet, aber nicht getestet
   - **Mitigation:** Integration-Tests für Email-Provider, Schema-Design für OAuth validiert

4. **Performance:** DB-Queries bei jedem Login/Refresh
   - **Mitigation:** Indexes auf kritischen Spalten, Performance-Tests in US-17

---

## Definition of Done (Sprint-Level)

**Alle User Stories müssen:**
- ✅ Implementiert und gemerged
- ✅ Tests grün (≥ 80% Coverage)
- ✅ Code Review bestanden (keine CRITICAL/HIGH Issues)
- ✅ Dokumentation aktualisiert (README, API-Docs)
- ✅ Security-Review bestanden (für Auth-PRs)
- ✅ CI/CD-Pipeline grün

**Sprint-Gate (Product Owner-Abnahme):**
- ✅ Alle 9 User Stories abgeschlossen
- ✅ Kompletter Auth-Flow funktioniert: Register → Verify → Login → Refresh → Logout
- ✅ Password-Reset-Flow funktioniert
- ✅ Email-Verifizierung funktioniert
- ✅ Multi-Provider-Schema vorbereitet
- ✅ 80%+ Coverage
- ✅ `ruff check` fehlerfrei
- ✅ Security-Review bestanden

---

## Nächste Schritte nach Sprint 2

**Sprint 3 (geplant):**
- OAuth-Provider-Integration (GitHub, Google)
- Telefon-Auth (SMS-Code)
- Account-Deletion (GDPR)
- Rate Limiting für Auth-Endpoints
- Multi-Device-Management ("Welche Geräte sind eingeloggt?")
- Email-Templates (HTML-Emails statt plain text)

---

## Quellen

- Sprint 1 Backlog: `docs/backlog.md`
- Architektur: `docs/architecture.md`
- Knowledge Graph: `graphify-out/GRAPH_REPORT.md`
