"""Tests for GET /api/user/me: current user info + game stats."""

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
from src.utils.jwt_utils import (
    JWT_ALGORITHM,
    JWT_SECRET,
    create_access_token,
    create_refresh_token,
)

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
    db_session.add(User(id=USER_ID, username=USER_USERNAME, email=USER_EMAIL))
    db_session.add(
        UserIdentity(
            id="identity-me-001",
            user_id=USER_ID,
            provider="email",
            provider_user_id=USER_EMAIL,
            password_hash="secret-hash",
            verified_at=datetime.now(UTC),
        )
    )
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
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def test_me_returns_user_with_zero_stats_before_first_game(client):
    resp = await client.get("/api/user/me", headers=_bearer())

    assert resp.status_code == 200
    assert resp.json() == {
        "id": USER_ID,
        "username": USER_USERNAME,
        "email": USER_EMAIL,
        "correct_count": 0,
        "incorrect_count": 0,
        "points": 0,
    }


async def test_me_returns_stored_stats(client, seeded_db):
    seeded_db.add(
        UserStats(user_id=USER_ID, correct_count=3, incorrect_count=1, points=42)
    )
    await seeded_db.commit()

    resp = await client.get("/api/user/me", headers=_bearer())

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct_count"] == 3
    assert data["incorrect_count"] == 1
    assert data["points"] == 42


async def test_me_reflects_game_actions(client):
    await client.post("/api/game/correct", json={"bits": 8}, headers=_bearer())
    await client.post("/api/game/incorrect", json={"bits": 1}, headers=_bearer())

    data = (await client.get("/api/user/me", headers=_bearer())).json()

    assert data["correct_count"] == 1
    assert data["incorrect_count"] == 1
    assert data["points"] == 4  # 8 - 1 * 4


async def test_me_does_not_expose_password_hash(client):
    resp = await client.get("/api/user/me", headers=_bearer())

    assert set(resp.json()) == {
        "id",
        "username",
        "email",
        "correct_count",
        "incorrect_count",
        "points",
    }
    assert "secret-hash" not in resp.text


async def test_me_without_token_returns_401(client):
    resp = await client.get("/api/user/me")

    assert resp.status_code == 401


async def test_me_with_invalid_token_returns_401(client):
    resp = await client.get(
        "/api/user/me", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert resp.status_code == 401


async def test_me_rejects_expired_token(client):
    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime.now(UTC).timestamp() - 60,
        "iat": datetime.now(UTC).timestamp() - 1200,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    resp = await client.get(
        "/api/user/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token has expired."


async def test_me_rejects_refresh_token(client):
    token = create_refresh_token(USER_ID)
    resp = await client.get(
        "/api/user/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token type."


async def test_me_for_unknown_user_returns_401(client):
    resp = await client.get("/api/user/me", headers=_bearer("does-not-exist"))

    assert resp.status_code == 401
    assert resp.json()["detail"] == "User not found."
