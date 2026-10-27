"""Tests for POST /api/auth/resend-verification endpoint (US-04/US-08).

Acceptance Criteria:
- Valid resend for unverified user → 200, email sent
- Rate limit after 3 requests → 429
- Email not registered → 200 (silent, no email)
- Email already verified → 200 (silent, no email)
- Invalid email format → 422
- Silent handling prevents email enumeration
"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import RateLimitAttempt, User, UserIdentity
from src.routers.auth import get_email_service, set_email_service
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import create_verification_token
from src.utils.password import hash_password
from uuid_utils import uuid7

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
VALID_PASSWORD = "StrongPass1!"

GENERIC_RESEND_MESSAGE = (
    "If this email is registered and unverified, a new mail has been sent."
)


@pytest.fixture
async def async_engine():
    """Create in-memory SQLite engine."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    """Create async session."""
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture(autouse=True)
async def cleanup_rate_limits(session):
    """Clean up rate limit attempts before and after each test."""
    await session.execute(delete(RateLimitAttempt))
    await session.commit()
    yield
    await session.execute(delete(RateLimitAttempt))
    await session.commit()


@pytest.fixture
def mock_email_service():
    """Mock email service for testing."""
    svc = MockEmailService()
    set_email_service(svc)
    yield svc
    set_email_service(MockEmailService())


@pytest.fixture
async def client(async_engine, mock_email_service):
    """Create test client."""
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_email_service] = lambda: mock_email_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_unverified_user(
    session: AsyncSession, email: str, username: str
) -> str:
    """Insert a user with an unverified email identity; returns the user id."""
    user_id = f"user-{username}"
    session.add(User(id=user_id, username=username, email=email))
    session.add(
        UserIdentity(
            id=f"identity-{username}",
            user_id=user_id,
            provider="email",
            provider_user_id=email,
            password_hash=hash_password(VALID_PASSWORD),
            verified_at=None,
        )
    )
    await session.commit()
    return user_id


async def _seed_verified_user(
    session: AsyncSession, email: str, username: str
) -> str:
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


def _count_verification_emails(svc: MockEmailService) -> int:
    """Count verification emails sent."""
    return sum(1 for mail in svc.sent if mail.get("type") == "verification")


def _latest_verification_token(svc: MockEmailService) -> str:
    """Extract the token of the most recent verification email."""
    for mail in reversed(svc.sent):
        if mail.get("type") == "verification":
            return mail["token"]
    raise AssertionError("No verification email was sent")


# ---------------------------------------------------------------------------
# US-08: Email Verification Resend Tests
# ---------------------------------------------------------------------------


async def test_resend_verification_unverified_user_sends_email(
    client, session, mock_email_service
):
    """Valid resend for unverified user → 200, email sent."""
    await _seed_unverified_user(session, "unverified@example.com", "unverified_user")
    initial_email_count = _count_verification_emails(mock_email_service)

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "unverified@example.com"}
    )

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RESEND_MESSAGE}
    assert _count_verification_emails(mock_email_service) == initial_email_count + 1


async def test_resend_verification_rate_limit_after_3_requests(
    client, session, mock_email_service
):
    """Rate limit after 3 requests → 429."""
    await _seed_unverified_user(session, "ratelimit@example.com", "ratelimit_user")

    for i in range(3):
        response = await client.post(
            "/api/auth/resend-verification", json={"email": "ratelimit@example.com"}
        )
        assert response.status_code == 200, f"Request {i + 1} should succeed"

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "ratelimit@example.com"}
    )

    assert response.status_code == 429
    assert "Too many requests" in response.json()["detail"]


async def test_resend_verification_email_not_registered_returns_200_silent(
    client, mock_email_service
):
    """Email not registered → 200 (silent, no email sent)."""
    initial_email_count = _count_verification_emails(mock_email_service)

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "nonexistent@example.com"}
    )

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RESEND_MESSAGE}
    assert _count_verification_emails(mock_email_service) == initial_email_count


async def test_resend_verification_email_already_verified_returns_200_silent(
    client, session, mock_email_service
):
    """Email already verified → 200 (silent, no email sent)."""
    await _seed_verified_user(session, "verified@example.com", "verified_user")
    initial_email_count = _count_verification_emails(mock_email_service)

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "verified@example.com"}
    )

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RESEND_MESSAGE}
    assert _count_verification_emails(mock_email_service) == initial_email_count


async def test_resend_verification_invalid_email_format_returns_422(client):
    """Invalid email format → 422."""
    response = await client.post(
        "/api/auth/resend-verification", json={"email": "not-an-email"}
    )

    assert response.status_code == 422


async def test_resend_verification_missing_email(client):
    """Missing email field should return 422."""
    response = await client.post("/api/auth/resend-verification", json={})

    assert response.status_code == 422


async def test_resend_verification_generates_new_token(
    client, session, mock_email_service
):
    """Resend generates a new verification token."""
    await _seed_unverified_user(session, "newtoken@example.com", "newtoken_user")

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "newtoken@example.com"}
    )
    assert response.status_code == 200

    token = _latest_verification_token(mock_email_service)
    assert token is not None
    assert len(token) > 0


async def test_resend_verification_different_emails_separate_rate_limits(
    client, session, mock_email_service
):
    """Different emails have separate rate limits."""
    await _seed_unverified_user(session, "user1@example.com", "user1")
    await _seed_unverified_user(session, "user2@example.com", "user2")

    # Exhaust rate limit for email1
    for _ in range(3):
        response = await client.post(
            "/api/auth/resend-verification", json={"email": "user1@example.com"}
        )
        assert response.status_code == 200

    # email1 is rate limited
    response = await client.post(
        "/api/auth/resend-verification", json={"email": "user1@example.com"}
    )
    assert response.status_code == 429

    # email2 is NOT rate limited (separate limit)
    response = await client.post(
        "/api/auth/resend-verification", json={"email": "user2@example.com"}
    )
    assert response.status_code == 200


async def test_resend_verification_email_send_failure_silent(
    client, session, mock_email_service
):
    """Email send failure is handled silently (no error response)."""
    await _seed_unverified_user(session, "silent-fail@example.com", "silent_fail_user")

    async def failing_send(*args, **kwargs):
        raise Exception("SMTP connection failed")

    mock_email_service.send_verification_email = failing_send

    response = await client.post(
        "/api/auth/resend-verification", json={"email": "silent-fail@example.com"}
    )

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RESEND_MESSAGE}


# ---------------------------------------------------------------------------
# US-04: Silent handling prevents email enumeration
# ---------------------------------------------------------------------------


async def test_all_responses_identical(client, session, mock_email_service):
    """All responses should be identical regardless of email status."""
    await _seed_unverified_user(session, "unverified@example.com", "unverified")
    await _seed_verified_user(session, "verified@example.com", "verified")

    scenarios = [
        ("nonexistent@example.com", "non-existent email"),
        ("verified@example.com", "verified email"),
        ("unverified@example.com", "unverified email"),
    ]

    responses = []
    for email, scenario in scenarios:
        response = await client.post(
            "/api/auth/resend-verification", json={"email": email}
        )
        responses.append((scenario, response.status_code, response.json()))

    first_status = responses[0][1]
    first_message = responses[0][2]["message"]

    for scenario, status_code, body in responses:
        assert status_code == first_status, (
            f"{scenario}: expected status {first_status}, got {status_code}"
        )
        assert body["message"] == first_message, (
            f"{scenario}: expected message '{first_message}', got '{body['message']}'"
        )
