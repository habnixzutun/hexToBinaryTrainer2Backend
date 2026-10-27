"""Tests for POST /api/auth/register and POST /api/auth/verify-email (US-13)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity  # noqa: F401
from src.routers.auth import (
    get_email_service,
    set_email_service,
)
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import JWT_ALGORITHM, JWT_SECRET

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def async_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
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
async def client(async_engine, mock_email_service):
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


VALID_PAYLOAD = {
    "email": "user@example.com",
    "username": "testuser",
    "password": "StrongPass1!",
}


class TestRegister:
    async def test_successful_registration_returns_201(self, client):
        response = await client.post("/api/auth/register", json=VALID_PAYLOAD)
        assert response.status_code == 201
        data = response.json()
        assert data["message"] == (
            "Registration successful. Check your email to verify your account."
        )
        assert "user_id" in data

    async def test_successful_registration_sends_verification_email(
        self, client, mock_email_service
    ):
        await client.post("/api/auth/register", json=VALID_PAYLOAD)
        assert len(mock_email_service.sent) == 1
        assert mock_email_service.sent[0]["to"] == VALID_PAYLOAD["email"]
        assert mock_email_service.sent[0]["type"] == "verification"

    async def test_duplicate_email_returns_409(self, client):
        await client.post("/api/auth/register", json=VALID_PAYLOAD)
        second = {**VALID_PAYLOAD, "username": "otheruser"}
        response = await client.post("/api/auth/register", json=second)
        assert response.status_code == 409
        assert "Email" in response.json()["detail"]

    async def test_duplicate_username_returns_409(self, client):
        await client.post("/api/auth/register", json=VALID_PAYLOAD)
        second = {**VALID_PAYLOAD, "email": "other@example.com"}
        response = await client.post("/api/auth/register", json=second)
        assert response.status_code == 409
        assert "Username" in response.json()["detail"]

    async def test_weak_password_returns_400(self, client):
        payload = {**VALID_PAYLOAD, "password": "weak"}
        response = await client.post("/api/auth/register", json=payload)
        assert response.status_code == 400
        assert "errors" in response.json()["detail"]

    async def test_password_missing_uppercase_returns_400(self, client):
        payload = {**VALID_PAYLOAD, "password": "nouppercase1!"}
        response = await client.post("/api/auth/register", json=payload)
        assert response.status_code == 400

    async def test_password_missing_special_char_returns_400(self, client):
        payload = {**VALID_PAYLOAD, "password": "NoSpecial1"}
        response = await client.post("/api/auth/register", json=payload)
        assert response.status_code == 400

    async def test_rate_limit_returns_429_after_three_attempts(
        self, client, async_engine
    ):
        """Rate limit is per IP+email. Test via service directly."""
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.services.rate_limit_service import RateLimitService

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            rate_limit = RateLimitService(session)

            # Simulate 3 registration attempts with same IP+email
            for _ in range(3):
                await rate_limit.check(
                    "register:testclient:user@example.com",
                    limit=3,
                    window_seconds=3600,
                )

            # 4th attempt should be blocked
            is_allowed = await rate_limit.check(
                "register:testclient:user@example.com",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is False

    async def test_invalid_email_format_returns_422(self, client):
        payload = {**VALID_PAYLOAD, "email": "not-an-email"}
        response = await client.post("/api/auth/register", json=payload)
        assert response.status_code == 422

    async def test_username_too_short_returns_422(self, client):
        payload = {**VALID_PAYLOAD, "username": "ab"}
        response = await client.post("/api/auth/register", json=payload)
        assert response.status_code == 422

    async def test_missing_fields_returns_422(self, client):
        response = await client.post("/api/auth/register", json={})
        assert response.status_code == 422


class TestVerifyEmail:
    def _make_verification_token(self, user_id: str, expired: bool = False) -> str:
        exp = (
            datetime.now(UTC) - timedelta(hours=1)
            if expired
            else datetime.now(UTC) + timedelta(hours=24)
        )
        payload = {
            "sub": user_id,
            "type": "email_verification",
            "exp": exp,
            "iat": datetime.now(UTC),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    async def _register(self, client) -> str:
        response = await client.post("/api/auth/register", json=VALID_PAYLOAD)
        return response.json()["user_id"]

    async def test_successful_verification_returns_200(self, client):
        user_id = await self._register(client)
        token = self._make_verification_token(user_id)
        response = await client.post("/api/auth/verify-email", json={"token": token})
        assert response.status_code == 200
        assert "verified" in response.json()["message"].lower()

    async def test_expired_token_returns_400(self, client):
        user_id = await self._register(client)
        token = self._make_verification_token(user_id, expired=True)
        response = await client.post("/api/auth/verify-email", json={"token": token})
        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower()

    async def test_invalid_token_returns_400(self, client):
        response = await client.post(
            "/api/auth/verify-email", json={"token": "not.a.valid.token"}
        )
        assert response.status_code == 400

    async def test_wrong_token_type_returns_400(self, client):
        user_id = await self._register(client)
        payload = {
            "sub": user_id,
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iat": datetime.now(UTC),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        response = await client.post("/api/auth/verify-email", json={"token": token})
        assert response.status_code == 400

    async def test_already_verified_returns_200_idempotent(self, client):
        user_id = await self._register(client)
        token = self._make_verification_token(user_id)
        await client.post("/api/auth/verify-email", json={"token": token})
        response = await client.post("/api/auth/verify-email", json={"token": token})
        assert response.status_code == 200

    async def test_missing_token_returns_422(self, client):
        response = await client.post("/api/auth/verify-email", json={})
        assert response.status_code == 422
