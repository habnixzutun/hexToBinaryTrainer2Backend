# Auth-Härtung — Adversarialer Testplan

> Stand: 2026-10-26 · Autor: 🧪 QA Engineer
> Zugehörig zu: `docs/auth-hardening-plan.md`
> Ziel: Pro User Story die Unit-, Integration- und Adversarial-Testfälle explizit auflisten. Fokus auf Angriffsszenarien, Timing-Side-Channels und Regressionen.

---

## 1. Test-Strategie-Überblick

### Test-Pyramide

```
           ╱╲
          ╱E2E╲        US-08b (Password-Reset-UI)
         ╱──────╲
        ╱Advers.╲      Pro US 2–6 Szenarien (timing, replay, dict)
       ╱──────────╲
      ╱Integration╲    HTTP-Flows über FastAPI-TestClient
     ╱──────────────╲
    ╱    Unit-Tests   ╲   Pure functions, Services, Utilities
   ╱────────────────────╲
```

### Rahmenbedingungen

| Aspekt | Vorgabe |
|---|---|
| Framework | `pytest` + `pytest-asyncio` + `httpx.AsyncClient` |
| Test-DB | PostgreSQL via `docker-compose.test.yaml` (Port 5434) — wie Produktiv-DB |
| Fixtures | Zentrale `tests/conftest.py` mit `async_engine`, `session`, `app_dependency_override` |
| Coverage | Pro US ≥ 80 %, Gesamt ≥ 85 % — erzwungen via `pyproject.toml` |
| Secret-Schutz | `COOKIE_SECURE=false` in Tests gesetzt; `JWT_SECRET` via ENV |
| Timing-Tests | `pytest.mark.timing` + `statistics`-Modul; ±50ms p95 als Schwelle |
| Race-Condition-Tests | `pytest.mark.race` + `asyncio.gather`; 10+ parallele Requests |

### Test-Data-Factories

Neue Datei `tests/factories.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid_utils import uuid7
from src.models.auth import User, UserIdentity, UserRefreshToken, PasswordResetToken
from src.utils.password import hash_password

def make_user(*, email=None, username=None) -> User: ...
def make_identity(user_id, *, provider="email", password="Password1!", verified=True) -> UserIdentity: ...
def make_refresh_token(user_id, *, expires_days=7) -> UserRefreshToken: ...
def make_reset_token(user_id, *, expires_hours=1) -> PasswordResetToken: ...

# Konstanten
VALID_PASSWORD = "Password1!"
NEW_PASSWORD = "NewPass2@"
WEAK_PASSWORD = "short"
```

---

## 2. Test-Matrix pro User Story

---

### US-01 · JWT-Secret in Settings-System überführen 🔴 CRITICAL

**Dateien**: `tests/test_settings.py`, `tests/test_jwt_utils.py`, `tests/test_secret_leak.py`

#### Unit-Tests

- [ ] `test_settings_missing_jwt_secret_raises_value_error`
  - Given: ENV enthält `JWT_SECRET=""` (oder fehlt)
  - When: `Settings()` wird instanziiert
  - Then: `ValueError` mit Meldung `"JWT_SECRET is required"`, kein Plaintext im Exception-Objekt
- [ ] `test_settings_with_jwt_secret_ok`
  - Given: ENV enthält `JWT_SECRET="strong-random-value"`
  - When: `Settings()`
  - Then: Instanz korrekt, `jwt_secret.get_secret_value() == "strong-random-value"`
- [ ] `test_jwt_secret_is_secret_str`
  - Then: `isinstance(settings.jwt_secret, SecretStr) is True`
- [ ] `test_jwt_round_trip_with_settings_secret`
  - Given: Settings mit Secret geladen
  - When: `create_access_token(user_id="u1")` → `decode_token(token)`
  - Then: `payload["sub"] == "u1"`, `payload["type"] == "access"`
- [ ] `test_all_token_types_round_trip` — access, refresh, verification, password_reset, email_change

