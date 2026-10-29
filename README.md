# HexToBinaryTrainer2 Backend

FastAPI backend for a hex-to-binary training application with comprehensive user
authentication, email verification, a hardened session lifecycle (rotating
refresh tokens, replay detection, persistent rate limiting, adaptive lockout,
audit log and security alert emails) — and an XP economy that turns answered
questions into `total_xp`, levels, streaks and content unlocks.

## Features

### Security Hardening (Sprint 3)
Defense-in-depth layered on top of the authentication system. Details in [Security Features](#security-features):
- **Refresh Token Rotation** — every `/api/auth/refresh` issues a fresh token and revokes the one presented
- **Replay Detection** — presenting an already-rotated token revokes **all** sessions of the account and writes an audit event (the user alert for it is prepared but not mailed yet, see [Security Notifications](#security-notifications-us-09))
- **Logout-All** — `POST /api/auth/logout-all` terminates every active session (suspected compromise)
- **Anti-Enumeration** — registration and resend-verification always return the same generic answer; the password is hashed even for a duplicate so both paths take the same time
- **Persistent Rate Limiting** — counters live in the Postgres table `rate_limit_attempt`, so limits survive restarts and are shared across workers
- **Adaptive Login Lockout** — progressive per-IP lockout (1 min → 5 min → 15 min) instead of a fixed hard wall
- **Encrypted Email-Change Tokens** — the new address is sealed in a Fernet envelope instead of a plaintext JWT claim
- **Security Notifications** — self-contained HTML alert templates for sensitive
  account changes (XSS-escaped, rate-limited; delivery hooked up for email
  changes, the remaining actions still need the transport wiring)
- **Mandatory JWT Secret** — `JWT_SECRET` is required (≥ 32 chars); the app refuses to start instead of falling back to a default

### Authentication System (Sprint 2)
- **User Registration** with email verification
- **Login/Logout** with JWT access tokens and secure refresh token cookies
- **Token Refresh** for seamless session continuation — rotated on every call
  since Sprint 3 (single-use refresh tokens)
- **Password Reset** via email with secure one-time tokens
- **Password Change** for authenticated users (invalidates all sessions)
- **Email Change** with verification and audit logging
- **Username Change** with 7-day cooldown limit
- **Audit Logging** for all critical security actions
- **Security Notifications** via email for sensitive operations (templates ready,
  delivery partially wired — see [Security Notifications](#security-notifications-us-09))
- **Multi-Provider Ready** schema (prepared for OAuth integration)
- **Rate Limiting** on authentication endpoints — persistent (DB-backed) since
  Sprint 3

### Game Economy (Sprint 4)
- **XP Economy**: one `POST /api/game/answer` records an answer and returns the full
  economy state — signed `xp_delta`, `total_xp`, derived `level`, streak counters and
  the `newly_unlocked` delta (see [Game Endpoints](#game-endpoints))
- **Level Curve + Content Unlocks**: level is derived from `total_xp`; each
  `(mode, bit_width)` pair is playable from a minimum level, enforced server-side
  with `403` and the required level in the message
- **Leaderboard**: Top N users sorted by `total_xp` (DESC), tiebroken by fewer
  mistakes then `user_id` — public, no authentication. `limit` defaults to 50 and is
  bounded 1–100
- **Rate Limited**: 120 answers per user per 60 s window, `429` with `Retry-After`

The Sprint 1 `+bits` / `-4×bits` scoring scheme is **gone** (US-34, Sprint 4 close): both
of its `POST` routes under `/api/game` and the counters table behind them were deleted with
no deprecated alias. `POST /api/game/answer` is the economy's only write path now. The
schema change shipped as two migrations — `v004` (additive, creates `user_game_profile`) and
`v005` (drops the legacy table) — and no data was backfilled (the database was empty).

Numbers, curves and the golden XP matrix live in
[docs/xp-economy.md](docs/xp-economy.md).

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL 15+ (async via asyncpg)
- **ORM**: SQLAlchemy 2.0 (async)
- **Validation**: Pydantic v2
- **Authentication**: JWT (PyJWT) with bcrypt password hashing
- **Email**: SMTP (configurable, tested with Strato)
- **Testing**: pytest with pytest-asyncio, pytest-cov
- **Deployment**: Docker + Docker Compose
- **CI/CD**: GitHub Actions

## Architecture

For detailed architecture documentation, see [docs/architecture.md](docs/architecture.md).
It carries the Sprint-1 knowledge-graph community view (communities 1–5) plus the
Sprint-4 **Game Economy** community — layering, the three `/api/game` surfaces,
`user_game_profile` and the two-step v004/v005 migration strategy.

## Documentation

| Document | Contents |
|---|---|
| [docs/xp-economy.md](docs/xp-economy.md) | **Sprint 4 reference tables** — base XP, mode multipliers, streak tiers as rational pairs, golden XP matrix, penalty table, level curve + cumulative, unlock seed, `bit_width` semantics, and where each number lives in code |
| [docs/architecture.md](docs/architecture.md) | Module/community structure (incl. the Sprint-4 game economy), resolved code gaps, architecture decisions |
| [docs/adr/001-auth-hardening.md](docs/adr/001-auth-hardening.md) | Sprint 3 threat analysis + decisions DEC-01 … DEC-05 (DB rate limit, aggressive replay revocation, IP backoff, Fernet, cookie handling) |
| [docs/auth-hardening-plan.md](docs/auth-hardening-plan.md) | Security-hardening backlog (US-01 … US-12) with acceptance criteria |
| [docs/auth-hardening-test-plan.md](docs/auth-hardening-test-plan.md) | Adversarial test matrix per hardening story |
| [docs/sprint2-plan.md](docs/sprint2-plan.md) / [docs/sprint3-plan.md](docs/sprint3-plan.md) / [docs/sprint3-review.md](docs/sprint3-review.md) | Auth-system plan, production-readiness plan and its review (157 tests / 96,72 % at the time) |
| [docs/test-setup.md](docs/test-setup.md) | PostgreSQL test instance, `.env.test`, CI service container |
| [docs/backlog.md](docs/backlog.md) | Sprint-1 consolidation backlog (historical) |
| `http://localhost:8000/docs` | Live OpenAPI / Swagger UI — the authoritative reference for request/response schemas |

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- SMTP server for email sending (e.g., Strato, Gmail)
- uv (recommended) or pip

### Installation

```bash
# Clone repository
git clone https://github.com/habnixzutun/hexToBinaryTrainer2Backend.git
cd hexToBinaryTrainer2Backend

# Install dependencies with uv
uv sync

# Or with pip
pip install -e .

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration (see Configuration section)
```

### Configuration

Create a `.env` file with the following variables:

```env
# Database Configuration
DB_NAME=your_database
DB_USER=your_user
DB_PW=your_password
DB_HOST=localhost
DB_PORT=5432

# JWT Configuration (REQUIRED — startup fails without it)
# Generate a strong secret: python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET=your-strong-jwt-secret-here

# Email-Change Token Key (REQUIRED in production, ≥ 32 chars)
# Encrypts the new email address inside the verification token (Sprint 3 / US-07).
# The built-in default is only safe for the test suite.
# Generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"
EMAIL_CHANGE_KEY=your-email-change-key-here

# SMTP Configuration (REQUIRED for email notifications)
SMTP_HOST=smtp.strato.de
SMTP_PORT=587
SMTP_USER=your-email@your-domain.de
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=noreply@your-domain.de

# API Configuration
ALLOWED_ORIGINS=["http://localhost:5173"]
BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:5173

# Optional: Cookie security (set to false for local development without HTTPS)
COOKIE_SECURE=true
```

**Important:**
- `JWT_SECRET` is mandatory: empty, shorter than 32 characters, or the old
  default `dev-secret-change-in-production` aborts startup with a `ValueError`
  that never prints the secret itself
- `EMAIL_CHANGE_KEY` must be set to a value of ≥ 32 characters in production;
  rotating it invalidates every outstanding email-change token.
  **⚠️ `docker-compose.yaml` does not forward this variable to the `api` container
  yet** — until it does, a composed deployment runs on the hard-coded test-suite
  default key and every email-change token is decryptable by anyone with repository
  access. Add `EMAIL_CHANGE_KEY=${EMAIL_CHANGE_KEY}` to the `environment:` list
  (tracked as a follow-up in the roadmap)
- Configure SMTP credentials for email verification, password reset and security notifications;
  without them the app falls back to `MockEmailService` (development only)
- `BASE_URL` is used for the links inside verification/reset emails, `FRONTEND_URL`
  is where the HTML verification page redirects afterwards
- Set `COOKIE_SECURE=false` for local development without HTTPS
- All secret fields (`DB_PW`, `SMTP_PASSWORD`, `JWT_SECRET`, `EMAIL_CHANGE_KEY`) are
  `SecretStr`s — they are masked in logs and in validation error messages

## Running the Application

```bash
# Start the server
uvicorn src.main:app --reload

# Or with uv
uv run uvicorn src.main:app --reload
```

The API will be available at `http://localhost:8000`

Interactive API documentation: `http://localhost:8000/docs`

## API Endpoints

### Authentication Endpoints

#### Registration & Verification

**POST /api/auth/register**

Register a new user account.

**Request:**
```json
{
  "email": "user@example.com",
  "username": "username",
  "password": "Password123"
}
```

**Response:** `201 Created`
```json
{
  "message": "If this email is available, a verification mail has been sent.",
  "user_id": null
}
```

**Anti-Enumeration (US-03):** the answer is always identical — a duplicate
email or username returns the same `201` with the same message instead of a
`409`, and `user_id` is always `null`. Internally the password is hashed and the
verification token is created on **every** call, so the duplicate path and the
fresh path take the same time (bcrypt ≈ 190 ms dominates both). A race on the
unique constraints (`IntegrityError`) is handled as a duplicate too.

**Password Requirements:**
- 8 – 128 characters
- at least one uppercase letter, one lowercase letter, one digit **and** one special character

Weak passwords are the one case that still answers `400` with the concrete
reason, because the caller has to know which rule broke:
```json
{
  "detail": {
    "errors": [
      "Password must contain at least one digit.",
      "Password must contain at least one special character."
    ]
  }
}
```

**Rate Limiting:** 3 registrations per hour per IP address + email combination
(`429 Too Many Requests` when exceeded). Persisted in the `rate_limit_attempt`
table, so the limit survives restarts and is shared across workers.

---

**POST /api/auth/verify-email**

Verify email address with token from registration email.

**Request:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:** `200 OK`
```json
{
  "message": "Email verified successfully."
}
```
Already-verified accounts get `{"message": "Email already verified."}` — the call is idempotent.

**Errors:** `400` for an expired token, a wrong token type or an unknown identity.

> The same flow is available as a browser link: `GET /verify-email?token=…` renders an
> HTML result page and redirects to `FRONTEND_URL` on success. That is what the
> verification emails point at.

---

**POST /api/auth/resend-verification**

Send the verification email again for an unverified account. Silent handling:
the response is identical whether the address exists, is already verified, or
is unknown, so the endpoint cannot be used for email enumeration.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:** `200 OK`
```json
{
  "message": "If this email is registered and unverified, a new mail has been sent."
}
```

**Rate Limiting:** 3 resends per hour per IP address + email combination
(`429 Too Many Requests` when exceeded). Every request for a known address is
recorded as the `verification_resend_requested` audit event.

**Delivery failures are silent:** if the SMTP send throws, the error is swallowed
and the caller still gets the standard `200` message — the response must never
reveal whether a mail actually left the server.

---

#### Login & Session Management

**POST /api/auth/login**

Authenticate user and receive access token + refresh cookie.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "Password123"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Headers:** `Set-Cookie: refresh_token=<token>; HttpOnly; Secure; SameSite=Strict`

**Errors:** `401 Invalid credentials.` for a wrong password **and** for an
unknown email (identical answer, no account existence leak), `403 Account not
verified.` for an unverified account, `429` when rate-limited or locked out.

**Rate Limiting:** 5 login attempts per minute per IP address + email
combination (`429 Too Many Requests` when exceeded), backed by the persistent
`rate_limit_attempt` table

**Adaptive Lockout (US-05):** on top of the per-request limit, failed logins are
counted per IP address with progressively longer blocks — 1 minute after 5
failures, 5 minutes after 10, 15 minutes after 20. A successful login resets the
counter. `429` is returned while locked out.

---

**POST /api/auth/refresh**

Rotate the refresh token and return a fresh access token.

**Request:** Cookie `refresh_token=<token>` (sent automatically)

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Headers:** `Set-Cookie: refresh_token=<new token>; HttpOnly; Secure; SameSite=Strict`

**Rotation (US-02):** every call revokes the presented token and sets a new one,
so a refresh token is single-use. The client must accept the new cookie — with
`credentials: "include"` / `curl -b cookies.txt -c cookies.txt` this happens
automatically.

**Replay Detection (US-02, ADR 001 DEC-02):** presenting a token that was
already rotated (or otherwise revoked) is treated as theft. **All** refresh
tokens of the user are revoked, the audit event `refresh_token_replay_detected`
is written (with IP and user agent) and a security notification is dispatched
for the user (see [Security Notifications](#security-notifications-us-09) — the
email leg of that dispatch is not wired to the transport yet). The caller
receives:
```json
{
  "detail": "Security violation detected. All sessions have been revoked."
}
```
with status `401` (the router also clears the dead refresh cookie). Clients should
drop the cookie on *any* `401` from this endpoint and send the user to the login
page. Other failure modes answer `401 Invalid or expired refresh token.`

---

**POST /api/auth/logout**

Logout and revoke the presented refresh token.

**Request:** Cookie `refresh_token=<token>` (sent automatically)

**Response:** `200 OK`
```json
{
  "message": "Logged out."
}
```

Idempotent: an unknown, expired or already-revoked token still answers `200`.

---

**POST /api/auth/logout-all**

Revoke **all** active refresh tokens of the authenticated user (US-06) — the
kill-switch for a suspected account compromise. Every device has to
re-authenticate afterwards.

**Headers:** `Authorization: Bearer <access_token>`

**Response:** `200 OK`
```json
{
  "message": "All sessions revoked.",
  "count": 3
}
```

- `count` is the number of tokens revoked by this call. The endpoint is
  idempotent: a second call returns `count: 0` and writes no second audit entry.
- Audit event: `all_sessions_revoked` with `ip`, `user_agent` and
  `metadata.session_count`.
- Expired-but-not-yet-revoked tokens are counted too, so `count` matches the
  sessions the user actually had.
- **Scope:** only refresh tokens are revoked. The presented access token stays
  valid for up to 15 minutes (its TTL) — this is the accepted residual risk in
  ADR 001.
- Missing/invalid Bearer header → `401 Missing or invalid Authorization header.`

---

#### Password Reset

**POST /api/auth/request-password-reset**

Request password reset email.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:** `200 OK`
```json
{
  "message": "If this email is registered, a reset link has been sent."
}
```

**Security:** Always returns 200, even if the email doesn't exist (prevents email
enumeration). The mail contains a link to `{BASE_URL}/reset-password?token=…`;
the token is valid for **1 hour**, is stored only as a SHA-256 hash and is
single-use.

> **Note:** unlike `GET /verify-email`, the backend does **not** serve a
> `/reset-password` page — that path has to be provided by the frontend, and
> `BASE_URL` must point to wherever the page is reachable. The backend only owns
> the `POST /api/auth/reset-password` confirmation below.

---

**POST /api/auth/reset-password**

Reset password with token from reset email.

**Request:**
```json
{
  "token": "reset-token-from-email",
  "new_password": "NewPassword456"
}
```

**Response:** `200 OK`
```json
{
  "message": "Password reset successful."
}
```

**Errors:** `400 Reset token has expired.`, `400 Reset token has already been
used.` or `400 Invalid reset token.` (also for a token of the wrong type), plus
`400` with the strength `errors` list if the new password is too weak.

**Security:**
- All active refresh tokens are invalidated → every device must log in again
- The token is marked as used (`used_at`), so it cannot be replayed
- Audit log entry `password_reset`
- Security notification dispatched (see [Security Notifications](#security-notifications-us-09))

---

#### Current User

**GET /api/user/me**

Profile plus the Sprint-4 economy state of the authenticated user — the same nine
fields `GET /api/game/profile` reports for the numbers, read from the same
`user_game_profile` row. The counters are `0`/`level` 1 until the first answer is
submitted (that `user_game_profile` row is created lazily by
`POST /api/game/answer`; neither read inserts one).

**Headers:** `Authorization: Bearer <access_token>`

**Response:** `200 OK`
```json
{
  "id": "01234567-89ab-cdef-0123-456789abcdef",
  "username": "username",
  "email": "user@example.com",
  "correct_count": 42,
  "incorrect_count": 5,
  "total_xp": 1320,
  "level": 6,
  "current_streak": 3,
  "best_streak": 7
}
```

(The legacy `points` field is gone outright — no deprecated alias, removed with its
two endpoints in US-34.)

**Errors:** `401` for a missing/invalid/expired token, `404 User not found.` if
the token no longer belongs to an existing user.

---

#### User Management (Authenticated)

**PATCH /api/user/email**

Request email change (requires authentication).

**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "new_email": "newemail@example.com"
}
```

**Response:** `200 OK`
```json
{
  "message": "Verification email sent to new address."
}
```

**Errors:** `409 Email already registered.` if the address already belongs to a
user, `422` for an invalid address format.

**Security:** audit event `email_change_requested` (with the target address in
metadata) before the mail goes out; the confirmation token is Fernet-encrypted
(see below).

---

**POST /api/auth/verify-email-change**

Verify new email address with token from verification email.

**Request:**
```json
{
  "token": "gAAAAABk...Fernet-token-from-email"
}
```

**Response:** `200 OK`
```json
{
  "message": "Email updated successfully."
}
```

**Errors:** `400 Token has expired.`, `400 Invalid token.` (tampered, wrong key,
old plaintext token or wrong type) and `400 User not found.`

**Token format (US-07):** the token is `Fernet(JWT)`, i.e. the JWT (HMAC-SHA256)
inside a Fernet envelope (AES-128-CBC + HMAC-SHA256). The `new_email` claim is
therefore **not** readable in transit — the old plaintext JWT leaked the target
address to every proxy and mail log. The Fernet key is derived as
`base64url(SHA-256(EMAIL_CHANGE_KEY))`; the payload still carries `sub`, `type`,
`new_email`, `exp`, `iat` and keeps the 24 h TTL. Tokens issued before this
change are rejected as `Invalid token.` (clean `ValueError`, no crash) — affected
users just have to request the change again.

**Side effects:** updates `users.email` **and** the `email` identity's
`provider_user_id` (so login works with the new address), writes the audit event
`email_changed` with `old_email`/`new_email`, and sends the security alert to the
**old** address — the user may no longer control the new one.

---

**PATCH /api/user/username**

Change username (requires authentication, limited to once per 7 days).

**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "new_username": "newusername"
}
```

**Response:** `200 OK`
```json
{
  "message": "Username updated successfully."
}
```

**Username Requirements:**
- 3-50 characters (length only — no character-class restriction is enforced)
- Unique across all users → `409 Username already taken.`

**Rate Limiting:** Once per 7 days per user. Inside the cooldown the endpoint
answers `429` with the exact unlock time so the UI can show a countdown:
```json
{
  "detail": {
    "message": "Username can only be changed once every 7 days.",
    "next_change_at": "2026-11-03T14:22:01.512345+00:00"
  }
}
```

**Side effects:** sets `last_username_change_at`, writes the audit event
`username_changed` (metadata: `new_username`) and dispatches a security
notification.

---

**POST /api/user/change-password**

Change password while logged in (requires authentication).

**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "old_password": "OldPassword123",
  "new_password": "NewPassword456"
}
```

**Response:** `200 OK`
```json
{
  "message": "Password changed successfully. All sessions have been invalidated."
}
```

**Errors:** `401 Old password is incorrect.`, `400 New password does not meet
strength requirements.`, `400 New password must be different from old password.`,
`401 User identity not found.` (no email/password identity for the account)

**Security:**
- Validates old password before allowing change
- All active sessions are invalidated (bulk `UPDATE` on `user_refresh_tokens`)
- Audit log entry `password_change`
- Security notification dispatched (see [Security Notifications](#security-notifications-us-09))

---

### Game Endpoints

The `summary` of all three endpoints and every `Field` description of
`AnswerRequest`/`AnswerResponse` reach the generated OpenAPI document
(`http://localhost:8000/docs`), including the per-mode `bit_width` contract (bits in
every mode: 4/8/16/32 for the conversion modes, 8/16/24/32 for `unicode`). Two gaps are
known and deliberately **not** fixed here, because US-40 is documentation-only — the
error responses documented below are not declared in the decorator (so Swagger shows
only `200`/`422` on `/answer` and only `200` on `/profile`), and most
`ProfileResponse`/`LeaderboardEntryV2` fields carry no `Field(description=…)`. Both are
filed in [docs/backlog.md](docs/backlog.md) as **TD-03**.

**POST /api/game/answer**

Record one answer and return the caller's full economy state (requires
authentication). This is the economy's only write path: US-34 removed the two Sprint-1
scoring routes that used to sit beside it. Calculation reference:
[docs/xp-economy.md](docs/xp-economy.md).

**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "mode": "hex_to_bin",
  "bit_width": 8,
  "is_correct": true
}
```

`mode` ∈ `hex_to_bin` · `hex_to_dec` · `bin_to_dec` · `unicode`. `bit_width` is
**bits in every mode** (OQ-7): 4/8/16/32 for the three conversion modes, 8/16/24/32
for `unicode` (= 1–4 bytes). Anything else is a `422`, as is any extra field —
`extra="forbid"`, so a client-computed `total_xp` or a body `user_id` cannot be
smuggled in. The request body is capped at 4 KiB (`413`).

**Response:** `200 OK`
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

`xp_delta` is the **signed, post-clamp** change: positive on a correct answer,
negative on a penalty, and `0` when the level floor blocks the penalty (so a client
must not assume a nonzero delta on `is_correct: false`). `level` is derived from
`total_xp`, `xp_in_level` = `total_xp − cum(level)`, and
`xp_required_for_next_level` is the **span** to the next level, not a cumulative
threshold. `newly_unlocked` is the delta set of this answer (usually empty, including
on a level-up with no unlock row — the same `{mode, bit_width, min_level}` element
shape as `/profile`'s `unlocks`). XP is a multiple of 4 throughout.

The caller's profile row is created lazily by the first answer; concurrent answers
from one user serialize on a row lock, and one request is one transaction.

**Errors:** `401` no/invalid/expired token (checked before any SQL), `422` invalid
payload, `429` over the rate budget (120 answers / 60 s, with `Retry-After`),
`403` the combo is not unlocked at the caller's **pre-answer** level, `404 User
not found.` if the token's user no longer exists.

---

**GET /api/game/profile**

The caller's economy state without answering — the same numbers as `/answer`, plus
the **full** unlock ladder for their level (so the client never hardcodes the seed).
Requires authentication; a missing profile row answers the zero state (level 1, 0 XP,
the level-1 ladder) and inserts nothing.

**Headers:** `Authorization: Bearer <access_token>`

Example — the zero state of a player who has not answered yet (level 1 at 0 XP, so
`xp_required_for_next_level` is the full span 1 → 2 and `unlocks` is the four-entry
level-1 ladder, grouped by mode):
```json
{
  "total_xp": 0,
  "level": 1,
  "xp_in_level": 0,
  "xp_required_for_next_level": 100,
  "current_streak": 0,
  "best_streak": 0,
  "correct_count": 0,
  "incorrect_count": 0,
  "unlocks": [
    { "mode": "hex_to_bin", "bit_width": 4, "min_level": 1 },
    { "mode": "hex_to_dec", "bit_width": 4, "min_level": 1 },
    { "mode": "bin_to_dec", "bit_width": 4, "min_level": 1 },
    { "mode": "unicode", "bit_width": 8, "min_level": 1 }
  ]
}
```

**Errors:** `401`, `404 User not found.`

---

**GET /api/game/leaderboard**

Top N players by `total_xp` (DESC). Public — no authentication required (the entry
shape carries `username` plus game numbers only, no email and no PII, which is what
keeps it public; the bounded `limit` is the abuse control).

**Query Parameter:** `limit` (default `50`, allowed 1–100 — outside that range is a
`422`)

Ordering is `total_xp DESC`, then `incorrect_count ASC`, then `user_id ASC` — a total
order, so a client polling the board never sees tied rows reshuffle between two
requests. Players without a profile row are absent ("has not played yet" is not a
rank). No `points` field, and no deprecated alias for it.

**Response:** `200 OK`
```json
{
  "entries": [
    {
      "user_id": "01234567-89ab-cdef-0123-456789abcdef",
      "username": "username",
      "total_xp": 1320,
      "level": 6,
      "correct_count": 42,
      "incorrect_count": 5
    }
  ]
}
```

---

### Health Check

**GET /healthz**

Health check endpoint for Docker and monitoring.

**Response:** `200 OK`
```json
{
  "status": "ok",
  "database": "connected"
}
```

## Authentication Flows

### Complete Registration Flow

1. **Register** → POST `/api/auth/register` (always `201`, even for a duplicate)
2. **Verify Email** → Check email, open the link (`GET /verify-email?token=…`) or
   POST `/api/auth/verify-email` with the token
3. **Login** → POST `/api/auth/login` → Receive access token + refresh cookie
4. **Use API** → Include `Authorization: Bearer <access_token>` header
5. **Refresh** → POST `/api/auth/refresh` (uses cookie automatically, and gets a
   *new* refresh cookie back — the old one is dead)
6. **Logout** → POST `/api/auth/logout` (this device only)
7. **Logout everywhere** → POST `/api/auth/logout-all` (all devices)

### Session Revocation Flows

| Situation | Endpoint | Effect |
|---|---|---|
| User logs out normally | `POST /api/auth/logout` | Revokes the refresh token in the cookie |
| User changes or resets password | `POST /api/user/change-password`, `POST /api/auth/reset-password` | Revokes **all** refresh tokens |
| User suspects a compromise | `POST /api/auth/logout-all` | Revokes **all** refresh tokens, returns the count |
| Stolen token is replayed | detected by `POST /api/auth/refresh` | Revokes **all** refresh tokens + audit event `refresh_token_replay_detected` |

Access tokens are never revoked early — they expire on their own after 15 minutes.

### Password Reset Flow

1. **Request Reset** → POST `/api/auth/request-password-reset` (generic answer either way)
2. **Check Email** → Open the reset link `{BASE_URL}/reset-password?token=…` — the page
   is served by the frontend, the backend only validates the token
3. **Reset Password** → POST `/api/auth/reset-password` with token + new password
4. **All other sessions die** → refresh tokens are revoked, so every device must log in again
5. **Login with New Password** → POST `/api/auth/login`

### Email Change Flow

1. **Login** → Get access token
2. **Request Email Change** → PATCH `/api/user/email` (with Bearer token)
3. **Check New Email** → Extract the Fernet-encrypted verification token
4. **Verify New Email** → POST `/api/auth/verify-email-change`
5. **Alert to the old address** → the `email_changed` notification is delivered to the
   previous address, so a takeover is visible there
6. **Login with New Email** → POST `/api/auth/login`

### Password Change Flow

1. **Login** → Get access token
2. **Change Password** → POST `/api/user/change-password` (with Bearer token)
3. **All Sessions Invalidated** → Must login again on all devices
4. **Login with New Password** → POST `/api/auth/login`

## Security Features

Sprint 3 hardened the Sprint 2 auth stack against the findings of the 2026-10-26
security review (CRITICAL → MEDIUM). Every item below maps to a story in
[docs/auth-hardening-plan.md](docs/auth-hardening-plan.md), the rationale and
trade-offs live in [ADR 001](docs/adr/001-auth-hardening.md).

### Configuration Secrets (US-01) 🔴
- `JWT_SECRET` is a required `SecretStr`: empty, `< 32` characters, or the old
  default `dev-secret-change-in-production` stops the app at startup
- No fallback secret anywhere in the code — an unsigned/guessed token can never
  be accepted because there is no known default to guess
- `Settings.__init__` rewrites pydantic `ValidationError.input` for every secret
  field, so a crash traceback can never print `DB_PW`, `SMTP_PASSWORD`,
  `JWT_SECRET` or `EMAIL_CHANGE_KEY`

### Refresh Token Rotation (US-02) 🟠
- Each `/api/auth/refresh` revokes the presented token and issues a new access +
  refresh pair — a token is single-use, never reusable for 7 days
- Only the SHA-256 hash of the token is stored (`user_refresh_tokens.token_hash`)
- **Replay detection:** a token that is presented while already revoked is
  treated as stolen → **all** refresh tokens of the account are revoked
  (aggressive strategy, ADR 001 DEC-02), audit event
  `refresh_token_replay_detected`, `401` with a distinct detail message
- **Residual risk:** an attacker who refreshes before the legitimate user wins
  the race; and a lost/duplicated request can look like a replay and log the
  real user out. The audit entry carries IP and user agent so the difference is
  visible after the fact.

### Anti-Enumeration (US-03) 🟠
- `/api/auth/register` always answers `201` with
  *"If this email is available, a verification mail has been sent."* and
  `user_id: null` — duplicate email or username included, no `409` any more
- Timing-safe path: the password is **always** hashed (bcrypt ≈ 190 ms) and a
  verification token is **always** created, before the existence checks are
  evaluated. The duplicate path rolls the transaction back instead of returning
  early, so both branches take the same time
- The unique-constraint race is caught (`IntegrityError`) and answered with the
  same generic `201`
- Login mirrors this: unknown email and wrong password both answer
  `401 Invalid credentials.`
- `POST /api/auth/resend-verification` applies the same rule (US-08): unknown,
  unverified and already-verified addresses all get
  *"If this email is registered and unverified, a new mail has been sent."*, and a
  failing SMTP send is swallowed rather than surfaced
- **Residual risk:** a mail-volume side channel (a real verification mail is
  only sent for a fresh address) and the `403 Account not verified.` on login,
  both of which require an authenticated position to observe.

### Persistent Rate Limiting (US-04) 🟠

| Endpoint | Limit | Key |
|---|---|---|
| `POST /api/auth/login` | 5 / minute | `login:<ip>:<email>` |
| `POST /api/auth/register` | 3 / hour | `register:<ip>:<email>` |
| `POST /api/auth/resend-verification` | 3 / hour | `resend:<ip>:<email>` |
| Security notification delivery | 10 / hour | `security_notification:<user_id>:<action>` |
| `PATCH /api/user/username` | 1 / 7 days | `last_username_change_at` on the user row |

Limits are stored in the Postgres table `rate_limit_attempt` (ADR 001 DEC-01 —
no Redis in this project) instead of process memory, so they survive restarts
and are shared across workers. Expired rows are swept on every check. Blocked
requests answer `429 Too Many Requests`. The in-memory dict that used to hold
registration attempts is gone.

**Residual risk:** one indexed query per request (latency under high load), and
no atomic counter — a highly parallel burst can overshoot the limit marginally.

### Adaptive Login Lockout (US-05) 🟡
- Progressive lockout **per IP address** (never per account, so an attacker
  cannot lock out a victim): 1 minute after 5 failures, 5 minutes after 10,
  15 minutes after 20
- All three counters are rows in `rate_limit_attempt`
  (`login_lockout_1min|5min|15min:<ip>`), so the lockout is persistent too
- A successful login deletes all lockout rows for that IP (and the rate-limit
  row for that IP + email), so typos never accumulate into a block
- While locked out: `429 Too many failed login attempts. Account locked out.
  Try again later.` — generic, no hint about account existence
- **Residual risk:** distributed brute force from many IPs, and legitimate users
  sharing a NAT/source IP (corporate network, campus WiFi). Follow-up: CAPTCHA
  after 10 failures (documented in ADR 001, not implemented).

### Session Management
- **Access Tokens:** JWT (HS256), 15 minutes TTL, sent as `Authorization: Bearer`
- **Refresh Tokens:** JWT with `type: refresh` + `jti`, 7 days TTL, stored in an
  HttpOnly / SameSite=Strict cookie; only the SHA-256 hash is persisted
- **Rotation:** single-use refresh tokens (US-02, see above)
- **Session Invalidation:** all sessions are revoked on password change, password
  reset, refresh-token replay, and on demand via `POST /api/auth/logout-all` (US-06)
- `COOKIE_SECURE=true` is the default; set it to `false` for local HTTP
  development (auto-derivation from `BASE_URL` is a planned follow-up, ADR 001 DEC-05)

### Encrypted Email-Change Tokens (US-07) 🟡
- The email-change token is `Fernet(JWT)` — AES-128-CBC + HMAC-SHA256 envelope
  around the signed JWT, so the `new_email` claim is not readable in a proxy,
  mail log or browser history
- Key material: `EMAIL_CHANGE_KEY` (≥ 32 chars, `SecretStr`), converted to the
  32-byte url-safe-base64 key Fernet requires via `base64url(SHA-256(key))`
- Decryption happens before JWT verification; any decrypt failure maps to
  `400 Invalid token.` — old plaintext tokens fail cleanly, they do not crash
- **Migration:** tokens issued before US-07 are invalid; users must start the
  email change again. Rotating `EMAIL_CHANGE_KEY` invalidates outstanding tokens
  by design (no key rotation in the MVP, ADR 001 DEC-04)
- **Deployment prerequisite:** the compose stack does not pass `EMAIL_CHANGE_KEY`
  into the container yet, so it currently falls back to the test-suite default key.
  Encryption is then formally active but worthless — see the warning under
  [Configuration](#configuration)

### Audit Logging
Every security-relevant action is written to `user_audit_log` with user ID,
timestamp, IP address, user agent and JSON metadata. Recorded actions:

| Action | Trigger |
|---|---|
| `registration` | `POST /api/auth/register` — fresh accounts only; the duplicate path rolls the transaction back, so no row is kept (deliberate: a rejected duplicate must not leave a trace that leaks the address) |
| `email_verified` | verification via API or `GET /verify-email` link |
| `verification_resend_requested` | `POST /api/auth/resend-verification` (known address) |
| `email_change_requested` | `PATCH /api/user/email` (metadata: `new_email`) |
| `email_changed` | `POST /api/auth/verify-email-change` (metadata: `old_email`, `new_email`) |
| `username_changed` | `PATCH /api/user/username` (metadata: `new_username`) |
| `password_change` | `POST /api/user/change-password` |
| `password_reset` | `POST /api/auth/reset-password` |
| `refresh_token_replay_detected` | replayed refresh token (metadata: `token_id`, `expired_at`) |
| `all_sessions_revoked` | `POST /api/auth/logout-all` (metadata: `session_count`) |

### Security Notifications (US-09)
Self-contained HTML alert emails (no external CSS, images or tracking links) for
sensitive account events:
- **Templates** per action: `password_reset`, `password_change`, `email_changed`,
  `username_changed`, `refresh_token_replay_detected`, plus a generic fallback for
  unknown actions — the alert for `email_changed` is deliberately sent to the **old**
  address
- **XSS protection:** every user-controlled value (username, IP, user agent,
  old/new email, new username, even the action name in `<title>`) passes
  `_sanitize()` / `html.escape(..., quote=True)` before it reaches the template
- **Recipient validation:** addresses are checked against an RFC-5322-lite regex
  so a malformed or header-injection address never reaches the MTA
- **Spam/DoS protection:** delivery is capped at 10 notifications per user and
  action per hour through the same persistent `RateLimitService`
- **Failure isolation:** a broken mail pipeline is logged and swallowed — it can
  never fail the security action that triggered it. The audit log is the source
  of truth, the mail is a courtesy copy

> **⚠️ Current wiring gap:** `SecurityNotificationService` and its templates are
> complete and covered by `tests/test_security_notifications.py`, but the hook the
> flows call — `src/services/audit_service.send_security_notification()` — is still
> a no-op placeholder. Today only the `email_changed` alert is actually delivered
> (via `EmailService.send_security_notification_email` in
> `POST /api/auth/verify-email-change`). Password changes/resets, username changes
> and replay alerts are audited but not yet mailed. Connecting that hook is the
> open follow-up for US-09.

### Password Requirements
- 8 – 128 characters
- at least one uppercase letter, one lowercase letter, one digit **and** one
  special character
- Every rule violation is reported as its own message in `detail.errors`
- Hashed with bcrypt (cost 12); verification uses `bcrypt.checkpw`

## Testing

### Prerequisites

DB-backed tests (migrations, repositories, endpoint tests) run against a
dedicated PostgreSQL test instance — not the development database on port
5432. Start it before running the suite:

```bash
docker compose -f docker-compose.test.yaml up -d
```

Defaults are `test_user`/`test_password` @ `localhost:5434`, database
`test_db` (identical credentials to the CI Postgres service). Local
overrides belong in a gitignored `.env.test` file (see
`.env.test.example`). Details: `docs/test-setup.md`.

### Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage report
uv run pytest --cov=src --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_auth_register.py

# Run with verbose output
uv run pytest -v
```

### Test Structure

**Sprint 2 — authentication system**
- `tests/test_auth_register.py`: Registration + email verification (US-13,
  plus the anti-enumeration cases from US-03)
- `tests/test_auth_login.py`: Login, refresh, logout (US-14)
- `tests/test_auth_password_reset.py`: Password reset flow (US-15)
- `tests/test_user_changes.py`: Email/username change (US-16)
- `tests/test_user_password_change.py`: Password change while logged in (US-19)
- `tests/test_integration_auth.py`: End-to-end auth flows (US-17)
- `tests/test_healthz.py`: Health check endpoint (US-18)
- `tests/test_jwt_utils.py`: Token creation/decoding (US-12)
- `tests/test_services.py`: Auth/user service layer units
- `tests/test_auth_coverage.py`: Auth router edge cases — rate-limit key
  fallback, verify-email/logout corners (US-21 part 2)
- `tests/test_user_me.py`: `GET /api/user/me` profile + stats

**Sprint 3 — security hardening**
- `tests/test_settings.py`: `JWT_SECRET`/`EMAIL_CHANGE_KEY` validation, secret
  masking in `ValidationError`s (US-01)
- `tests/test_auth_refresh.py`: Rotation, expired/revoked tokens, replay →
  full session kill + audit event (US-02)
- `tests/test_rate_limit.py`: DB-backed limiter — window, expiry, sweep (US-04)
- `tests/test_auth_resend_verification.py`: Silent resend + enumeration safety
  (US-04/US-08)
- `tests/test_login_lockout.py`: Progressive per-IP lockout, reset on success (US-05)
- `tests/test_logout_all.py`: Kill-all sessions, idempotency, auth required (US-06)
- `tests/test_email_change_token.py`: Fernet round-trip, tampering, wrong key,
  legacy plaintext tokens (US-07)
- `tests/test_security_notifications.py`: Templates, HTML escaping, recipient
  validation, delivery rate limit (US-09)

**Sprint 4 — XP economy**
- `tests/test_game.py`: The economy route table — `/answer`, `/profile`,
  `/leaderboard`, and the 404 the removed legacy writers now return (US-34/US-35/US-36)
- `tests/test_answer_endpoint.py`, `tests/test_input_validation.py`: the
  authenticate → validate → rate-limit → unlock-gate → transact order, per-mode
  `bit_width` rejection, `extra="forbid"`, payload budget (US-32/US-33)
- `tests/test_game_config.py`: the frozen registry — bits in every mode, rational
  tiers, immutability, no import side effects (US-25)
- `tests/test_game_profile_models.py`: ORM row + wire schemas, incl. the US-33 golden
  fixture reproduced byte-for-byte (US-26)
- `tests/test_xp_engine.py`, `tests/test_level_service.py`,
  `tests/test_unlock_service.py`: the three pure layers — golden matrix and property
  tests (§4.6), level curve/floor/clamp (§4.8), unlock ladder (§4.9) (US-29/US-30/US-31)
- `tests/test_game_service.py`: streak lifecycle — row lock, one commit per answer,
  lazy profile creation (US-28)
- `tests/test_user_me.py`: `/api/user/me` agrees with `/api/game/profile` field for
  field on the same row (US-37)

**Infrastructure**
- `tests/test_migrations.py`: Schema migrations vs. PostgreSQL (US-09, US-22, v003,
  v004, v005)
- `tests/test_smtp_integration.py`: SMTP settings + startup wiring (US-20)
- `tests/test_email_service_ssl.py`: STARTTLS / implicit-TLS handling
- `tests/test_init_db.py`, `tests/test_smoke.py`, `tests/test_deployment.py`:
  Startup script, app boot, Docker/CI contracts
- `tests/test_engine_pool_config.py`: the engine's pool is configured explicitly, and
  an in-flight answer holds **one** pooled connection — the limiter's session and the
  answer transaction are sequential, never simultaneous (US-32, Rev 3 R-2)
- `tests/test_device_info_service.py`, `tests/test_geolocation_service.py`: login-audit
  enrichment — User-Agent → device string, IP → location (incl. private-IP rejection)
- `tests/test_architecture_docs.py`: Guards the docs themselves

### Coverage Requirements

The project enforces a minimum coverage of **80%** on all code.

Async coverage note: SQLAlchemy async switches to greenlets and aiosqlite
runs queries in a worker thread. `[tool.coverage.run]` therefore sets
`concurrency = ["greenlet", "thread"]` (pyproject.toml). Without it,
coverage.py loses the trace after the first `await` into the DB layer and
understates router coverage badly (this made `src/routers/auth.py` look
like 75 % when existing tests already exercised ~98 % of it).

**Current Test Results — Sprint 4** (measured 2026-10-29, `uv run pytest`):
- **1983 tests**, total coverage **95.35 %** (gate: 80 %)
- `routers/game.py` **100 %** — it left the omit list with US-32/33, because it holds
  `submit_answer`, which is security-critical (auth → validate → rate-limit → unlock
  gate → transact) and an omitted file measures nothing. The two legacy Sprint-1
  handlers that used to live beside it were excluded per handler by
  `# pragma: no cover` until US-34 deleted them.
- Sprint 4 added the answer-endpoint, input-validation, profile, leaderboard, pool-config
  and economy-unit suites on top of the Sprint-3 baseline below.

**Sprint-3 baseline** (measured 2026-10-27, `uv run pytest --cov=src`: 340 tests,
92.83 %) — still the auth/hardening reference breakdown:
- Security-critical modules at 100 %: `config.py`, `models/auth.py`,
  `services/auth_service.py`, `services/rate_limit_service.py`,
  `services/security_notification_service.py`, `utils/login_lockout.py`,
  `utils/jwt_utils.py`, `migrations/v001_auth_schema.py`
- Routers: `routers/auth.py` 99 %, `routers/user.py` 95 %
- Hardening units: `utils/email_change_token.py` 94 %,
  `services/password_reset_service.py` 94 %, `services/user_service.py` 96 %,
  `migrations/v003_rate_limit.py` 88 %
- All authentication and hardening flows are covered, including adversarial
  cases (replay, lockout escalation, tampered/legacy tokens, enumeration timing)
- Coverage debt below the per-file target *as of that snapshot*: `main.py` 63 %
  (lifespan + HTML verification pages), `email_templates.py` 79 % (transactional
  builders), `migrations/v002_game_stats.py` 33 % — the current figures are below
- Excluded from the coverage run by `pyproject.toml`: `init_db.py`,
  `services/email_service.py` (needs a real SMTP server)

## Docker

### Build and Run

```bash
# Build image
docker build -t hextobinarytrainer2-backend .

# Run container
docker run -p 8000:8000 --env-file .env hextobinarytrainer2-backend
```

### Docker Compose

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f api

# Stop all services
docker compose down
```

## Development

### Code Quality

```bash
# Run linter
uv run ruff check src/

# Auto-fix linting issues
uv run ruff check --fix src/

# Format code
uv run ruff format src/
```

### Database

Database migrations run automatically on application startup via the lifespan context manager.

## Sprint Progress

> **Numbering note:** this README numbers the delivery streams (Sprint 1 = test
> foundation, Sprint 2 = authentication system, Sprint 3 = security hardening,
> Sprint 4 = XP economy).
> The docs under `docs/` predate that convention: `docs/sprint3-plan.md` calls the
> *production-readiness* sprint (US-20 … US-22, delivered between Sprint 2 and 3)
> "Sprint 3", and `docs/auth-hardening-plan.md` numbers four sprints 1 – 4 *inside*
> the hardening stream (Sprint 1 = ADR/preparation, Sprint 2 = 🔴/🟠 stories
> US-01 … US-04, Sprint 3 = 🟡 stories US-05 … US-08, Sprint 4 = 🟢//📖 stories
> US-08b … US-12). The story IDs below are the canonical ones used by the plan, the
> `# US-xx` comments in `src/` and the commit messages — not a renumbering. Because
> the Sprint-2 backlog numbers its own stories US-09 … US-19, a number such as
> `US-09` appears twice in this file with different meanings; the sprint heading
> tells you which backlog is meant.

### Sprint 2 - Authentication System (COMPLETED)

**Status:** ✅ All tasks completed (11/11 User Stories)

**User Stories:**
- ✅ US-09: Database Schema Migration (Clean Break)
- ✅ US-10: Email Service Interface
- ✅ US-11: Password Hashing Utility
- ✅ US-12: JWT Token Utilities
- ✅ US-13: Registration + Email Verification
- ✅ US-14: Login + Refresh + Logout
- ✅ US-15: Password Reset Flow
- ✅ US-16: Email + Username Change
- ✅ US-17: Integration Tests + Cross-Feature Flows
- ✅ US-18: CI/CD + Deployment Updates
- ✅ US-19: Password Change (logged in)

**Results:**
- 123 tests passed
- 80%+ code coverage enforced
- All authentication flows implemented and tested
- Security features: rate limiting (made persistent in Sprint 3 / US-04), audit
  logging, session invalidation
- Multi-provider schema ready for OAuth integration
- Findings of the 2026-10-26 security review → handed over to Sprint 3

### Sprint 3 - Security Hardening (COMPLETED)

**Status:** ✅ All stories completed and merged (US-01 … US-09)

**Context:** Defense-in-depth on the Sprint 2 auth stack, closing the CRITICAL /
HIGH / MEDIUM findings of the 2026-10-26 review. Design decisions and threat
analysis: [ADR 001](docs/adr/001-auth-hardening.md); story definitions:
[docs/auth-hardening-plan.md](docs/auth-hardening-plan.md); adversarial test
plan: [docs/auth-hardening-test-plan.md](docs/auth-hardening-test-plan.md). It
builds on the production-readiness work delivered in between (US-20 SMTP wiring,
US-21 coverage ≥ 80 %, US-22 PostgreSQL migration tests — see
[docs/sprint3-review.md](docs/sprint3-review.md)).

**User Stories** (hardening backlog, severity from the review):
- ✅ US-01: `JWT_SECRET` as required `SecretStr` in Settings, fail-fast startup 🔴
- ✅ US-02: Refresh Token Rotation + replay detection (kills all sessions) 🟠
- ✅ US-03: Anti-Enumeration — generic registration answer, timing-safe paths 🟠
- ✅ US-04: Persistent DB-backed rate limiting (`rate_limit_attempt`, migration
  `v003`) + `POST /api/auth/resend-verification` 🟠
- ✅ US-05: Adaptive login lockout — progressive per-IP blocks (1 / 5 / 15 min) 🟡
- ✅ US-06: `POST /api/auth/logout-all` — revoke every active session 🟡
- ✅ US-07: Email-change tokens encrypted with Fernet (no plaintext address in the JWT) 🟡
- ✅ US-08: Verification resend with silent, enumeration-safe handling 🟡
- ✅ US-09: Security notification emails — HTML templates, XSS-escaped, rate-limited 🟢
  (delivery hook still a placeholder, see [Security Notifications](#security-notifications-us-09))
- ➡️ US-08b (password-reset UI) lives in the frontend repository and is **not** part
  of this backend

**Results:**
- 340 tests passed (up from 157), total coverage 92.83 % — gate 80 %
- New table `rate_limit_attempt` (migration `v003_rate_limit`), new modules
  `services/rate_limit_service.py`, `services/security_notification_service.py`,
  `services/email_templates.py`, `utils/login_lockout.py`, `utils/email_change_token.py`
- New endpoints: `POST /api/auth/logout-all` (US-06) and
  `POST /api/auth/resend-verification` (US-04/US-08); `/api/auth/refresh` now
  rotates the cookie on every call — breaking for clients that ignore the
  `Set-Cookie` response
- ADR 001 decisions implemented: DEC-01 (DB instead of Redis), DEC-02 (aggressive
  replay revocation), DEC-03 (IP-based backoff), DEC-04 (Fernet without key rotation)
- No CRITICAL or HIGH finding from the review remains open

**Follow-ups carried into the next sprint:** see
[Carried-over hardening work](#carried-over-hardening-work) — the open items are
the notification transport wiring (US-09), the low-priority hardening stories
(US-10 … US-12), `EMAIL_CHANGE_KEY` in the compose stack, the frontend
`/reset-password` page and the coverage debt.

### Sprint 4 - XP Economy (IN PROGRESS — backend complete)

**Status:** 🟡 US-25 … US-37 and US-40 (this documentation) are merged; **US-41**
(end-to-end economy suite) and **US-42** (sprint quality gate) are outstanding, and
US-38/US-39 live in the frontend repository. Plan:
[docs/sprint4-plan.md](docs/sprint4-plan.md); normative tables:
[docs/xp-economy.md](docs/xp-economy.md).

**Context:** replaces the Sprint-1 `+bits` / `-4×bits` scoring with an XP economy —
four modes, streak tiers, a level curve and level-gated content unlocks. All arithmetic
is exact integer/rational (D-8): every award, penalty and level requirement is a
multiple of 4, and **no rounding path exists anywhere in the engine**.

**User Stories (this repository):**
- ✅ US-25: frozen config registry — `services/game_config.py`
- ✅ US-26: ORM row + wire schemas — `models/game.py`, `models/game_schemas.py`
- ✅ US-27: migration `v004` (additive XP schema, no backfill)
- ✅ US-28: streak lifecycle — row lock, one commit per answer (`services/game_service.py`)
- ✅ US-29: exact-integer XP engine (`services/xp_engine.py`)
- ✅ US-30: level progression + `cum(level)` floor (`services/level_service.py`)
- ✅ US-31: unlock ladder (`services/unlock_service.py`)
- ✅ US-32: input validation + anti-XP-farming (per-mode widths, `extra="forbid"`,
  4 KiB body cap, 120 answers / 60 s)
- ✅ US-33: `POST /api/game/answer` — gate order 401 → 422 → 429 → 403 → 200/404
- ✅ US-34: legacy removal + migration `v005` (drop the counters table)
- ✅ US-35: `GET /api/game/profile`
- ✅ US-36: leaderboard v2 by `total_xp` — **still public**
- ✅ US-37: `/api/user/me` adapted (shares the profile snapshot)
- ✅ US-40: documentation (this section, `docs/xp-economy.md`, architecture, backlog)
- ⏭️ US-38/US-39: frontend (sibling repo) — unified submit, XP/streak/level HUD,
  unlock-gated width/mode selection
- ⬜ US-41: end-to-end economy integration suite
- ⬜ US-42: sprint quality gate

**Changelog (Sprint 4):**

*Added*
- `POST /api/game/answer` — the economy's only write path. Returns the full state:
  `xp_delta` (signed, post-clamp), `total_xp`, derived `level`, `xp_in_level`,
  `xp_required_for_next_level`, `current_streak` / `best_streak`, `correct_count` /
  `incorrect_count` and the `newly_unlocked` delta set
- `GET /api/game/profile` — the same numbers plus the **full** unlock ladder for the
  caller's level, so the client never hardcodes the D-2 seed
- Table `user_game_profile` (migration **`v004`, additive**): `total_xp`, `level`,
  `current_streak`, `best_streak`, `correct_count`, `incorrect_count`, `updated_at`,
  four `CHECK` constraints (`total_xp`, `level` and both streak counters `>= 0`) and the
  index `ix_user_game_profile_total_xp (total_xp DESC)`.
  The row is created **lazily** by the first answer (US-28 AC-3) — no insert at
  registration, and `GET /profile` never writes
- Eight new modules: `services/game_config.py` (frozen registry), `xp_engine.py`,
  `level_service.py`, `unlock_service.py` (the pure layers), `game_service.py` and
  `answer_service.py` (row lock, streak lifecycle, wire assembly), `profile_service.py`
  and `leaderboard_service.py` (the read sides)
- [docs/xp-economy.md](docs/xp-economy.md) — the reference tables, code-adjacent,
  cross-referenced from `src/services/game_config.py`
- Backlog **TD-01** (server-issued challenge nonces / HMAC-signed answers) and
  **TD-02** (answer-history event table) — see [docs/backlog.md](docs/backlog.md)

*Changed*
- **Leaderboard v2**: ranked by `total_xp DESC`, then `incorrect_count ASC`, then
  `user_id ASC` — a total order, so a polling client never sees tied rows reshuffle.
  Path, the `{entries: [...]}` wrapper and **public visibility are unchanged** (no auth,
  PO decision Rev 3 R-3 / OQ-9); the abuse controls are the bounded `limit` (1–100,
  enforced twice) and the absence of PII from the entry shape
- `GET /api/user/me` reports the economy counters from `user_game_profile` instead of
  the legacy stats row, and agrees with `/api/game/profile` field for field (US-37)
- `bit_width` speaks **bits in every mode** (OQ-7): 4/8/16/32 for `hex_to_bin` /
  `hex_to_dec` / `bin_to_dec`, 8/16/24/32 for `unicode` (= 1–4 bytes). Consequence:
  `unicode + bit_width: 4` is a loud `422` instead of a valid-but-wrong 128-XP answer,
  and the frontend's existing `bytes * 8` arithmetic is correct unchanged
- Answer validation is per-mode (`game_config.VALID_WIDTHS_BY_MODE`), strict-typed
  (`is_correct` must be a JSON boolean, `bit_width` an int) and `extra="forbid"`, so a
  client-computed `total_xp` or a body `user_id` cannot be smuggled in
- The rate limiter's counting entry point on the answer path is
  `check_without_cleanup`, and it runs on its **own** session — a throttled caller can
  never hold another caller's row lock, and a 429 leaves no partial state

*Removed*
- The Sprint-1 `+bits` / `-4×bits` scoring: both `POST` routes under `/api/game` and the
  counters table behind them, via migration **`v005` (destructive)** — with **no
  deprecated alias**
- The legacy score field from every response (me, leaderboard, profile)

*Migration / deployment note*
- **No backfill, fresh installs only** (D-6): the database was empty, so there is no
  `points → xp` conversion and no dual-write of values — XP is only ever written to
  `user_game_profile`
- The split into `v004` (create) and `v005` (drop) exists so no commit ever left the app
  unable to boot or the suite red: the legacy routes kept serving until US-33/US-36/US-37
  had removed every reader and writer (Rev 3 R-1). Both migrations are idempotent via
  `schema_migrations` and registered after `v003` in the lifespan
- **Deploy consequence:** `v005` must ship in the **same release train** as the frontend
  switch (US-38/US-39), or older clients lose their write path mid-rollout

**Results:**
- **1983 tests, 95.35 % total coverage** (measured 2026-10-29, `uv run pytest`) — gate 80 %
- Every Sprint-4 economy module measures **100 %**: `routers/game.py`, `models/game.py`,
  `models/game_schemas.py`, `services/game_config.py`, `services/game_service.py`,
  `migrations/v005` (the legacy-table drop); plus `migrations/v004_xp_schema.py` 89 %.
  `routers/game.py` left the coverage omit list with US-32/33 because `submit_answer` is
  security-critical and an omitted file measures nothing
- Remaining debt below the per-file target: `main.py` **70 %** (lifespan + HTML
  verification pages) and `email_templates.py` **79 %** (transactional builders).
  `migrations/v002_game_stats.py` closed its 33 % gap and sits at **80 %**
- Gate order pinned at the boundary: 401 with zero SQL (before any session exists),
  422 before the handler body runs, 429 on a separate transaction, 403 on the **locked
  pre-answer** level (finding F-2 — an answer worth enough XP to reach the level it asks
  about is still refused), 200 with one `COMMIT`
- **Known accepted risk (T-01):** `is_correct` is the client's own assertion and the
  server has no oracle to check it — no challenge nonce, no answer history. The rate
  limit bounds request rate, not XP. Hardening is filed as TD-01/TD-02, deliberately
  not built without production data (YAGNI)

### Sprint 1 - Test Foundation (COMPLETED)

**Status:** ✅ All tasks completed

**Results:**
- 58 tests passed
- 95% total coverage
- All God Nodes >80% coverage

## Roadmap

Sprint 3 (security hardening) is **done** — see [Sprint Progress](#sprint-3---security-hardening-completed).
Sprint 4 (XP economy) is **backend-complete, not closed**: US-25 … US-37 and this
documentation pass are merged, while the frontend stories (US-38/US-39, sibling repo), the
end-to-end economy suite (US-41) and the sprint quality gate (US-42) are outstanding — see
[Sprint Progress](#sprint-4---xp-economy-in-progress--backend-complete).

### Open Sprint-4 work
- US-41: end-to-end economy integration suite (register → lazy profile → streak 1→11 with
  the exact §4.6 deltas → unlock boundaries at 476 / 3,220 / 25,752 XP → penalty at the
  floor → 429 leaves no partial state → leaderboard ordering → public-board no-auth
  regression pin)
- US-42: sprint quality gate (Section 7 checklist, security-reviewer APPROVE on the
  US-32/US-33 diff, no `# OQ-x` seam markers left in code)
- Ship `v005` in the **same release train** as the frontend switch — see the migration
  note in [Sprint 4](#sprint-4---xp-economy-in-progress--backend-complete)

### Next after Sprint 4
- OAuth Provider Integration (GitHub, Google) — schema is ready since Sprint 2 (US-09)
- Phone Authentication (SMS verification)
- Account Deletion (GDPR compliance)
- Device list for "which devices are logged in?" — the revocation half already
  exists (`POST /api/auth/logout-all`), a per-device view over
  `user_refresh_tokens` is still missing
- Frontend: password-reset pages (`/forgot-password`, `/reset-password?token=…`) —
  backend-complete, see US-08b

### Carried-over hardening work
- Forward `EMAIL_CHANGE_KEY` in `docker-compose.yaml` (and set a real value in the
  deployment `.env`) — today the container runs on the default test key, which
  defeats US-07 in production
- Wire `audit_service.send_security_notification()` to the email transport so
  password/username/replay alerts actually leave the server (US-09)
- US-10: timing-safe token comparison (`hmac.compare_digest`)
- US-11: `COOKIE_SECURE` auto-derivation from `BASE_URL` + CSRF double-submit for
  `/refresh` (ADR 001 DEC-05)
- US-12: `docs/runbooks/auth-incident.md` (replay detected, JWT secret leaked,
  brute force), structured JSON logging, ADR 001 status → *Accepted*
- CAPTCHA after repeated login failures — mitigation for distributed brute force
  (documented as follow-up in ADR 001 DEC-03)
- Close the coverage debt: `main.py` 70 %, `email_templates.py` 79 % (measured
  2026-10-29; `migrations/v002_game_stats.py` reached 80 % in Sprint 4)

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/us-XX-description`)
3. Write tests first (TDD)
4. Implement feature
5. Ensure 80%+ coverage
6. Create pull request

## Support

For issues and questions, please open an issue on GitHub.
