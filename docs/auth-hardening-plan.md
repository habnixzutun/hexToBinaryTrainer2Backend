# Auth-Härtung — Sprint-Plan

> Stand: 2026-10-26 · Folgesprint auf Sprint 2 (Auth-System) & Sprint 3 (Production Readiness)
> Ziel: **Robustheit & Defense-in-Depth** — CRITICAL/HIGH/MEDIUM-Schwachstellen aus dem Auth-Review schließen, Password-Reset-UI, Logging-Aufräumung, Dokumentation.

## Kontext

Das Auth-System (Sprint 2) ist funktional vollständig:
- ✅ bcrypt, httponly+SameSite=strict Cookies
- ✅ Refresh-Token-Hash in DB
- ✅ Generische Login-Fehlermeldungen (Anti-Enumeration)
- ✅ SecretStr in Settings gegen Log-Leaks
- ✅ Password-Reset Backend komplett (inkl. Session-Kill nach Reset)

**Verbleibende Lücken** (Review 2026-10-26):
- 🔴 CRITICAL: `JWT_SECRET` mit unsicherem Default außerhalb Settings
- 🟠 HIGH: Keine Refresh-Rotation, Username-Enumeration, In-Memory-Rate-Limit
- 🟡 MEDIUM: Kein adaptiver Lockout, kein Logout-all, unverschlüsselte Email-Tokens
- 🟢 LOW: `print()`-Statements, timing-unsichere Token-Vergleiche, Dev/Prod-Cookie-Handling
- 🎨 FE: Password-Reset-UI fehlt komplett

## Übersicht

| Sprint | Fokus | SD | USs |
|---|---|---|---|
| **1** | Vorbereitung & ADR | 0,5 | — |
| **2** | 🔴 CRITICAL + 🟠 HIGH | 6 | US-01 … US-04 |
| **3** | 🟡 MEDIUM (BE-only) | 5 | US-05 … US-08 |
| **4** | 🟢 LOW + FE + Doku | 5 | US-08b, US-09 … US-12 |
| **Gesamt** | | **16,5 SD** | |

## Agent-RACI

| Rolle | Kurz | Verantwortung |
|---|---|---|
| 🧭 SM | scrum-master | Sprint-Cadence, Daily, Retro, Merge-Freigabe |
| 🎯 PO | product-owner | Backlog, US-Akzeptanzkriterien, Prioritäten |
| 🏗️ TL | tech-lead | ADRs, Threat-Kapitel, komplexe Design-Entscheidungen |
| 🔁 TDD | tdd-guide | RED-GREEN-REFACTOR erzwingen, Test-First coachen |
| 🧪 QA | qa-engineer | Testpläne, Integration/E2E, Coverage ≥ 80 %, adversariale Tests |
| ⚙️ BE | backend-dev | Implementierung Auth-Backend |
| 🎨 FE | frontend-dev | Password-Reset-UI (US-08b) |
| 🔐 SEC | security-reviewer | Review jeder US, BLOCK bei CRITICAL/HIGH |
| 📖 DOC | doc-updater | US-12, ADR-Support, API-Doku-Updates |

**Hinweis**: Pentester-Role ist aus dem Plan entfernt (API-Filter-Konflikte). Threat-Modell liegt als Kapitel im ADR (🏗️ TL), adversariale Szenarien laufen als Integrationstests (🧪 QA).

## Definition of Done (pro US)

- [ ] TDD: Tests zuerst geschrieben (RED vor GREEN)
- [ ] Coverage ≥ 80 % (`pytest --cov=src`)
- [ ] Security-Review durch 🔐 SEC (keine CRITICAL/HIGH offen)
- [ ] Code-Review durch 👁️ (code-reviewer) — Approve
- [ ] Linting clean: `ruff check`, `black --check`, `mypy`
- [ ] Branch: `feature/us-XX-kurzbeschreibung`
- [ ] Commit: Conventional Commit + `— US-XX` Suffix
- [ ] PR-Kaskade gegen vorherigen Feature-Branch, am Sprint-Ende nach `main`
- [ ] README aktualisiert bei API-Change
- [ ] ADR aktualisiert bei Design-Decision