#### Adversarial-Tests ⚔️

- [ ] `test_secret_never_in_startup_crash_log`
  - Given: `JWT_SECRET="ultra-secret-value-xyz"`
  - When: Settings-Validierung wirft `ValidationError` (z. B. durch ungültige Kombination)
  - Then: Captured stderr/stdout enthält **nicht** `"ultra-secret-value-xyz"`
  - Mechanik: `capfd`/`capsys` + `from None`-Chain in `get_settings()`
- [ ] `test_secret_never_in_traceback`
  - Given: Crash während `Settings()` (z. B. ungültige ENV-Kombination)
  - When: Traceback via `traceback.format_exc()` formatiert
  - Then: Secret nicht in Traceback-String vorhanden
- [ ] `test_default_value_removed`
  - Then: `jwt_utils.py` enthält nicht mehr `"dev-secret-change-in-production"`
  - Mechanik: AST/Grep-Test
- [ ] `test_no_os_environ_direct_read`
  - Then: `jwt_utils.py` nutzt nicht `os.environ.get("JWT_SECRET"...)`
  - Mechanik: AST-Inspection

---

### US-02 · Refresh-Token-Rotation + Replay-Detektion 🟠 HIGH

**Dateien**: `tests/test_refresh_rotation.py`, `tests/test_adversarial_refresh.py`

#### Integration-Tests

- [ ] `test_refresh_returns_new_access_token`
  - Given: User eingeloggt, Refresh-Token in DB + Cookie
  - When: `POST /api/auth/refresh`
  - Then: 200, neuer Access-Token, neuer Refresh-Token im Cookie
- [ ] `test_old_refresh_token_revoked_after_use`
  - Given: Refresh-Token T1 genutzt
  - When: DB-Query auf `user_refresh_tokens`
  - Then: `T1.revoked_at is not None`
- [ ] `test_new_refresh_token_persisted`
  - Given: Refresh mit T1
  - Then: neue Zeile in `user_refresh_tokens` mit neuem Hash, `revoked_at is None`
- [ ] `test_expired_refresh_returns_401`
  - Given: abgelaufener Refresh-Token
  - Then: 401 `"Invalid or expired refresh token"`
- [ ] `test_revoked_refresh_returns_401`
  - Given: manuell revoketer Refresh-Token (via logout)
  - When: `POST /refresh`
  - Then: 401

#### Adversarial-Tests ⚔️

- [ ] `test_replay_after_legitimate_use_kills_all_sessions`
  - Given: User eingeloggt, T1 genutzt → T2 ausgestellt
  - When: T1 erneut präsentiert (Replay)
  - Then: (a) 401, (b) **alle** Tokens des Users revoked, (c) Audit-Event `refresh_token_replay_detected`, (d) Security-Notification versendet
- [ ] `test_three_replays_in_sequence_only_one_audit_event`
  - Given: Replay-Szenario wie oben
  - When: 3× Replay in Folge
  - Then: Nur 1× `refresh_token_replay_detected` Audit-Event (kein Spam)
- [ ] `test_hundred_parallel_rotations`
  - Given: User mit 100 gleichzeitigen Login-Sessions
  - When: 100 parallele Refresh-Calls
  - Then: Jeder Call bekommt neuen Token, kein Datenverlust, keine Race
- [ ] `test_token_type_confusion_rejected`
  - Given: Access-Token mit `type="access"`
  - When: Als Refresh-Token präsentiert
  - Then: 401 (type check schlägt fehl)
- [ ] `test_replay_across_users_isolated`
  - Given: User A's revoketer Token
  - When: Von User B präsentiert
  - Then: 401, keine Cross-User-Side-Effects, kein Audit-Event für User A
- [ ] `test_token_hash_uniqueness`
  - Given: 1000 Refresh-Tokens für denselben User
  - Then: Alle `token_hash`-Werte unique (kein Collision)

---

### US-03 · Username- & Email-Enumeration entfernen 🟠 HIGH

