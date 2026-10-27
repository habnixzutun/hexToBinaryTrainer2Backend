"""Tests for US-15: Password Reset Flow."""

import hashlib
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import PasswordResetToken, User, UserIdentity, UserRefreshToken
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import JWT_ALGORITHM, get_jwt_secret
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "Password1!"
NEW_VALID_PASSWORD = "NewPass2@"
WEAK_PASSWORD = "short"
EXISTING_EMAIL = "reset-user@example.com"
UNKNOWN_EMAIL = "nobody@example.com"
USER_ID = "user-reset-001"


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def seeded_db(db_session):
    user = User(id=USER_ID, username="resetuser", email=EXISTING_EMAIL)
    db_session.add(user)

    identity = UserIdentity(
        id="identity-reset-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=EXISTING_EMAIL,
        password_hash=hash_password(VALID_PASSWORD),
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)
    await db_session.commit()
    yield db_session


@pytest.fixture
def mock_email_service():
    return MockEmailService()


@pytest.fixture
async def client(engine, seeded_db, mock_email_service):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    from src.routers.auth import set_email_service

    set_email_service(mock_email_service)
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    set_email_service(MockEmailService())


def _make_expired_reset_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "password_reset",
        "exp": datetime.now(UTC) - timedelta(hours=1),
        "iat": datetime.now(UTC) - timedelta(hours=2),
    }
    return pyjwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Request Password Reset tests
# ---------------------------------------------------------------------------


async def test_request_password_reset_existing_email_returns_200(
    client, mock_email_service
):
    """Request with existing email returns 200 and sends reset email."""
    response = await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )
    assert response.status_code == 200
    assert len(mock_email_service.sent) == 1
    assert mock_email_service.sent[0]["to"] == EXISTING_EMAIL
    assert mock_email_service.sent[0]["type"] == "password_reset"


async def test_request_password_reset_unknown_email_returns_200_no_email(
    client, mock_email_service
):
    """Request with unknown email returns 200 but sends no email (no info-leak)."""
    response = await client.post(
        "/api/auth/request-password-reset",
        json={"email": UNKNOWN_EMAIL},
    )
    assert response.status_code == 200
    assert len(mock_email_service.sent) == 0


async def test_request_password_reset_invalid_email_returns_422(client):
    """Malformed email returns 422."""
    response = await client.post(
        "/api/auth/request-password-reset",
        json={"email": "not-an-email"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Reset Password tests
# ---------------------------------------------------------------------------


async def test_reset_password_valid_token_returns_200(client, engine):
    """Valid reset token returns 200 and updates password."""
    # First request a reset to get a valid token
    await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )

    # Retrieve token from DB
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(select(PasswordResetToken))
        record = result.scalars().first()
        assert record is not None

    # We need the raw token — get it from the email service
    from src.routers.auth import get_email_service
    email_service = get_email_service()
    assert hasattr(email_service, "sent")
    raw_token = email_service.sent[0]["token"]

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": NEW_VALID_PASSWORD},
    )
    assert response.status_code == 200


async def test_reset_password_revokes_all_refresh_tokens(client, engine):
    """After password reset, all refresh tokens for the user are revoked."""
    # Seed a refresh token for the user
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        rt = UserRefreshToken(
            id="rt-reset-001",
            user_id=USER_ID,
            token_hash=hashlib.sha256(b"some-token").hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(rt)
        await session.commit()

    # Request reset
    await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )
    from src.routers.auth import get_email_service
    email_service = get_email_service()
    raw_token = email_service.sent[0]["token"]

    await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": NEW_VALID_PASSWORD},
    )

    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        tokens = result.scalars().all()
        assert all(t.revoked_at is not None for t in tokens)


async def test_reset_password_marks_token_as_used(client, engine):
    """After successful reset, used_at is set on the reset token."""
    await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )
    from src.routers.auth import get_email_service
    email_service = get_email_service()
    raw_token = email_service.sent[0]["token"]

    await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": NEW_VALID_PASSWORD},
    )

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(select(PasswordResetToken))
        record = result.scalars().first()
        assert record is not None
        assert record.used_at is not None


async def test_reset_password_expired_token_returns_400(client):
    """Expired reset token returns 400."""
    expired_token = _make_expired_reset_token(USER_ID)
    response = await client.post(
        "/api/auth/reset-password",
        json={"token": expired_token, "new_password": NEW_VALID_PASSWORD},
    )
    assert response.status_code == 400


async def test_reset_password_invalid_token_returns_400(client):
    """Completely invalid token returns 400."""
    response = await client.post(
        "/api/auth/reset-password",
        json={"token": "this.is.not.valid", "new_password": NEW_VALID_PASSWORD},
    )
    assert response.status_code == 400


async def test_reset_password_already_used_token_returns_400(client):
    """Already-used token returns 400."""
    await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )
    from src.routers.auth import get_email_service
    email_service = get_email_service()
    raw_token = email_service.sent[0]["token"]

    # First use — succeeds
    await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": NEW_VALID_PASSWORD},
    )
    # Second use — already used
    response = await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": "AnotherPass3#"},
    )
    assert response.status_code == 400


async def test_reset_password_weak_password_returns_400(client):
    """Weak new password returns 400."""
    await client.post(
        "/api/auth/request-password-reset",
        json={"email": EXISTING_EMAIL},
    )
    from src.routers.auth import get_email_service
    email_service = get_email_service()
    raw_token = email_service.sent[0]["token"]

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": WEAK_PASSWORD},
    )
    assert response.status_code == 400


async def test_reset_password_missing_fields_returns_422(client):
    """Missing required fields returns 422."""
    response = await client.post(
        "/api/auth/reset-password", json={"token": "sometoken"}
    )
    assert response.status_code == 422