## Git-Workflow

- NIEMALS direkt auf `main` committen
- Pro US ein eigener Branch, kaskadierend (US-02 brancht von US-01, etc.)
- Am Sprint-Ende: letzten Branch → `main` merge, Feature-Branches löschen
- Details: `~/.claude/rules/common/git-workflow.md`

---

# Sprint 1 — Vorbereitung & ADR (½ Tag)

**Ziel**: Bedrohungsmodell klären, Plan finalisieren, Agents briefen, ADR als Single Source of Truth.

| Step | Agent | Output |
|---|---|---|
| 1.1 Kickoff + Scope | 🧭 SM | Sprint-Ziele kommuniziert |
| 1.2 ADR + Threat-Kapitel | 🏗️ TL | `docs/adr/00X-auth-hardening.md` inkl. STRIDE-ähnlicher Risiko-Analyse pro US |
| 1.3 Backlog refine | 🎯 PO | US-01 … US-08b finalisiert |
| 1.4 Teststrategie + adversariale Szenarien | 🧪 QA | Testplan pro US mit expliziten Angreifer-Szenarien |

**DoD Sprint 1**: ADR liegt ab, alle USs haben ACs + adversariale Test-Szenarien, Team hat Briefing.

---

# Sprint 2 — 🔴 CRITICAL + 🟠 HIGH

## US-01 · JWT-Secret in Settings-System überführen 🔴 CRITICAL

**Branch**: `feature/us-01-jwt-secret-settings`
**Komplexität**: MEDIUM · **Dependencies**: keine · **SD**: 1,5

**Story**: Als Betreiber möchte ich, dass `JWT_SECRET` über Pydantic-Settings geladen wird und die App bei fehlendem/Default-Secret nicht startet, damit Token-Fälschung in Produktion unmöglich wird.

**Akzeptanzkriterien**:
1. `JWT_SECRET` liegt als `SecretStr` in `Settings` (`src/config.py`)
2. Kein Fallback-Default mehr — leer → Startup-Fehler mit klarer Meldung (ohne Secret zu loggen)
3. `Settings._validate_smtp`-Muster wird für JWT adaptiert (`_validate_jwt`)
4. `jwt_utils.py` lädt Secret via `get_settings()`, nicht mehr `os.environ`
5. Test `test_settings.py`: fehlendes Secret → `ValueError`; vorhandenes Secret → OK
6. Test `test_jwt_utils.py`: Token round-trip mit Settings-Secret
7. Regression-Test: `JWT_SECRET` taucht in keinem Log-Output auf (auch nicht bei Crash)
8. Coverage ≥ 80 %

**Agents**: 🎯 PO (ACs finalisieren) → 🧪 QA (Tests RED) → ⚙️ BE (GREEN) → 🔁 TDD (REFACTOR) → 🔐 SEC → 👁️ CR → 🧭 SM

---

## US-02 · Refresh-Token-Rotation + Replay-Detektion 🟠 HIGH

**Branch**: `feature/us-02-refresh-rotation`
**Komplexität**: HIGH · **Dependencies**: US-01 · **SD**: 2

**Story**: Als User möchte ich, dass jeder Refresh-Call einen neuen Refresh-Token ausgibt und der alte sofort revoked wird, damit ein gestohlener Token maximal einmal nutzbar ist.

**Akzeptanzkriterien**:
1. `auth_service.refresh()` revoked den benutzten Refresh-Token (`revoked_at = now`)
2. `auth_service.refresh()` persistiert einen neuen Token-Hash in `user_refresh_tokens`
3. Router `/refresh` gibt den neuen Refresh-Token via Cookie zurück (`_set_refresh_cookie`)
4. **Replay-Erkennung**: Wird ein bereits revoketer Refresh-Token erneut präsentiert → **alle** Refresh-Tokens des Users revoken (Session-Kill)
5. Audit-Log: `refresh_token_replay_detected` mit `user_id`, `ip`, `user_agent`
6. Security-Notification an User bei Replay-Erkennung (`send_security_notification`)
7. Kein Schema-Change nötig (Schema unterstützt Rotation bereits)
8. Integrationstest: happy path, abgelaufener Token, revoketer Token, Replay-Szenario
9. Adversarial: 3× Replay in Folge → alle Sessions tot, Audit-Event, User-Notification
10. Coverage ≥ 80 %

