"""Tests for US-16: Email and Username Change endpoints."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.utils.jwt_utils import (
    JWT_ALGORITHM,
    JWT_SECRET,
    create_access_token,
    create_email_change_token,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-us16-001"
USER_EMAIL = "original@example.com"
USER_USERNAME = "originaluser"
NEW_EMAIL = "newemail@example.com"
TAKEN_EMAIL = "taken@example.com"
TAKEN_USERNAME = "takenuser"
NEW_USERNAME = "newusername"


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
    user = User(id=USER_ID, username=USER_USERNAME, email=USER_EMAIL)
    db_session.add(user)

    identity = UserIdentity(
        id="identity-us16-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=USER_EMAIL,
        password_hash="irrelevant",
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)

    taken_user = User(id="user-taken-001", username=TAKEN_USERNAME, email=TAKEN_EMAIL)
    db_session.add(taken_user)

    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seeded_db):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _bearer(user_id: str = USER_ID) -> dict:
    token = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _expired_bearer() -> dict:
    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime.now(UTC) - timedelta(minutes=1),
        "iat": datetime.now(UTC) - timedelta(minutes=16),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# PATCH /api/user/email — request email change
# ---------------------------------------------------------------------------


async def test_email_change_request_success(client):
    """Valid bearer + new email returns 200 and sends verification email."""
    from src.routers.user import get_email_service
    from src.services.email_service import MockEmailService

    mock = MockEmailService()
    app.dependency_overrides[get_email_service] = lambda: mock

    response = await client.patch(
        "/api/user/email",
        json={"new_email": NEW_EMAIL},
        headers=_bearer(),
    )
    assert response.status_code == 200
    assert any(m["type"] == "email_change_verification" for m in mock.sent)

    app.dependency_overrides.pop(get_email_service, None)


async def test_email_change_request_taken_email_returns_409(client):
    """Requesting change to an already-registered email returns 409."""
    response = await client.patch(
        "/api/user/email",
        json={"new_email": TAKEN_EMAIL},
        headers=_bearer(),
    )
    assert response.status_code == 409


async def test_email_change_request_no_token_returns_401(client):
    """Missing Bearer token returns 401."""
    response = await client.patch("/api/user/email", json={"new_email": NEW_EMAIL})
    assert response.status_code == 401


async def test_email_change_request_invalid_token_returns_401(client):
    """Invalid Bearer token returns 401."""
    response = await client.patch(
        "/api/user/email",
        json={"new_email": NEW_EMAIL},
        headers={"Authorization": "Bearer not.a.valid.token"},
    )
    assert response.status_code == 401


async def test_email_change_request_expired_token_returns_401(client):
    """Expired Bearer token returns 401."""
    response = await client.patch(
        "/api/user/email",
        json={"new_email": NEW_EMAIL},
        headers=_expired_bearer(),
    )
    assert response.status_code == 401


async def test_email_change_request_invalid_email_returns_422(client):
    """Malformed email returns 422."""
    response = await client.patch(
        "/api/user/email",
        json={"new_email": "not-an-email"},
        headers=_bearer(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/auth/verify-email-change — verify new email
# ---------------------------------------------------------------------------


async def test_verify_email_change_success(client, engine):
    """Valid email_change token updates email in DB and returns 200."""
    token = create_email_change_token(USER_ID, NEW_EMAIL)

    response = await client.post(
        "/api/auth/verify-email-change",
        json={"token": token},
    )
    assert response.status_code == 200

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(User).where(User.id == USER_ID)
        )
        user = result.scalar_one()
        assert user.email == NEW_EMAIL


async def test_verify_email_change_expired_token_returns_400(client):
    """Expired email_change token returns 400."""
    payload = {
        "sub": USER_ID,
        "type": "email_change",
        "new_email": NEW_EMAIL,
        "exp": datetime.now(UTC) - timedelta(hours=1),
        "iat": datetime.now(UTC) - timedelta(hours=25),
    }
    expired_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    response = await client.post(
        "/api/auth/verify-email-change",
        json={"token": expired_token},
    )
    assert response.status_code == 400


async def test_verify_email_change_invalid_token_returns_400(client):
    """Garbage token returns 400."""
    response = await client.post(
        "/api/auth/verify-email-change",
        json={"token": "garbage.token.value"},
    )
    assert response.status_code == 400


async def test_verify_email_change_wrong_type_returns_400(client):
    """Token with wrong type field returns 400."""
    wrong_type_token = create_access_token(USER_ID)
    response = await client.post(
        "/api/auth/verify-email-change",
        json={"token": wrong_type_token},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/user/username — change username
# ---------------------------------------------------------------------------


async def test_username_change_success(client, engine):
    """Valid bearer + new username returns 200 and updates DB."""
    response = await client.patch(
        "/api/user/username",
        json={"new_username": NEW_USERNAME},
        headers=_bearer(),
    )
    assert response.status_code == 200

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(User).where(User.id == USER_ID)
        )
        user = result.scalar_one()
        assert user.username == NEW_USERNAME
        assert user.last_username_change_at is not None


async def test_username_change_taken_username_returns_409(client):
    """Requesting change to an already-taken username returns 409."""
    response = await client.patch(
        "/api/user/username",
        json={"new_username": TAKEN_USERNAME},
        headers=_bearer(),
    )
    assert response.status_code == 409


async def test_username_change_within_7_days_returns_429(client, engine):
    """Username change within 7-day cooldown returns 429 with next_change_at."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == USER_ID))
        user = result.scalar_one()
        user.last_username_change_at = datetime.now(UTC) - timedelta(days=3)
        await session.commit()

    response = await client.patch(
        "/api/user/username",
        json={"new_username": NEW_USERNAME},
        headers=_bearer(),
    )
    assert response.status_code == 429
    data = response.json()
    assert "next_change_at" in data["detail"]


async def test_username_change_no_token_returns_401(client):
    """Missing Bearer token returns 401."""
    response = await client.patch(
        "/api/user/username", json={"new_username": NEW_USERNAME}
    )
    assert response.status_code == 401


async def test_username_change_invalid_token_returns_401(client):
    """Invalid Bearer token returns 401."""
    response = await client.patch(
        "/api/user/username",
        json={"new_username": NEW_USERNAME},
        headers={"Authorization": "Bearer bad.token.here"},
    )
    assert response.status_code == 401


async def test_username_change_expired_token_returns_401(client):
    """Expired Bearer token returns 401."""
    response = await client.patch(
        "/api/user/username",
        json={"new_username": NEW_USERNAME},
        headers=_expired_bearer(),
    )
    assert response.status_code == 401
