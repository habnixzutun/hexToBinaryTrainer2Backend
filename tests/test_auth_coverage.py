"""Coverage tests for ``src/routers/auth.py`` (US-21 part 2).

Sprint baseline measured auth.py at 75 %. Investigation (see PR description)
showed the measurement was wrong: without ``[tool.coverage.run]
concurrency = ["greenlet", "thread"]`` (pyproject.toml), coverage.py loses the
trace after the first await into the SQLAlchemy/aiosqlite layer, so every line
of the endpoint bodies *after* a DB await was reported as uncovered even though
existing tests exercised them. With the corrected measurement the existing
auth suites already reach 98 %.

This file closes the genuine remaining gaps (lines 70-72 and 275) and adds
dedicated tests for the AC scenarios that had no exact-match test yet.

AC mapping (docs/sprint3-plan.md, US-21 part 2):

- lines 70-72 (_email_key IP fallback)
    -> test_email_key_falls_back_to_ip_when_body_unparseable (this file).
       Over HTTP the branch is unreachable: FastAPI parses the login body
       *before* the slowapi wrapper calls ``_email_key``, so an unparseable
       body returns 422 first. Defensive code -> direct unit test; a
       companion integration test pins the HTTP-visible behavior.
- register rate limit 429
    -> test_register_rate_limit_returns_429 (this file).
       NOTE: the AC expected a Retry-After header, but the registration
       limiter raises a plain HTTPException (not slowapi's
       RateLimitExceeded), so no header is set today. Adding it would need
       a router change — out of scope here; flagged to PO.
- test_login_unverified_account_returns_403
    -> existing: tests/test_auth_login.py
- test_login_wrong_password_returns_401
    -> existing: tests/test_auth_login.py
- test_refresh_with_revoked_token_returns_401
    -> existing: tests/test_auth_login.py
- test_register_sends_verification_email
    -> existing: tests/test_auth_register.py (TestRegister)
- test_register_duplicate_email_returns_409
    -> existing: tests/test_auth_register.py (TestRegister)
- verify-email already verified (exact message)
    -> test_verify_email_already_verified_returns_exact_message (this file)
- test_verify_email_invalid_type_returns_400
    -> test_verify_password_reset_token_type_returns_400 (this file)
- line 275 (identity not found)
    -> test_verify_email_unknown_identity_returns_400 (this file)
- test_logout_with_unknown_token_returns_200_idempotent
    -> same name (this file)
"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.routers.auth import (
    _email_key,
    _registration_attempts,
    get_email_service,
    limiter,
    set_email_service,
)
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import create_password_reset_token, create_verification_token
from src.utils.password import hash_password
from starlette.requests import Request

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "StrongPass1!"


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Clear both registration limiters between tests to prevent bleed-over."""
    _registration_attempts.clear()
    limiter.reset()
    yield
    _registration_attempts.clear()
    limiter.reset()


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
def mock_email_service():
    svc = MockEmailService()
    set_email_service(svc)
    yield svc
    set_email_service(MockEmailService())


@pytest.fixture
async def client(engine, mock_email_service):
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_email_service] = lambda: mock_email_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


