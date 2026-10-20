# HexToBinaryTrainer2 Backend

FastAPI backend for a hex-to-binary training application with comprehensive user authentication, email verification, and secure session management.

## Features

### Authentication System (Sprint 2)
- **User Registration** with email verification
- **Login/Logout** with JWT access tokens and secure refresh token cookies
- **Token Refresh** for seamless session continuation
- **Password Reset** via email with secure one-time tokens
- **Password Change** for authenticated users (invalidates all sessions)
- **Email Change** with verification and audit logging
- **Username Change** with 7-day cooldown limit
- **Audit Logging** for all critical security actions
- **Security Notifications** via email for sensitive operations
- **Multi-Provider Ready** schema (prepared for OAuth integration)
- **Rate Limiting** on authentication endpoints

### Core Features (Sprint 1)
- **Answer Tracking**: Track correct/incorrect answers with point calculations
  - Correct answer: +bits points
  - Incorrect answer: -4×bits points
- **Leaderboard**: Top 10 users sorted by points (DESC), then incorrect count (ASC)

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

# JWT Configuration (REQUIRED)
# Generate a strong secret: python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET=your-strong-jwt-secret-here

# SMTP Configuration (REQUIRED for email notifications)
SMTP_HOST=smtp.strato.de
SMTP_PORT=587
SMTP_USER=your-email@your-domain.de
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=noreply@your-domain.de

# API Configuration
ALLOWED_ORIGINS=["http://localhost:5173"]

# Optional: Cookie security (set to false for local development without HTTPS)
COOKIE_SECURE=true
```

**Important:**
- Generate a strong `JWT_SECRET` (at least 32 bytes)
- Configure SMTP credentials for email verification and security notifications
- Set `COOKIE_SECURE=false` for local development without HTTPS

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
  "message": "Registration successful. Please check your email to verify your account.",
  "user_id": "01234567-89ab-cdef-0123-456789abcdef"
}
```

**Password Requirements:**
- Minimum 8 characters
- Maximum 128 characters
- At least 1 uppercase letter
- At least 1 digit

**Rate Limiting:** 3 registrations per hour per IP address

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
  "message": "Email verified successfully. You can now log in."
}
```

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

**Headers:** `Set-Cookie: refresh_token=<token>; HttpOnly; Secure; SameSite=Strict; Path=/api/auth/refresh`

**Rate Limiting:** 5 login attempts per minute per email address

---

**POST /api/auth/refresh**

Refresh access token using refresh cookie.

**Request:** Cookie `refresh_token=<token>` (sent automatically)

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

**POST /api/auth/logout**

Logout and revoke refresh token.

**Request:** Cookie `refresh_token=<token>` (sent automatically)

**Response:** `200 OK`
```json
{
  "message": "Logged out successfully"
}
```

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
  "message": "If the email exists, a reset link has been sent."
}
```

**Security:** Always returns 200, even if email doesn't exist (prevents email enumeration)

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
  "message": "Password reset successfully. All sessions have been invalidated."
}
```

**Security:**
- All active sessions are invalidated
- Audit log entry created
- Security notification email sent

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
  "message": "Verification email sent to new address"
}
```

---

**POST /api/auth/verify-email-change**

Verify new email address with token from verification email.

**Request:**
```json
{
  "token": "email-change-token-from-email"
}
```

