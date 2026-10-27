# ADR 001: Auth-Härtung — Defense-in-Depth

**Status**: Proposed
**Datum**: 2026-10-26
**Deciders**: 🏗️ Tech Lead, 🎯 Product Owner, 🔐 Security Reviewer

## Context

Das Auth-System (Sprint 2) ist funktional vollständig, aber ein Security-Review (2026-10-26) hat mehrere Schwachstellen identifiziert:

- 🔴 **CRITICAL**: `JWT_SECRET` mit unsicherem Default (`"dev-secret-change-in-production"`) außerhalb des Settings-Systems
- 🟠 **HIGH**: Keine Refresh-Token-Rotation (gestohlener Token = 7 Tage Zugriff), Username/Email-Enumeration via 409 bei Registration, In-Memory-Rate-Limit (geht bei Neustart/Scaling verloren)
- 🟡 **MEDIUM**: Kein adaptiver Lockout, kein Logout-all, Email-Change-Token enthält `new_email` im Klartext
- 🟢 **LOW**: `print()` statt `logging`, timing-unsichere Token-Vergleiche, Dev/Prod-Cookie-Handling

Ziel: **Defense-in-Depth** über 4 Sprints, 16,5 Story Points, 12 User Stories (US-01 bis US-12 inkl. US-08b FE).

## Threat Analysis (STRIDE-ähnlich)

### US-01: JWT-Secret in Settings

- **Bedrohung**: JWT-Secret ist in Prod nicht gesetzt → alle Tokens fälschbar
- **Angriffsvektor**: Angreifer nutzt Default-Secret `"dev-secret-change-in-production"` aus `jwt_utils.py:9`, mint eigene Access/Refresh/Password-Reset-Tokens
- **Mitigation**: Secret in Pydantic-Settings als `SecretStr`, Startup-Fail bei leerem Wert, kein Fallback-Default
- **Restrisiko**: Secret Leak via ENV-Var oder Config-File → bleibt grundsätzlich möglich, aber kein "stiller" Default mehr

### US-02: Refresh-Token-Rotation

- **Bedrohung**: Gestohlener Refresh-Token bleibt 7 Tage nutzbar
- **Angriffsvektor**: XSS/Netzwerk-Sniffing → Refresh-Token aus Cookie extrahieren → bis zu 7 Tage Zugriff auf User-Account
- **Mitigation**: Rotation bei jedem Refresh-Call (alter Token revoked, neuer ausgestellt); Replay-Erkennung revokt alle User-Sessions
- **Restrisiko**: Replay-Erkennung schlägt fehl wenn Angreifer Token nutzt bevor legitimer User refresht → User-Session wird gekillt (DoS auf legitimen User)

### US-03: Enumeration entfernen

- **Bedrohung**: Angreifer enumeriert Emails/Usernamen
- **Angriffsvektor**: Dictionary-Attack auf `/register` → 409 "Email already registered" / 409 "Username already taken" → vollständige User-Liste
- **Mitigation**: Immer 201 mit generischer Nachricht ("If this email is available..."), Username-Check intern beibehalten aber gleiche Response
- **Restrisiko**: Timing-Side-Channel (Duplicate vs. Non-Duplicate) → mitigiert durch konstante Antwortzeit (±50ms)

### US-04: Persistentes Rate-Limit

- **Bedrohung**: In-Memory-Rate-Limit überlebt Neustart/Scaling nicht
- **Angriffsvektor**: Angreifer triggert 1000 Registrierungen → bei jedem Restart verschwindet Counter → Rate-Limit wirkungslos; hinter Load-Balancer mit 10 Workern teilt sich jeder Worker den In-Memory-Counter
- **Mitigation**: Persistente DB-Tabelle `rate_limit_attempt` mit TTL; Key `(ip, email)` kombiniert
- **Restrisiko**: DB-Query pro Request → Latenz-Spike bei hoher Last; mitigierbar durch Index auf `(key, expires_at)`

### US-05: Adaptiver Login-Lockout