**Dateien**: `tests/test_no_enumeration.py`, `tests/test_adversarial_enumeration.py`

#### Integration-Tests

- [ ] `test_register_duplicate_email_returns_201_generic`
  - Given: Email bereits registriert
  - When: `POST /api/auth/register`
  - Then: 201 mit Body `{ "message": "If this email is available, ...", "user_id": null }` — identisch zu frischer Email
- [ ] `test_register_duplicate_username_returns_201_generic`
  - Given: Username bereits vergeben
  - When: Register mit neuer Email, bestehendem Username
  - Then: 201 mit gleicher generischer Nachricht
- [ ] `test_register_fresh_email_returns_201_with_user_id`
  - Given: Email + Username neu
  - When: Register
  - Then: 201 mit `user_id != null`, aber **gleiche** `message`
- [ ] `test_password_reset_unknown_email_still_200`
  - Regression für bestehenden, korrekten Flow
- [ ] `test_password_reset_known_email_200_indistinguishable`
  - Given: bekannte Email
  - When: `POST /request-password-reset`
  - Then: 200, Response-Body identisch zu unbekannter Email

#### Adversarial-Tests ⚔️

- [ ] `test_timing_email_enumeration_50_50`
  - Given: 50 registrierte Emails, 50 unbekannte
  - When: 100 Requests an `/register`, Response-Zeit gemessen
  - Then: p95(registered) - p95(unknown) < 50ms
  - Mechanik: `statistics.quantiles`, 100 Samples pro Kategorie
- [ ] `test_timing_username_enumeration_1000_samples`
  - Given: 500 bekannte, 500 unbekannte Usernames
  - When: 1000 Register-Requests
  - Then: t-test: p > 0.05 (kein signifikanter Unterschied)
- [ ] `test_response_body_never_leaks_duplicate_info`
  - Given: 100 Registrierungen, davon 50 Duplikate
  - When: Alle Responses eingesammelt
  - Then: Nur 2 eindeutige Body-Varianten (beide generisch) — keine Sondermeldung
- [ ] `test_dictionary_attack_100_entries`
  - Given: 100 Emails/Username-Paare, 30 davon registriert
  - When: Alle 100 registrieren
  - Then: Response-Verteilung erlaubt keine Rekonstruktion der 30
  - Assertion: Alle 100 Responses haben identischen SHA-256
- [ ] `test_no_internal_logging_of_duplicate_error`
  - Given: Duplicate-Registration
  - When: Captured logs
  - Then: Kein WARN/ERROR mit "duplicate email" oder "username taken" (kein Leak via Logs)

---

### US-04 · Persistentes Rate-Limit + Resend-Verification 🟠 HIGH

**Dateien**: `tests/test_rate_limit.py`, `tests/test_adversarial_ratelimit.py`

#### Integration-Tests

- [ ] `test_register_rate_limit_3_per_hour`
  - Given: 3 erfolgreiche Registers pro (IP, Email)
  - When: 4. Request
  - Then: 429 `"Too many registration attempts"`
- [ ] `test_rate_limit_expires_after_ttl`
  - Given: Limit erreicht, 1h gewartet (via `time_machine` oder DB-Manipulation)
  - When: Nächster Request
  - Then: 201
- [ ] `test_resend_verification_endpoint_exists`
  - Then: `POST /api/auth/resend-verification` existiert, akzeptiert `{ "email": ... }`
- [ ] `test_resend_verification_rate_limit`
  - Given: 3 Resends in 1h
  - When: 4. Resend
  - Then: 429
- [ ] `test_resend_verification_sends_new_token`
  - Given: registrierter, nicht-verifizierter User
  - When: Resend
  - Then: neue Email an `MockEmailService.sent`, neuer Token in DB
- [ ] `test_resend_verification_audit_event`
  - Then: `verification_resend_requested` in `user_audit_log`

#### Adversarial-Tests ⚔️

