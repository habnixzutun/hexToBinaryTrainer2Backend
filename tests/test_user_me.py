"""Tests for GET /api/user/me — current user profile + game stats."""

from datetime import UTC, datetime

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.models.game import UserStats
from src.utils.jwt_utils import JWT_ALGORITHM, JWT_SECRET, create_access_token

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-me-001"
USER_EMAIL = "me@example.com"
USER_USERNAME = "meuser"


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
        id="identity-me-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=USER_EMAIL,
        password_hash="irrelevant",
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)

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


# ---------------------------------------------------------------------------
# GET /api/user/me — success cases
# ---------------------------------------------------------------------------


async def test_me_returns_user_with_zero_stats_when_no_stats_row(client):
    """A user without a user_stats row gets zeroed counters."""
    response = await client.get("/api/user/me", headers=_bearer())

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == USER_ID
    assert data["username"] == USER_USERNAME
    assert data["email"] == USER_EMAIL
    assert data["correct_count"] == 0
    assert data["incorrect_count"] == 0
    assert data["points"] == 0


async def test_me_returns_user_with_stats(client, seeded_db):
    """A user with a user_stats row gets the stored counters."""
    seeded_db.add(
        UserStats(
            user_id=USER_ID,
            correct_count=7,
            incorrect_count=2,
            points=42,
        )
    )
    await seeded_db.commit()

    response = await client.get("/api/user/me", headers=_bearer())

    assert response.status_code == 200
    data = response.json()
    assert data["correct_count"] == 7
    assert data["incorrect_count"] == 2
    assert data["points"] == 42


# ---------------------------------------------------------------------------
# GET /api/user/me — auth failure cases
# ---------------------------------------------------------------------------


async def test_me_requires_auth_header(client):
    """Missing Authorization header returns 401."""
    response = await client.get("/api/user/me")
    assert response.status_code == 401


async def test_me_rejects_invalid_token(client):
    """Garbage token returns 401."""
    response = await client.get(
        "/api/user/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_me_rejects_expired_token(client):
    """Expired access token returns 401."""
    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime.now(UTC).timestamp() - 60,
        "iat": datetime.now(UTC).timestamp() - 1200,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    response = await client.get(
        "/api/user/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


async def test_me_returns_404_for_unknown_user(client):
    """Valid token for a non-existent user returns 404."""
    response = await client.get("/api/user/me", headers=_bearer("ghost-user-id"))
    assert response.status_code == 404
