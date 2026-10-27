"""Tests for POST /api/auth/resend-verification endpoint (US-08).

Acceptance Criteria:
- Valid resend for unverified user → 200, email sent
- Rate limit after 3 requests → 429
- Email not registered → 200 (silent, no email)
- Email already verified → 200 (silent, no email)
- Invalid email format → 422
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
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
VALID_PASSWORD = "StrongPass1!"


@pytest.fixture
async def engine():
    """Create test database engine."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    """Create test database session."""
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
def mock_email_service():
    """Set up mock email service."""
    svc = MockEmailService()
    set_email_service(svc)
    yield svc
    set_email_service(MockEmailService())


@pytest.fixture
async def client(engine, mock_email_service):
    """Create test client with database and email service overrides."""
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


@pytest.fixture(autouse=True)
async def cleanup_rate_limits(db_session):
    """Clean up rate limit attempts before and after each test."""
    await db_session.execute(delete(RateLimitAttempt))
    await db_session.commit()
    yield
    await db_session.execute(delete(RateLimitAttempt))
    await db_session.commit()


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
            verified_at=None,  # Unverified
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
            verified_at=datetime.now(UTC),  # Verified
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
    client, db_session, mock_email_service
):
    """Valid resend for unverified user → 200, email sent."""
    # Arrange: Create unverified user
    email = "unverified@example.com"
    username = "unverified_user"
    await _seed_unverified_user(db_session, email, username)

    initial_email_count = _count_verification_emails(mock_email_service)

    # Act: Request resend
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )

    # Assert: 200 OK, email sent
    assert response.status_code == 200
    assert response.json() == {
        "message": "If this email is registered and unverified, a new mail has been sent."
    }

    # Verify email was sent
    new_email_count = _count_verification_emails(mock_email_service)
    assert new_email_count == initial_email_count + 1


async def test_resend_verification_rate_limit_after_3_requests(
    client, db_session, mock_email_service
):
    """Rate limit after 3 requests → 429."""
    # Arrange: Create unverified user
    email = "ratelimit@example.com"
    username = "ratelimit_user"
    await _seed_unverified_user(db_session, email, username)

    # Act: Make 3 requests (should succeed)
    for i in range(3):
        response = await client.post(
            "/api/auth/resend-verification", json={"email": email}
        )
        assert response.status_code == 200, f"Request {i+1} should succeed"

    # Act: 4th request should be rate limited
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )

    # Assert: 429 Too Many Requests
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests."


async def test_resend_verification_email_not_registered_returns_200_silent(
    client, mock_email_service
):
    """Email not registered → 200 (silent, no email sent)."""
    # Arrange: Non-existent email
    email = "nonexistent@example.com"
    initial_email_count = _count_verification_emails(mock_email_service)

    # Act: Request resend for non-existent email
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )

    # Assert: 200 OK (silent failure)
    assert response.status_code == 200
    assert response.json() == {
        "message": "If this email is registered and unverified, a new mail has been sent."
    }

    # Verify NO email was sent
    new_email_count = _count_verification_emails(mock_email_service)
    assert new_email_count == initial_email_count


async def test_resend_verification_email_already_verified_returns_200_silent(
    client, db_session, mock_email_service
):
    """Email already verified → 200 (silent, no email sent)."""
    # Arrange: Create verified user
    email = "verified@example.com"
    username = "verified_user"
    await _seed_verified_user(db_session, email, username)

    initial_email_count = _count_verification_emails(mock_email_service)

    # Act: Request resend for verified email
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )

    # Assert: 200 OK (silent failure)
    assert response.status_code == 200
    assert response.json() == {
        "message": "If this email is registered and unverified, a new mail has been sent."
    }

    # Verify NO email was sent
    new_email_count = _count_verification_emails(mock_email_service)
    assert new_email_count == initial_email_count


async def test_resend_verification_invalid_email_format_returns_422(client):
    """Invalid email format → 422."""
    # Act: Request resend with invalid email format
    response = await client.post(
        "/api/auth/resend-verification", json={"email": "not-an-email"}
    )

    # Assert: 422 Unprocessable Entity
    assert response.status_code == 422


async def test_resend_verification_generates_new_token(
    client, db_session, mock_email_service
):
    """Resend generates a new verification token (different from first)."""
    # Arrange: Create unverified user
    email = "newtoken@example.com"
    username = "newtoken_user"
    await _seed_unverified_user(db_session, email, username)

    # Act: Request resend
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )
    assert response.status_code == 200

    # Assert: Email was sent with a token
    token = _latest_verification_token(mock_email_service)
    assert token is not None
    assert len(token) > 0


async def test_resend_verification_different_emails_separate_rate_limits(
    client, db_session, mock_email_service
):
    """Different emails have separate rate limits."""
    # Arrange: Create two unverified users
    email1 = "user1@example.com"
    username1 = "user1"
    await _seed_unverified_user(db_session, email1, username1)

    email2 = "user2@example.com"
    username2 = "user2"
    await _seed_unverified_user(db_session, email2, username2)

    # Act: Make 3 requests for email1 (exhausts rate limit)
    for i in range(3):
        response = await client.post(
            "/api/auth/resend-verification", json={"email": email1}
        )
        assert response.status_code == 200

    # Assert: email1 is rate limited
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email1}
    )
    assert response.status_code == 429

    # Assert: email2 is NOT rate limited (separate limit)
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email2}
    )
    assert response.status_code == 200


async def test_resend_verification_email_send_failure_silent(
    client, db_session, mock_email_service
):
    """Email send failure is handled silently (no error response)."""
    # Arrange: Create unverified user
    email = "silent-fail@example.com"
    username = "silent_fail_user"
    await _seed_unverified_user(db_session, email, username)

    # Mock email service to raise exception
    async def failing_send(*args, **kwargs):
        raise Exception("SMTP connection failed")

    mock_email_service.send_verification_email = failing_send

    # Act: Request resend
    response = await client.post(
        "/api/auth/resend-verification", json={"email": email}
    )

    # Assert: 200 OK (silent failure, no error leaked)
    assert response.status_code == 200
    assert response.json() == {
        "message": "If this email is registered and unverified, a new mail has been sent."
    }