**Agents**: 🏗️ TL (Design Doc) → 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

**Komplexitäts-Hinweis**: HIGH → Parallel-Dev aktivieren: 2 BE-Agents mit unterschiedlichen Ansätzen für Replay-Handling, dann 🏗️ TL synthesisiert.

---

## US-03 · Username- & Email-Enumeration entfernen 🟠 HIGH

**Branch**: `feature/us-03-no-enumeration`
**Komplexität**: MEDIUM · **Dependencies**: keine · **SD**: 1,5

**Story**: Als Sicherheitsverantwortlicher möchte ich, dass Registration und Password-Reset keine Rückschlüsse auf existierende Accounts zulassen.

**Akzeptanzkriterien**:
1. `/register` gibt **immer** dieselbe Response zurück: `201 { "message": "If this email is available, a verification mail has been sent.", "user_id": null|id }` — kein 409 mehr nach außen
2. Intern: Silent Return bei Duplicate (kein Error-Logging, das Enumeration leakt)
3. Username uniqueness prüfen, aber Fehlermeldung generisch halten (selbe Nachricht wie Email)
4. Password-Reset bleibt generisch (schon korrekt) — Regression-Test hinzufügen
5. Integrationstest: 2x Registration derselben Email → beide Male identische Response
6. Adversarial: Dictionary mit 100 Emails/Usernames → Response-Verteilung zeigt keine Information
7. Timing: Response-Zeit bei Duplicate vs. Non-Duplicate soll statistisch ähnlich sein (±50ms)
8. Coverage ≥ 80 %

**Agents**: 🎯 PO (AC schärfen: internes Verhalten) → 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

**Hinweis**: Kann parallel zu US-01 laufen.

---

## US-04 · Persistentes Rate-Limit + Resend-Verification 🟠 HIGH

**Branch**: `feature/us-04-persistent-ratelimit-resend`
**Komplexität**: HIGH · **Dependencies**: US-03 · **SD**: 1

**Story**: Als Betreiber möchte ich ein Rate-Limit, das über Neustarts/Worker-Skalierung hinweg wirkt, und einen Endpunkt zum erneuten Versand der Verifikationsmail.

**Akzeptanzkriterien**:
1. In-Memory-Dict `_registration_attempts` in `auth.py` entfernt
2. Neues persistentes Store: **Redis** (bevorzugt) oder DB-Tabelle `rate_limit_attempt` mit TTL — Entscheidung in ADR
3. Limit-Key: `(ip, email)` kombiniert
4. Neuer Endpoint `/api/auth/resend-verification`:
   - akzeptiert Email
   - rate-limited (3/h pro Email+IP)
   - sendet neues Verifikations-Token
   - logged als Audit-Event
5. Integrationstest: 4 Requests in Folge → 429; nach TTL → OK wieder
6. Adversarial: 50 Requests mit wechselnden IPs, gleicher Email → Limit greift trotzdem
7. Coverage ≥ 80 %

**Agents**: 🏗️ TL (Redis-vs-DB-Entscheidung) → 🧪 QA → ⚙️ BE → 🧪 QA (Last-Test) → 🔐 SEC → 👁️ CR

---

# Sprint 3 — 🟡 MEDIUM (BE-only)

**Ziel**: Defense-in-Depth für Auth-Backend. Keine FE-Abhängigkeiten, alle USs können parallel oder sequenziell abgearbeitet werden.

## US-05 · Adaptive Login-Lockout 🟡 MEDIUM