**Response:** `200 OK`
```json
{
  "message": "Email updated successfully"
}
```

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
  "message": "Username updated successfully"
}
```

**Username Requirements:**
- 3-50 characters
- Alphanumeric + underscore only
- Unique across all users

**Rate Limiting:** Once per 7 days per user

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

**Security:**
- Validates old password before allowing change
- All active sessions are invalidated
- Audit log entry created
- Security notification email sent

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

1. **Register** → POST `/api/auth/register`
2. **Verify Email** → Check email, extract token → POST `/api/auth/verify-email`
3. **Login** → POST `/api/auth/login` → Receive access token + refresh cookie
4. **Use API** → Include `Authorization: Bearer <access_token>` header
5. **Refresh** → POST `/api/auth/refresh` (uses cookie automatically)
6. **Logout** → POST `/api/auth/logout`

### Password Reset Flow

1. **Request Reset** → POST `/api/auth/request-password-reset`
2. **Check Email** → Extract reset token
3. **Reset Password** → POST `/api/auth/reset-password`
4. **Login with New Password** → POST `/api/auth/login`

### Email Change Flow

1. **Login** → Get access token
2. **Request Email Change** → PATCH `/api/user/email` (with Bearer token)
3. **Check New Email** → Extract verification token
4. **Verify New Email** → POST `/api/auth/verify-email-change`
5. **Login with New Email** → POST `/api/auth/login`

### Password Change Flow

1. **Login** → Get access token
2. **Change Password** → POST `/api/user/change-password` (with Bearer token)
3. **All Sessions Invalidated** → Must login again on all devices
4. **Login with New Password** → POST `/api/auth/login`

## Security Features

### Rate Limiting
- **Login:** 5 attempts per minute per email
- **Registration:** 3 attempts per hour per IP
- **Username Change:** Once per 7 days per user

### Session Management
- **Access Tokens:** JWT, 15 minutes TTL
- **Refresh Tokens:** Opaque, 7 days TTL, stored in HttpOnly cookies
- **Session Invalidation:** All sessions revoked on password change/reset

### Audit Logging
All critical actions are logged with:
- User ID
- Timestamp
- IP address
- User agent
- Action type (password_change, password_reset, email_change, username_change)

### Security Notifications
Email notifications sent for:
- Password changes
- Password resets
- Email changes
- Username changes

### Password Requirements
- Minimum 8 characters
- Maximum 128 characters
- At least 1 uppercase letter
- At least 1 digit
- Hashed with bcrypt (cost 12)

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

- `tests/test_auth_register.py`: Registration + email verification (US-13)
- `tests/test_auth_login.py`: Login, refresh, logout (US-14)
- `tests/test_auth_password_reset.py`: Password reset flow (US-15)
- `tests/test_user_changes.py`: Email/username change (US-16)
- `tests/test_user_password_change.py`: Password change while logged in (US-19)
- `tests/test_integration_auth.py`: End-to-end auth flows (US-17)
- `tests/test_healthz.py`: Health check endpoint (US-18)
- `tests/test_migrations.py`: Auth schema migration vs. PostgreSQL (US-09, US-22)
- `tests/test_auth_coverage.py`: Auth router edge cases — rate-limit key
  fallback, verify-email/logout corners (US-21 part 2)

### Coverage Requirements

The project enforces a minimum coverage of **80%** on all code.

Async coverage note: SQLAlchemy async switches to greenlets and aiosqlite
runs queries in a worker thread. `[tool.coverage.run]` therefore sets
`concurrency = ["greenlet", "thread"]` (pyproject.toml). Without it,
coverage.py loses the trace after the first `await` into the DB layer and
understates router coverage badly (this made `src/routers/auth.py` look
like 75 % when existing tests already exercised ~98 % of it).

**Current Test Results:**
- 157 tests passed
- `src/routers/auth.py` at 100 %, total coverage 97 %
- All authentication flows covered
- Integration tests verify end-to-end scenarios

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
- Security features: rate limiting, audit logging, session invalidation
- Multi-provider schema ready for OAuth integration

### Sprint 1 - Test Foundation (COMPLETED)

**Status:** ✅ All tasks completed

**Results:**
- 58 tests passed
- 95% total coverage
- All God Nodes >80% coverage

## Roadmap

### Sprint 3 (Planned)
- OAuth Provider Integration (GitHub, Google)
- Phone Authentication (SMS verification)
- Account Deletion (GDPR compliance)
- Multi-Device Management ("Which devices are logged in?")
- HTML Email Templates

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