- [ ] `test_ip_rotation_same_email_limit_holds`
  - Given: 50 Requests von 50 verschiedenen IPs, gleiche Email
  - When: Alle 50 Register-Requests
  - Then: Nach 3 erfolgreichen → alle weiteren 429 (Limit keyed auf Email mit!)
  - Assertion: `(ip, email)`-Key → email dominiert
- [ ] `test_persistence_across_server_restart`
  - Given: Rate-Limit-Eintrag in DB/Redis
  - When: App-Prozess neu gestartet
  - Then: Limit-Eintrag bleibt erhalten (nicht in-memory!)
  - Mechanik: 2 Test-Runs nacheinander, DB-Check zwischen den Runs
- [ ] `test_race_condition_10_parallel_requests`
  - Given: 0 Rate-Limit-Einträge
  - When: 10 parallele Register-Requests (`asyncio.gather`)
  - Then: Maximal 3 erfolgreich, Rest 429 — nie mehr als das Limit
  - Assertion: `sum(status == 201) <= 3`
- [ ] `test_sql_injection_in_email_field`
  - Given: Email `"'; DROP TABLE rate_limit_attempt; --"`
  - When: Register mit dieser Email
  - Then: 422 (Pydantic EmailStr rejects), DB intakt
- [ ] `test_extreme_volume_1000_ips_1_email`
  - Given: 1000 verschiedene IPs, alle auf gleiche Email
  - When: Alle registerieren
  - Then: Immer noch ≤ 3 erfolgreich (Email-basiertes Limit wirkt)

---

### US-05 · Adaptive Login-Lockout 🟡 MEDIUM

**Dateien**: `tests/test_adaptive_lockout.py`, `tests/test_adversarial_lockout.py`

#### Integration-Tests

- [ ] `test_login_backoff_curve_after_15_failures`
  - Given: 15 fehlgeschlagene Logins von IP A
  - When: Weiterer Login-Versuch
  - Then: 429 mit `"Too many attempts"`
  - Backoff-Kurve (in ADR definiert): 5/min → 3/min → 1/min
- [ ] `test_successful_login_resets_counter`
  - Given: 4 Fehlschläge
  - When: Erfolg
  - Then: Counter → 0, nächste 5 Versuche sofort möglich
- [ ] `test_lockout_response_is_generic`
  - Given: Lockout aktiv
  - When: Login mit bekannter + unbekannter Email
  - Then: Beides 429 mit exakt gleicher Nachricht (keine Info über Account-Existenz)
- [ ] `test_audit_event_on_lockout`
  - Then: `login_lockout_triggered` in Audit-Log mit `ip`, `email_attempted`, `user_agent`

#### Adversarial-Tests ⚔️

- [ ] `test_x_forwarded_for_spoofing_blocked`
  - Given: Angreifer mit IP A, setzt `X-Forwarded-For: 1.2.3.4`
  - When: 10 Login-Versuche mit wechselnden XFF-Headern
  - Then: Lockout greift trotzdem — Server nutzt **Client-IP** aus vertrauenswürdiger Quelle (nicht manipulierbaren Header)
  - Assertion: Wenn FastAPI hinter Reverse Proxy, dann nur **erster** XFF-Wert trusted, sonst `request.client.host`
- [ ] `test_distributed_brute_force_100_ips_1_account`
  - Given: 100 verschiedene IPs, alle auf Ziel-Account `victim@example.com`
  - When: Alle versuchen Login
  - Then: Entweder (a) Account-based Lockout nach N Fehlversuchen (über alle IPs), oder (b) ADR-dokumentierte Mitigation
  - Assertion: max. 15 Fehlversuche insgesamt über alle IPs
- [ ] `test_lockout_bypass_via_timing`
  - Given: Lockout aktiv
  - When: Warten auf exakt das Backoff-Ende + Request
  - Then: Request funktioniert **genau einmal**, danach neuer Lockout
  - Assertion: Kein "grace period"-Bug, der mehrere Requests durchlässt