**Branch**: `feature/us-05-adaptive-login-lockout`
**Komplexität**: HIGH · **Dependencies**: US-04 (shared persistent store) · **SD**: 1,5

**Story**: Als User möchte ich nach mehreren Fehleingaben progressiv gebremst werden, ohne dass mich ein Angreifer aussperren kann.

**Akzeptanzkriterien**:
1. Progressives Backoff auf **IP-Basis** (nicht Account-basiert — kein Lockout-Angriff möglich): 5/min → 3/min → 1/min nach eskalierenden Fehlversuchen
2. Lockout-Counter in persistentem Store (Redis/DB, aus US-04)
3. Erfolgsloser Login erhöht Counter, erfolgreicher setzt ihn zurück
4. Audit-Event: `login_lockout_triggered` mit `ip`, `email_attempted`, `user_agent`
5. Antwort bei Lockout: generisch `429 { "detail": "Too many attempts." }` — keine Information über Account-Existenz
6. Integrationstest: Backoff-Kurve nach 15 Fehlversuchen
7. Adversarial: Distributed Brute-Force (100 IPs, 1 Ziel-Account) — wie wird das mitigiert? (ADR-Entscheidung)
8. Coverage ≥ 80 %

**Agents**: 🏗️ TL (Backoff-Kurve designen) → 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

## US-06 · Logout-all Sessions 🟡 MEDIUM

**Branch**: `feature/us-06-logout-all-sessions`
**Komplexität**: MEDIUM · **Dependencies**: keine · **SD**: 1

**Story**: Als User möchte ich alle meine aktiven Sessions auf einmal beenden können (z. B. bei vermuteter Kompromission).

**Akzeptanzkriterien**:
1. Neuer Endpoint `/api/auth/logout-all` (auth-required via Access-Token)
2. `auth_service.logout_all(user_id)` → setzt `revoked_at = now` auf **alle** aktiven Refresh-Tokens des Users
3. Audit-Event: `all_sessions_revoked` mit `user_id`, `ip`, `session_count`
4. Response: `{ "message": "All sessions revoked.", "count": <n> }`
5. Integrationstest: Login auf 2 "Geräten" (2 Refresh-Tokens) → logout-all → beide revoked
6. Coverage ≥ 80 %

**Agents**: 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

## US-07 · Email-Change-Token verschlüsseln 🟡 MEDIUM

**Branch**: `feature/us-07-encrypted-email-change-token`
**Komplexität**: MEDIUM · **Dependencies**: US-01 (Settings-System) · **SD**: 1,5

**Story**: Als Entwickler möchte ich, dass die neue Email-Adresse nicht im JWT-Klartext steht, sondern nur als verschlüsselter Payload.

**Akzeptanzkriterien**:
1. `email_change`-Token wird mit `cryptography.fernet` verschlüsselt
2. Fernet-Key via Settings (`SecretStr`, ENV: `EMAIL_CHANGE_KEY`)
3. Decode-Flow entschlüsselt zuerst, dann JWT-Verify
4. Migration: Bestehende Tokens werden ungültig (in ADR dokumentieren)
5. Test: Token enthält im Klartext nicht die neue Email (Byte-Level-Prüfung)
6. Regression: alte unverschlüsselte Tokens → sauberer `InvalidTokenError`
7. Coverage ≥ 80 %

**Agents**: 🏗️ TL (Key-Rotation-Strategie) → 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

## US-08 · Verification-Resend mit silent Handling 🟡 MEDIUM

**Branch**: `feature/us-08-resend-silent`
**Komplexität**: LOW · **Dependencies**: US-04 · **SD**: 1

**Story**: Als nicht-verifizierter User möchte ich eine neue Verifikationsmail anfordern können, ohne dass Angreifer erfahren, ob die Email registriert ist.