async def _seed_verified_user(session: AsyncSession, email: str, username: str) -> str:
    """Insert a user with a verified email identity; returns the user id."""
    user_id = f"user-{username}"
    session.add(User(id=user_id, username=username, email=email))
    session.add(
        UserIdentity(
            id=f"identity-{username}",
            user_id=user_id,
            provider="email",
            provider_user_id=email,
            password_hash=hash_password(VALID_PASSWORD),
            verified_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return user_id


def _latest_verification_token(svc: MockEmailService) -> str:
    """Extract the token of the most recent verification email."""
    for mail in reversed(svc.sent):
        if mail.get("type") == "verification":
            return mail["token"]
    raise AssertionError("No verification email was sent")


# ---------------------------------------------------------------------------
# Registration rate limit (custom limiter, lines 48-59)
# ---------------------------------------------------------------------------


async def test_register_rate_limit_returns_429(client):
    """Fourth registration from the same IP within the window returns 429.

    The AC expected a Retry-After header, but the registration limiter raises a
    plain HTTPException (not slowapi's RateLimitExceeded), so no header is set.
    Changing that would require a router modification — out of scope for this
    test-only US. Documented in the PR description.
    """
    for i in range(3):
        response = await client.post(
            "/api/auth/register",
            json={
                "email": f"rl-user{i}@example.com",
                "username": f"rluser{i}",
                "password": VALID_PASSWORD,
            },
        )
        assert response.status_code == 201, response.text

    blocked = await client.post(
        "/api/auth/register",
        json={
            "email": "rl-user3@example.com",
            "username": "rluser3",
            "password": VALID_PASSWORD,
        },
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == (
        "Too many registration attempts. Try again later."
    )


# ---------------------------------------------------------------------------
# Login rate-limit key function (_email_key, lines 62-72)
# ---------------------------------------------------------------------------


def _make_http_request(body: bytes | None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(b"content-type", b"application/json")],
        "client": ("203.0.113.7", 54321),
        "query_string": b"",
    }
    request = Request(scope)
    if body is not None:
        request.state._body = body
    return request


def test_email_key_uses_email_when_parseable():
    """A JSON body with an email yields the per-email login key."""
    request = _make_http_request(b'{"email": "key@example.com", "password": "x"}')
    assert _email_key(request) == "login:key@example.com"


def test_email_key_falls_back_to_ip_when_body_unparseable():
    """Unparseable cached body must not crash the key function (lines 70-72).

    Covers the defensive ``except`` branch: the client IP is used instead.
    Over HTTP this branch is unreachable (FastAPI rejects bad JSON before the
    slowapi wrapper runs), hence the direct unit test.
    """
    request = _make_http_request(b"this-is-not-json")
    assert _email_key(request) == "203.0.113.7"


def test_email_key_falls_back_to_ip_when_email_field_empty():
    """Valid JSON without an email value falls back to the client IP (line 72)."""
    request = _make_http_request(b'{"email": "", "password": "x"}')
    assert _email_key(request) == "203.0.113.7"


def test_email_key_falls_back_to_ip_when_no_body_cached():
    """No cached body on request.state must not crash the key function."""
    request = _make_http_request(None)
    assert _email_key(request) == "203.0.113.7"


async def test_login_malformed_body_returns_422_without_poisoning_limiter(
    client, db_session
):
    """HTTP-visible companion: a non-JSON login body returns a clean 422.

    The request is rejected by body validation before the limiter key function
    ever runs, and a subsequent well-formed login for the same client still
    succeeds (the failed attempt does not consume the legitimate user's budget
    beyond the IP-keyed fallback bucket).
    """
    await _seed_verified_user(db_session, "fallback-key@example.com", "fallbackuser")

    malformed = await client.post(
        "/api/auth/login",
        content=b"this-is-not-json",
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 422

    valid = await client.post(
        "/api/auth/login",
        json={"email": "fallback-key@example.com", "password": VALID_PASSWORD},
    )
    assert valid.status_code == 200
    assert "access_token" in valid.json()


# ---------------------------------------------------------------------------
# Verify email edge cases
# ---------------------------------------------------------------------------


async def test_verify_email_already_verified_returns_exact_message(
    client, mock_email_service
):
    """Second verification with the same token returns the idempotent message."""
    register = await client.post(
        "/api/auth/register",
        json={
            "email": "twice-verify@example.com",
            "username": "twiceverify",
            "password": VALID_PASSWORD,
        },
    )
    assert register.status_code == 201
    token = _latest_verification_token(mock_email_service)

    first = await client.post("/api/auth/verify-email", json={"token": token})
    assert first.status_code == 200
    assert first.json()["message"] == "Email verified successfully."

    second = await client.post("/api/auth/verify-email", json={"token": token})
    assert second.status_code == 200
    assert second.json()["message"] == "Email already verified."


async def test_verify_password_reset_token_type_returns_400(client):
    """A password-reset token is rejected by verify-email with 400.

    The token type check runs before any identity lookup, so no seeded user
    is required.
    """
    token = create_password_reset_token("some-user-id")
    response = await client.post("/api/auth/verify-email", json={"token": token})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid token type."


async def test_verify_email_unknown_identity_returns_400(client):
    """A valid email-verification token for a user without identity returns 400.

    Covers line 275 (``identity is None`` branch).
    """
    token = create_verification_token("ghost-user-000")
    response = await client.post("/api/auth/verify-email", json={"token": token})
    assert response.status_code == 400
    assert response.json()["detail"] == "User identity not found."


# ---------------------------------------------------------------------------
# Logout edge case
# ---------------------------------------------------------------------------


async def test_logout_with_unknown_token_returns_200_idempotent(client):
    """Logout with an unknown refresh_token cookie is idempotent (200)."""
    client.cookies.set("refresh_token", "completely.unknown.refresh.token")
    response = await client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json()["message"] == "Logged out."
    assert not response.cookies.get("refresh_token")