- [ ] `test_concurrent_logins_during_lockout`
  - Given: Lockout aktiv (1/min)
  - When: 20 parallele Login-Requests
  - Then: Alle 20 → 429, nie mehr als 1 pro Minute tatsächlich verarbeitet
- [ ] `test_lockout_is_per_ip_not_global`
  - Given: IP A locked out
  - When: IP B loggt ein
  - Then: IP B nicht betroffen (kein globaler Lockout)

---

### US-06 · Logout-all Sessions 🟡 MEDIUM

**Dateien**: `tests/test_logout_all.py`

#### Integration-Tests

- [ ] `test_logout_all_revokes_all_tokens`
  - Given: User mit 5 aktiven Refresh-Tokens
  - When: `POST /api/auth/logout-all`
  - Then: 200, `count == 5`, alle 5 Tokens haben `revoked_at` gesetzt
- [ ] `test_logout_all_requires_auth`
  - Given: Kein Access-Token im Request
  - When: `POST /logout-all`
  - Then: 401
- [ ] `test_logout_all_response_format`
  - Then: Body `{ "message": "All sessions revoked.", "count": <n> }`
- [ ] `test_logout_all_audit_event`
  - Then: `all_sessions_revoked` mit `session_count` in Audit-Log

#### Adversarial-Tests ⚔️

- [ ] `test_logout_all_race_with_concurrent_refresh`
  - Given: User mit 3 Tokens
  - When: `logout-all` + Refresh-Call **parallel** (`asyncio.gather`)
  - Then: Konsistentes Ergebnis: entweder Refresh erfolgreich und dann revoked, oder Refresh 401. Nie "neuer Token nach logout-all aktiv"
- [ ] `test_logout_all_with_other_users_token`
  - Given: Access-Token von User A
  - When: `POST /logout-all`
  - Then: Nur Tokens von User A revoked, User B's Tokens unberührt
- [ ] `test_logout_all_idempotent`
  - Given: User bereits alle Sessions revoked
  - When: Erneut `logout-all`
  - Then: 200, `count == 0`, keine Duplikate in Audit
- [ ] `test_logout_all_with_expired_tokens`
  - Given: User hat 3 Tokens, 2 bereits expired
  - When: `logout-all`
  - Then: Alle 3 revoked (auch expired), count = 3

---

### US-07 · Email-Change-Token verschlüsseln 🟡 MEDIUM

**Dateien**: `tests/test_email_change_encryption.py`

#### Unit-Tests

- [ ] `test_email_change_token_does_not_contain_plaintext_email`
  - Given: `create_email_change_token(user_id, "secret-new@example.com")`
  - When: Token (Base64-decoded) inspiziert
  - Then: `"secret-new@example.com"` NICHT als Substring vorhanden
  - Mechanik: Token in 3 Parts splitten (JWT), Payload-Teil Base64-decodieren, byte-level search
- [ ] `test_email_change_round_trip`
  - Given: Token erstellt
  - When: `decode_email_change_token(token)`
  - Then: `{ "sub": user_id, "new_email": "..." }`
- [ ] `test_legacy_unencrypted_token_rejected`
  - Given: Token erstellt mit altem (unverschlüsseltem) Flow
  - When: `decode_email_change_token`
  - Then: `InvalidTokenError` — saubere Exception, kein Leak

#### Adversarial-Tests ⚔️

- [ ] `test_fernet_key_not_in_logs`
  - Given: `EMAIL_CHANGE_KEY` gesetzt
  - When: Verschiedene Flows, Logs captured
  - Then: Key taucht nie in Logs auf (SecretStr + filter)