**Akzeptanzkriterien**:
1. `/resend-verification` gibt **immer** zurück: "If this email is registered and unverified, a new mail has been sent."
2. Intern: kein Leak über Existenz/Status via Response-Timing (konstante Antwortzeit anstreben, ±50ms)
3. Rate-Limit aus US-04 greift (3/h pro Email+IP)
4. Nur verifizierte Users → keine neue Mail (aber gleiche Response)
5. Audit-Event: `verification_resend_requested`
6. Adversarial: Timing-Test bei 100 registered vs. 100 unknown Emails — statistisch kein Unterschied
7. Coverage ≥ 80 %

**Agents**: 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

# Sprint 4 — 🟢 LOW + FE + Doku

**Ziel**: Letzte Härtungen, Password-Reset-UI, Logging-Aufräumung, Dokumentation.

## US-08b · Password-vergessen UI-Flow 🎨 FE

**Branch**: `feature/us-08b-password-reset-ui`
**Komplexität**: MEDIUM · **Dependencies**: keine (Backend fertig) · **SD**: 1,5

**Story**: Als User, der sein Passwort vergessen hat, möchte ich über die UI einen Reset-Link anfordern und nach Klick auf den Mail-Link ein neues Passwort eingeben können.

**Akzeptanzkriterien**:

### Seite 1: "Passwort vergessen?" (`/forgot-password`)
1. Link auf Login-Seite: "Passwort vergessen?"
2. Formular: Email-Eingabefeld + "Reset-Link senden"-Button
3. Nach Submit: **Immer** dieselbe Success-Nachricht ("Falls die Email registriert ist, wurde ein Link gesendet") — keine Enumeration
4. API-Call: `POST /api/auth/request-password-reset`
5. Error-Handling: Netzwerk-Fehler anzeigen, aber keine Info über Account-Existenz

### Seite 2: "Neues Passwort setzen" (`/reset-password?token=...`)
6. Route akzeptiert `?token=<jwt>` als Query-Parameter
7. Formular: "Neues Passwort" + "Passwort bestätigen" + "Speichern"-Button
8. Client-seitige Validierung: Passwort-Stärke (mind. 8 Zeichen, Upper/Lower/Digit/Special) + beide Felder müssen übereinstimmen
9. API-Call: `POST /api/auth/reset-password` mit `{ token, new_password }`
10. Success: Redirect zu Login-Seite mit Toast "Passwort erfolgreich zurückgesetzt"
11. Error-Handling: Token abgelaufen → "Link ist abgelaufen, bitte neu anfordern"; Token ungültig → "Ungültiger Link"; Passwort zu schwach → Validierungs-Fehler
12. Token-Feld: **Nicht** in URL-History oder Logs sichtbar machen (nach Submit `history.replaceState`)

### Security + UX
13. CSRF-Schutz via SameSite=strict Cookie
14. Rate-Limit-UI: Nach Submit Button disablen + Countdown
15. Loading-States während API-Calls
16. Accessibility: Labels, ARIA-Attributes, Keyboard-Navigation
17. Mobile-responsive

**Agents**: 🎨 FE → 🧪 QA (Integrationstest mit Mock-Backend) → 🔐 SEC → 👁️ CR

---

## US-09 · Strukturierte Logging 🟢 LOW

**Branch**: `feature/us-09-structured-logging`
**Komplexität**: LOW · **Dependencies**: keine · **SD**: 0,5

**Story**: Als Operator möchte ich strukturierte Logs (JSON), damit ich Auth-Events in SIEMs filtern kann.

**Akzeptanzkriterien**:
1. `logging`-Modul statt `print()` in allen Auth-Modulen
2. Log-Format: JSON (via `python-json-logger` oder stdlib-Konfig)
3. Log-Levels: WARN bei verdächtigen Events, INFO bei normalen Flows, DEBUG nur in Dev
4. Regression-Test: SecretStr darf nicht im Log-Output landen
5. `LOG_LEVEL` via Settings konfigurierbar
6. Coverage ≥ 80 %

**Agents**: 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

## US-10 · Timing-safe Token-Vergleich 🟢 LOW

**Branch**: `feature/us-10-timing-safe-token`
**Komplexität**: LOW · **Dependencies**: keine · **SD**: 0,25