- **Bedrohung**: Brute-Force auf Login-Endpunkt
- **Angriffsvektor**: 5/min Rate-Limit ist hart → legitimer User nach 5 Tippfehlern gesperrt; Angreifer mit 100 IPs umgeht IP-basiertes Limit
- **Mitigation**: Progressives Backoff auf IP-Basis (5/min → 3/min → 1/min); Account-basiertes Limit würde Account-Lockout-DoS ermöglichen
- **Restrisiko**: Distributed Brute-Force (100 IPs, 1 Ziel) → mitigierbar durch Captcha nach 10 Fehlversuchen (nicht in diesem Sprint)

### US-06: Logout-all Sessions

- **Bedrohung**: Keine Möglichkeit, alle Sessions zu killen bei Verdacht auf Kompromission
- **Angriffsvektor**: Angreifer hat Refresh-Token gestohlen → User kann nicht alle Sessions beenden
- **Mitigation**: `/api/auth/logout-all` revokt alle Refresh-Tokens des Users
- **Restrisiko**: Access-Token (15 min TTL) bleibt gültig → kurzzeitiges Zeitfenster nach Logout-all

### US-07: Email-Change-Token Encryption

- **Bedrohung**: `new_email` steht im JWT-Klartext
- **Angriffsvektor**: JWT ist Base64-encoded → jeder Proxy/Log/Eavesdropper sieht neue Email-Adresse bevor User sie bestätigt
- **Mitigation**: Fernet-Verschlüsselung des JWT-Payloads; Key via Settings
- **Restrisiko**: Fernet-Key Leak → alle Tokens entschlüsselbar; mitigierbar durch Key-Rotation (nicht in diesem Sprint)

### US-08: Verification-Resend silent

- **Bedrohung**: Enumeration via `/resend-verification`
- **Angriffsvektor**: Angreifer prüft für 1000 Emails ob "registered and unverified" → User-Liste
- **Mitigation**: Immer gleiche Response ("If this email is registered and unverified..."); konstante Antwortzeit
- **Restrisiko**: Timing-Side-Channel → mitigierbar durch `time.sleep()` bei Unknown-Email (nicht in diesem Sprint)

### US-08b: Password-Reset-UI

- **Bedrohung**: Token-Leak via URL-History oder Logs
- **Angriffsvektor**: User klickt Reset-Link → Token in Browser-History → Angreifer mit Zugriff auf Browser kann Token nutzen
- **Mitigation**: `history.replaceState` nach Submit; Token nicht in Logs
- **Restrisiko**: CSRF-Angriff auf Reset-Form → mitigierbar durch SameSite=strict Cookie (bereits gesetzt)

### US-09/10/11/12: LOW-Priority

- **US-09 (Logging)**: `print()` → `logging`-Modul; SecretStr darf nicht geloggt werden
- **US-10 (Timing-safe)**: `hmac.compare_digest` für Token-Hash-Vergleich; mitigiert theoretischen Timing-Side-Channel
- **US-11 (Cookie-Secure)**: Auto-Ableitung aus `base_url` + ENV-Override; mitigiert Dev/Prod-Verwechslung
- **US-12 (Doku)**: ADR + Runbook; keine direkte Threat-Mitigation, sondern Operational Readiness

## Design-Entscheidungen

### DEC-01: Persistentes Rate-Limit — DB-Tabelle (nicht Redis)

**Decision**: Wir nutzen eine PostgreSQL-Tabelle `rate_limit_attempt` mit TTL, kein Redis.

**Rationale**:
- Kein Redis im Projekt (geprüft: `grep -r "redis" .` → keine Treffer)
- Redis würde neue Dependency, neues Deployment-Target, neue Monitoring-Requirement bedeuten
- DB-Tabelle ist simpler: ein `INSERT ... ON CONFLICT DO UPDATE` mit `expires_at < NOW()` Cleanup
- Performance: Bei <1000 Requests/sec ist DB schnell genug; Index auf `(key, expires_at)` verhindert Full-Table-Scan

**Trade-offs**:
- ✅ Kein neues Infrastruktur-Dependency
- ✅ Einheitliches Deployment (nur PostgreSQL)
- ✅ Einfacher zu testen (in-memory SQLite für pytest)
- ❌ DB-Query pro Request → Latenz-Spike bei hoher Last
- ❌ Keine atomaren Counters (Redis `INCR` ist atomar, DB-Query nicht)

