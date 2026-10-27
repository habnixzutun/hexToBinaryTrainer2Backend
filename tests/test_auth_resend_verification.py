"""Tests for POST /api/auth/resend-verification (US-04).

Tests silent handling: endpoint always returns the same message regardless
of whether the email exists, is verified, or is unverified. This prevents
email enumeration attacks.
"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.routers.auth import get_email_service, set_email_service
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import create_verification_token
from uuid_utils import uuid7

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


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


GENERIC_RESEND_MESSAGE = (
    "If this email is registered and unverified, a new mail has been sent."
)


class TestResendVerification:
    """Tests for /resend-verification endpoint."""

    async def test_resend_verification_nonexistent_email(self, client):
        """Non-existent email should return generic message (silent handling)."""
        response = await client.post(
            "/api/auth/resend-verification",
            json={"email": "nonexistent@example.com"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == GENERIC_RESEND_MESSAGE

    async def test_resend_verification_already_verified_user(self, client, session):
        """Already verified user should return generic message."""
        # Create a verified user
        user_id = str(uuid7())
        user = User(id=user_id, username="verifieduser", email="verified@example.com")
        session.add(user)

        identity = UserIdentity(
            id=str(uuid7()),
            user_id=user_id,
            provider="email",
            provider_user_id="verified@example.com",
            password_hash="hash",
            verified_at=datetime.now(UTC),  # Already verified
        )
        session.add(identity)
        await session.commit()

        response = await client.post(
            "/api/auth/resend-verification",
            json={"email": "verified@example.com"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == GENERIC_RESEND_MESSAGE

    async def test_resend_verification_unverified_user_sends_email(
        self, client, session, mock_email_service
    ):
        """Unverified user should receive new verification email."""
        # Create an unverified user
        user_id = str(uuid7())
        user = User(
            id=user_id, username="unverifieduser", email="unverified@example.com"
        )
        session.add(user)

        identity = UserIdentity(
            id=str(uuid7()),
            user_id=user_id,
            provider="email",
            provider_user_id="unverified@example.com",
            password_hash="hash",
            verified_at=None,  # Not verified
        )
        session.add(identity)
        await session.commit()

        response = await client.post(
            "/api/auth/resend-verification",
            json={"email": "unverified@example.com"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == GENERIC_RESEND_MESSAGE

        # Verify email was sent
        assert len(mock_email_service.sent) == 1
        assert mock_email_service.sent[0]["to"] == "unverified@example.com"
        assert mock_email_service.sent[0]["type"] == "verification"

    async def test_resend_verification_rate_limit(self, client, session):
        """Rate limit should block after 3 attempts per IP+email."""
        # Create an unverified user
        user_id = str(uuid7())
        user = User(id=user_id, username="testuser", email="test@example.com")
        session.add(user)

        identity = UserIdentity(
            id=str(uuid7()),
            user_id=user_id,
            provider="email",
            provider_user_id="test@example.com",
            password_hash="hash",
            verified_at=None,
        )
        session.add(identity)
        await session.commit()

        # First 3 attempts should succeed
        for i in range(3):
            response = await client.post(
                "/api/auth/resend-verification",
                json={"email": "test@example.com"},
            )
            assert response.status_code == 200, f"Attempt {i+1} failed"

        # 4th attempt should be rate limited
        response = await client.post(
            "/api/auth/resend-verification",
            json={"email": "test@example.com"},
        )
        assert response.status_code == 429
        assert "Too many requests" in response.json()["detail"]

    async def test_resend_verification_invalid_email_format(self, client):
        """Invalid email format should return 422."""
        response = await client.post(
            "/api/auth/resend-verification",
            json={"email": "not-an-email"},
        )

        assert response.status_code == 422

    async def test_resend_verification_missing_email(self, client):
        """Missing email field should return 422."""
        response = await client.post(
            "/api/auth/resend-verification",
            json={},
        )

        assert response.status_code == 422


class TestResendVerificationSilentHandling:
    """Tests to verify silent handling prevents email enumeration."""

    async def test_all_responses_identical(self, client, session, mock_email_service):
        """All responses should be identical regardless of email status."""
        # Create test users
        unverified_id = str(uuid7())
        unverified = User(
            id=unverified_id, username="unverified", email="unverified@example.com"
        )
        session.add(unverified)
        session.add(
            UserIdentity(
                id=str(uuid7()),
                user_id=unverified_id,
                provider="email",
                provider_user_id="unverified@example.com",
                password_hash="hash",
                verified_at=None,
            )
        )

        verified_id = str(uuid7())
        verified = User(
            id=verified_id, username="verified", email="verified@example.com"
        )
        session.add(verified)
        session.add(
            UserIdentity(
                id=str(uuid7()),
                user_id=verified_id,
                provider="email",
                provider_user_id="verified@example.com",
                password_hash="hash",
                verified_at=datetime.now(UTC),
            )
        )

        await session.commit()

        # Test all scenarios
        scenarios = [
            ("nonexistent@example.com", "non-existent email"),
            ("verified@example.com", "verified email"),
            ("unverified@example.com", "unverified email"),
        ]

        responses = []
        for email, scenario in scenarios:
            response = await client.post(
                "/api/auth/resend-verification",
                json={"email": email},
            )
            responses.append((scenario, response.status_code, response.json()))

        # All responses should be identical
        first_status = responses[0][1]
        first_message = responses[0][2]["message"]

        for scenario, status_code, body in responses:
            assert status_code == first_status, (
                f"{scenario}: expected status {first_status}, got {status_code}"
            )
            assert body["message"] == first_message, (
                f"{scenario}: expected message '{first_message}', got '{body['message']}'"
            )