**Story**: Als Entwickler möchte ich, dass Token-Hashes timing-sicher verglichen werden, um theoretische Side-Channel-Angriffe auszuschließen.

**Akzeptanzkriterien**:
1. `auth_service.py:21` `_hash_token` Vergleich nutzt `hmac.compare_digest`
2. `password_reset_service.py:23` ebenfalls
3. Unit-Test: gleicher Hash → True, unterschiedlicher Hash → False
4. Coverage ≥ 80 %

**Agents**: 🧪 QA → ⚙️ BE → 👁️ CR

---

## US-11 · Cookie-Secure Dev/Prod + CSRF-Ergänzung 🟢 LOW

**Branch**: `feature/us-11-cookie-secure-csrf`
**Komplexität**: LOW · **Dependencies**: US-01 (Settings) · **SD**: 0,5

**Story**: Als Entwickler möchte ich im Dev-Modus ohne HTTPS arbeiten können, ohne in Prod unsichere Cookies zu riskieren.

**Akzeptanzkriterien**:
1. `cookie_secure`-Default aus `Settings` ableiten: `base_url.startswith("https://")` → True, sonst False
2. Explizite Überschreibbarkeit via ENV (`COOKIE_SECURE=0/1`)
3. Optional: CSRF-Token via `double-submit`-Pattern für `/refresh`
4. Dev/Prod-Matrix in Doku
5. Test: Dev-Settings → `secure=False`; Prod-Settings → `secure=True`
6. Coverage ≥ 80 %

**Agents**: 🧪 QA → ⚙️ BE → 🔐 SEC → 👁️ CR

---

## US-12 · ADRs + Security-Runbook 📖 Doku

**Branch**: `feature/us-12-auth-docs`
**Komplexität**: LOW · **Dependencies**: alle vorherigen USs · **SD**: 0,75

**Story**: Als neues Team-Mitglied möchte ich die Auth-Architektur und Incident-Response nachvollziehen können.

**Output**:
1. `docs/adr/00X-auth-hardening.md` (alle Entscheidungen dieses Sprints, inkl. Threat-Kapitel)
2. `docs/runbooks/auth-incident.md`:
   - Refresh-Replay erkannt → alle Sessions killen
   - JWT-Secret kompromittiert → Rotation + Re-Issue
   - Brute-Force erkannt → IP blockieren
3. `docs/api/auth.md` aktualisieren (neue Endpoints: `/resend-verification`, `/logout-all`)
4. README.md mit aktualisierten ENV-Variablen (`JWT_SECRET`, `EMAIL_CHANGE_KEY`, ggf. `REDIS_URL`)

**Agents**: 📖 DOC + 🔐 SEC (Review)

---

# Risiko-Buffer

| Risiko | Mitigation |
|---|---|
| US-02 (Refresh-Rotation) komplexer als gedacht | 🏗️ TL vorab Design-Doc, Parallel-Dev mit 2 BE-Agents |
| Persistentes Rate-Limit (US-04) braucht Redis-Setup | 🏗️ TL entscheidet in Sprint 1: Redis (wenn vorhanden) oder DB |
| Enumeration-Änderung (US-03) bricht FE-Expectations | 🎯 PO klärt mit FE-Team vorab; Migration-Plan |
| JWT-Secret-Rotation (US-01) in Prod = Downtime | 📖 Runbook: Dual-Secret-Phase während Rollout |
| FE-Team erst in Sprint 4 verfügbar | US-08b könnte bei Bedarf in Sprint 3 parallel (FE-Stream) vorgezogen werden |

---

# Nächster Schritt

**Sprint 1 anstoßen**: 🧭 SM + 🏗️ TL + 🎯 PO + 🧪 QA parallel spawnen
- 🎯 PO finalisiert US-01 … US-08b mit Akzeptanzkriterien
- 🏗️ TL entscheidet über persistentes Rate-Limit (Redis vs. DB) + schreibt ADR
- 🧪 QA definiert adversariale Tests pro US