**Migration**: `v003_rate_limit.sql` erstellt Tabelle `rate_limit_attempt(id, key, expires_at, created_at)` mit Index.

### DEC-02: Refresh-Replay-Strategie — Familie revoken (aggressiv)

**Decision**: Bei Replay-Erkennung werden **alle** Refresh-Tokens des Users revokt + Security-Notification an User.

**Rationale**:
- Aggressive Strategie ist sicherer: Angreifer verliert Zugriff, User muss sich neu authentifizieren
- Milde Strategie (nur einen Token ignorieren) lässt Angreifer weiterhin zugreifen wenn er mehrere Tokens gestohlen hat
- User-Notification ist kritisch: User weiß sofort, dass Account kompromittiert war

**Trade-offs**:
- ✅ Höchste Sicherheit: Angreifer verliert sofort Zugriff
- ✅ User wird informiert (Security-Notification)
- ❌ False Positive (z.B. Race Condition bei parallelen Refresh-Calls) → User wird aus allen Sessions geworfen
- ❌ DoS-Potenzial: Angreifer kann Replay triggern um legitimen User auszuloggen

**Mitigation**: Audit-Log `refresh_token_replay_detected` mit `ip`, `user_agent` → User kann sehen ob Replay von anderer IP kam.

### DEC-03: Adaptives Backoff — IP-basiert, 5/min → 3/min → 1/min

**Decision**: Lockout basiert auf IP-Adresse, nicht auf Account. Backoff-Kurve: 5 Requests/min → 3 Requests/min → 1 Request/min nach eskalierenden Fehlversuchen.

**Rationale**:
- IP-basiert verhindert Account-Lockout-DoS (Angreifer kann legitimen User nicht aussperren)
- Account-basiert wäre gefährlich: Angreifer tippt 100× falsches Passwort für Opfer-Account → Opfer kann sich nicht mehr einloggen
- Backoff-Kurve 5→3→1/min ist progressiv aber nicht zu hart: legitimer User kann nach 1-min Pause wieder versuchen

**Trade-offs**:
- ✅ Kein Account-Lockout-DoS möglich
- ✅ Progressiv: brute-force wird exponentiell langsamer
- ❌ Distributed Brute-Force (100 IPs, 1 Ziel) umgeht IP-basiertes Limit
- ❌ NAT/Shared IP (z.B. Firmennetzwerk) → alle User hinter IP betroffen

**Mitigation**: Captcha nach 10 Fehlversuchen (nicht in diesem Sprint, aber in ADR als Folge-Work dokumentiert).

### DEC-04: Email-Change-Token Encryption — Fernet, keine Key-Rotation

**Decision**: Wir nutzen `cryptography.fernet.Fernet` für Verschlüsselung. Fernet-Key via ENV `EMAIL_CHANGE_KEY`. Keine Key-Rotation im MVP.

**Rationale**:
- Fernet ist einfach, in `cryptography` enthalten (bereits Dependency für bcrypt)
- AES-GCM oder libsodium wären mächtiger, aber Overkill für diesen Use-Case
- Key-Rotation wäre schön, aber nicht kritisch: Email-Change-Tokens sind kurzlebig (24h TTL), Key-Leak ist selten

**Trade-offs**:
- ✅ Einfach zu implementieren
- ✅ Keine neue Dependency (Fernet ist Teil von `cryptography`)
- ❌ Keine Key-Rotation → bei Key-Leak müssen alle Tokens invalidiert werden
- ❌ Fernet ist langsamer als AES-GCM (aber irrelevant bei <100 Requests/sec)

**Migration**: Bestehende unverschlüsselte Email-Change-Tokens werden ungültig. User muss neuen Email-Change anstoßen.

### DEC-05: Cookie-Secure — ENV-Variable mit Auto-Ableitung

**Decision**: `COOKIE_SECURE` ist eine ENV-Variable mit Default abgeleitet aus `base_url`: `base_url.startswith("https://")` → `True`, sonst `False`. Explizite Überschreibung via `COOKIE_SECURE=0/1` möglich.