- [ ] `test_token_tampering_detected`
  - Given: Gültiger Token
  - When: Ein Byte im Ciphertext geändert
  - Then: `InvalidTokenError` (Fernet's MAC check)
- [ ] `test_key_rotation_invalidates_old_tokens` (falls ADR Key-Rotation vorsieht)
  - Given: Token mit Key K1 erstellt
  - When: Key auf K2 rotiert
  - Then: alter Token → `InvalidTokenError`

---

### US-08 · Verification-Resend mit silent Handling 🟡 MEDIUM

**Dateien**: `tests/test_resend_verification.py`, `tests/test_adversarial_resend.py`

#### Integration-Tests

- [ ] `test_resend_for_unverified_user_sends_email`
  - Given: registrierter, nicht-verifizierter User
  - When: `POST /resend-verification`
  - Then: 200, Email in `MockEmailService.sent`
- [ ] `test_resend_for_verified_user_no_email`
  - Given: bereits verifizierter User
  - When: Resend
  - Then: 200, **keine** Email gesendet
- [ ] `test_resend_for_unknown_email_no_email`
  - Given: Email nicht registriert
  - When: Resend
  - Then: 200, keine Email
- [ ] `test_resend_always_same_message`
  - Given: 3 Szenarien (unverified, verified, unknown)
  - Then: Alle 3 Responses identisch: `"If this email is registered and unverified, a new mail has been sent."`

#### Adversarial-Tests ⚔️

- [ ] `test_timing_resend_100_registered_100_unknown`
  - Given: 100 registrierte, 100 unbekannte Emails
  - When: 200 Resend-Requests
  - Then: p95(registered) - p95(unknown) < 50ms
- [ ] `test_resend_rate_limit_3_per_hour`
  - Given: 3 Resends in 1h
  - When: 4. Resend
  - Then: 429, gleiche Response-Struktur wie 200 (kein Leak via Status-Code? → ADR-Entscheidung)
- [ ] `test_old_token_validity_after_resend`
  - Given: User hat Verifikations-Token T1
  - When: Resend ausgestellt → T2
  - Then: ADR-Entscheidung: (a) T1 invalid, oder (b) beide gültig
  - Assertion: Verhalten konsistent mit ADR
- [ ] `test_resend_flood_same_ip_1000_requests`
  - Given: 1000 parallele Resend-Requests
  - Then: Max 3 erfolgreich, Rest 429

---

### US-08b · Password-vergessen UI-Flow 🎨 FE

**Dateien**: `tests/e2e/test_password_reset_ui.spec.ts` (Playwright o. ä.)

#### Integration/E2E-Tests

- [ ] `test_forgot_password_link_on_login_page`
  - Then: Login-Seite enthält Link "Passwort vergessen?" → `/forgot-password`
- [ ] `test_forgot_password_form_submits_generic_success`
  - Given: `/forgot-password` mit Email "any@example.com"
  - When: Submit
  - Then: Success-Nachricht angezeigt, **immer** identisch (registered oder nicht)
- [ ] `test_reset_password_form_validates_strength`
  - Given: `/reset-password?token=...`
  - When: Passwort "short" eingeben
  - Then: Client-Validation-Fehler visible
- [ ] `test_reset_password_confirm_must_match`
  - When: Password + Confirm divergieren
  - Then: Submit disabled / Fehler
- [ ] `test_reset_password_success_redirects_to_login`
  - Given: Gültiger Token, neues Passwort
  - When: Submit
  - Then: Redirect zu `/login`, Toast "Passwort erfolgreich zurückgesetzt"

#### Adversarial-Tests ⚔️

- [ ] `test_token_not_in_browser_history_after_submit`
  - Given: Reset-Form submitted
  - When: `window.history.length` / `location.href` geprüft
  - Then: Token nicht mehr in URL visible
  - Mechanik: `history.replaceState` nach Submit
- [ ] `test_csrf_attack_on_forgot_password_blocked`
  - Given: Angreifer-Seite mit Auto-Submit-Form auf `/forgot-password`
  - When: Cross-Origin Request
  - Then: Blocked via SameSite=strict (Browser-seitig) + CORS
- [ ] `test_token_replay_in_ui_shows_error`
  - Given: Token bereits verwendet
  - When: 2. Versuch im UI
  - Then: Fehlermeldung "Link ist abgelaufen" (nicht "schon benutzt" — Info-Minimierung)
- [ ] `test_password_not_in_console_log`
  - Given: Reset-Form mit Passwort "Secret1!"
  - When: Submit
  - Then: `console.log` enthält **nicht** `"Secret1!"`
- [ ] `test_mobile_keyboard_type_correct`
  - Given: Mobile Viewport (iPhone SE)
  - When: Email-Feld fokussiert → email-keyboard
  - When: Passwort-Feld fokussiert → password-keyboard (verdeckt)
- [ ] `test_accessibility_labels_aria`
  - Then: Alle Inputs haben `<label>` oder `aria-label`, Error-Messages via `aria-live`

---

### US-09 · Strukturierte Logging 🟢 LOW

**Dateien**: `tests/test_logging.py`

#### Unit-Tests

- [ ] `test_no_print_statements_in_auth_modules`
  - Given: `src/routers/auth.py`, `src/services/auth_service.py`, `src/services/password_reset_service.py`
  - When: AST geparst
  - Then: Kein `ast.Call` mit `func.id == "print"`
- [ ] `test_log_level_configurable_via_settings`
  - Given: `LOG_LEVEL=DEBUG`
  - When: Logger erzeugt
  - Then: `logger.level == logging.DEBUG`
- [ ] `test_json_log_format`
  - Given: Log-Event
  - When: Output captured
  - Then: JSON-parsebar, enthält `timestamp`, `level`, `message`

#### Adversarial-Tests ⚔️

- [ ] `test_secret_str_never_in_log_output`
  - Given: `SecretStr` mit Wert `"geheim123"`
  - When: Verschiedene Auth-Flows, Logs captured
  - Then: `"geheim123"` nirgendwo im Log-Output
- [ ] `test_password_never_logged`
  - Given: Login mit Passwort `"Password1!"`
  - When: Logs captured
  - Then: `"Password1!"` nicht in Logs

---

### US-10 · Timing-safe Token-Vergleich 🟢 LOW

**Dateien**: `tests/test_timing_safe.py`

#### Unit-Tests

- [ ] `test_hash_comparison_equal_returns_true`
  - Given: Gleicher Hash zweimal
  - When: `hmac.compare_digest(h1, h2)`
  - Then: True
- [ ] `test_hash_comparison_different_returns_false`
  - Given: Unterschiedliche Hashes
  - Then: False
- [ ] `test_auth_service_uses_compare_digest`
  - Given: `auth_service.py`
  - When: AST geparst
  - Then: Enthält `hmac.compare_digest`-Aufruf

#### Adversarial-Tests ⚔️

- [ ] `test_timing_side_channel_10000_samples`
  - Given: 10000 Vergleiche gleicher Hash, 10000 unterschiedlicher Hash
  - When: `time.perf_counter()` für jeden
  - Then: t-test: p > 0.05 (kein signifikanter Unterschied)
  - Mechanik: `scipy.stats.ttest_ind`
- [ ] `test_password_reset_service_uses_compare_digest`
  - AST-Check für `password_reset_service.py`

---

### US-11 · Cookie-Secure Dev/Prod + CSRF-Ergänzung 🟢 LOW

**Dateien**: `tests/test_cookie_secure.py`

#### Unit-Tests

- [ ] `test_dev_settings_cookie_secure_false`
  - Given: `BASE_URL="http://localhost:8000"`
  - When: `Settings()`
  - Then: `cookie_secure == False`
- [ ] `test_prod_settings_cookie_secure_true`
  - Given: `BASE_URL="https://prod.example.com"`
  - Then: `cookie_secure == True`
- [ ] `test_env_override_cookie_secure`
  - Given: `COOKIE_SECURE=0`, `BASE_URL=https://...`
  - Then: `cookie_secure == False` (ENV gewinnt)
- [ ] `test_cookie_set_with_correct_flags`
  - Given: Login-Response
  - Then: Cookie hat `httponly=True`, `samesite="strict"`, `secure=<settings>`

#### Adversarial-Tests ⚔️

- [ ] `test_csrf_double_submit_pattern` (falls ADR es vorsieht)
  - Given: Double-Submit-Cookie gesetzt
  - When: Request mit fehlendem/mismatched CSRF-Token
  - Then: 403
- [ ] `test_cookie_not_sent_on_http_in_prod`
  - Given: Prod-Settings (`secure=True`)
  - When: HTTP-Request (nicht HTTPS)
  - Then: Browser würde Cookie nicht mitschicken (via Test-Client prüfbar)

---

## 3. Test-Data-Strategie

### Factories

Siehe `tests/factories.py` (oben). Alle Tests nutzen Factories, keine Magic Strings.

### Konstanten (zentral)

```python
VALID_PASSWORD = "Password1!"
WEAK_PASSWORD = "short"
VALID_EMAIL = "user@example.com"
VALID_USERNAME = "testuser"
USER_ID_PREFIX = "test-user-"
```

### Shared Fixtures

Von `tests/conftest.py` geerbt:
- `async_engine` — Postgres-Engine für Tests
- `session` — AsyncSession mit Rollback nach Test
- `app_dependency_override` — FastAPI Dependency Override

Neu:
- `seeded_user` — vorbefüllter User via Factory
- `mock_email_service` — `MockEmailService`-Instanz
- `rate_limit_store` — leerer Rate-Limit-Store (pro Test)

---

## 4. Coverage-Report-Strategie

### Pro-US-Command

```bash
pytest tests/test_<us_name>.py --cov=src/services --cov=src/routers --cov=src/utils \
  --cov-report=term-missing --cov-fail-under=80
```

### Gesamt-Coverage-Gate

In `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["src"]
[tool.coverage.report]
fail_under = 85
```

### CI-Integration

- Coverage-Badge in README
- Gate im CI: PR rejected bei < 85 %
- Per-US-Reports als CI-Artefakt

---

## 5. Execution-Reihenfolge

| Phase | Timing | Tests |
|---|---|---|
| **RED** (vor Implementierung) | Pro US zu Beginn | Unit-Tests + Integration-Tests geschrieben, **alle rot** |
| **GREEN** (während Implementierung) | Während BE entwickelt | Tests werden grün, Coverage steigt |
| **Adversarial** (nach GREEN) | Vor Security-Review | Adversarial-Tests laufen, zeigen Lücken |
| **Regression** (nach Merge) | Nach US-Merge in Sprint-Branch | Alle bisherigen Tests laufen weiter grün |
| **Sprint-Ende** | Nach Sprint-Final-Merge in `main` | Gesamt-Coverage ≥ 85 %, alle adversarial tests grün |

### Markers

```python
@pytest.mark.timing     # Timing-Sensitive Tests (±50ms p95)
@pytest.mark.race       # Race-Condition-Tests (asyncio.gather)
@pytest.mark.adversarial # Alle Angriffsszenarien
@pytest.mark.security    # Security-relevant (SEC-Review-Pflicht)
```

Execution: `pytest -m "not timing"` für schnelle Runs; `pytest -m timing` für vollständige Security-Validierung.

---

## Zusammenfassung

| Kennzahl | Wert |
|---|---|
| **Testfälle gesamt** | ~130 (60 Unit + 45 Integration + 25 Adversarial) |
| **US mit meisten adversarial Tests** | US-03 (Enumeration, 5 Szenarien), US-04 (Rate-Limit, 5), US-02 (Replay, 6) |
| **Kritischste abgedeckte Szenarien** | JWT-Secret-Leak (US-01), Refresh-Replay (US-02), Timing-Enumeration (US-03), Race-Condition im Rate-Limit (US-04), XFF-Spoofing im Lockout (US-05) |
| **Timing-Tests** | US-03, US-08, US-10 (3 USs, 4 Tests) |
| **Race-Condition-Tests** | US-02, US-04, US-05, US-06 (4 USs, 4 Tests) |
| **Regression-Tests** | 8 explizite Regressionen (Secret-Leak, Legacy-Token, etc.) |