**Rationale**:
- Auto-Ableitung verhindert Dev/Prod-Verwechslung: Dev (localhost:8000) → `secure=False`, Prod (https://...) → `secure=True`
- ENV-Override erlaubt explizite Kontrolle: z.B. Prod hinter Reverse-Proxy mit HTTP-Terminierung → `COOKIE_SECURE=1` erzwingen
- Keine Magie: Default ist offensichtlich, Override ist explizit

**Trade-offs**:
- ✅ Dev funktioniert ohne HTTPS
- ✅ Prod ist sicher by default
- ✅ Override möglich für Edge-Cases
- ❌ Auto-Ableitung kann falsch sein wenn `base_url` nicht korrekt gesetzt ist

**Mitigation**: Startup-Log zeigt `cookie_secure=True/False` → Operator kann prüfen.

## Consequences

### Positive

- **Security**: CRITICAL-Lücke (JWT-Secret) geschlossen, Defense-in-Depth für alle Auth-Flows
- **Robustheit**: Persistentes Rate-Limit überlebt Neustart/Scaling; Refresh-Rotation limitiert Token-Lebensdauer
- **Operational Readiness**: ADR + Runbook → neues Team-Mitglied kann Auth-System nachvollziehen
- **Compliance**: Enumerations-Schutz, adaptive Lockout, Logout-all → erfüllt OWASP Top 10 (A07:2021 Identification and Authentication Failures)

### Negative

- **Komplexität**: Refresh-Replay-Erkennung + Logout-all + adaptive Lockout → mehr Code, mehr Edge-Cases
- **Performance**: Persistentes Rate-Limit (DB-Query pro Request) → Latenz-Spike bei hoher Last
- **Migration-Kosten**: Email-Change-Tokens werden invalidiert; User muss neuen Email-Change anstoßen
- **False Positives**: Replay-Erkennung kann legitimen User aus allen Sessions werfen (Race Condition)

### Migration-Hinweise

- **US-01 (JWT-Secret)**: Bestehende Tokens werden ungültig wenn Secret geändert wird → User müssen sich neu einloggen
- **US-02 (Refresh-Rotation)**: Keine Migration nötig (Schema unterstützt Rotation bereits)
- **US-07 (Email-Change-Token)**: Bestehende unverschlüsselte Tokens werden ungültig → User muss neuen Email-Change anstoßen
- **US-08b (Password-Reset-UI)**: Keine Migration nötig (Backend fertig, nur FE)

## Sprint-Execution-Modus

### Parallelität

Unabhängige USs können parallel über Branches laufen:

- **Track A**: US-01 → US-02 → US-07 → US-11 (JWT-Secret → Refresh-Rotation → Email-Encryption → Cookie-Secure)
- **Track B**: US-03 → US-04 → US-05 → US-08 (Enumeration → Rate-Limit → Lockout → Resend)
- **Track C**: US-06 (Logout-all, keine Dependencies)
- **Track D**: US-08b FE (Password-Reset-UI, keine Dependencies, kann parallel zu BE laufen)

### Dependencies

- US-02 nach US-01 (Refresh-Rotation braucht Settings-System)
- US-04 nach US-03 (Rate-Limit braucht Enumerations-Schutz)
- US-05 nach US-04 (Lockout braucht persistentes Rate-Limit)
- US-07 nach US-01 (Email-Encryption braucht Settings-System)
- US-08 nach US-04 (Resend braucht Rate-Limit)
- US-11 nach US-01 (Cookie-Secure braucht Settings-System)

### Merge-Reihenfolge

Pro Track: Branches kaskadieren (US-02 brancht von US-01, etc.). Am Sprint-Ende: letzten Branch → `main` merge, Feature-Branches löschen.

Details: `~/.claude/rules/common/git-workflow.md`

## Follow-Up Work (nicht in diesem Sprint)

- **Captcha nach 10 Login-Fehlversuchen**: Distributed Brute-Force Mitigation
- **Timing-Sleep bei Unknown-Email in `/resend-verification`**: Timing-Side-Channel Mitigation
- **Key-Rotation für Email-Change-Token**: Secret-Management-Verbesserung
- **Redis-Migration für Rate-Limit**: Wenn Last >1000 Requests/sec
