"""Tests for game endpoints: score tracking and leaderboard."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.models.game import UserStats
from src.utils.jwt_utils import create_access_token

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-game-001"
USER_EMAIL = "game_test@example.com"
USER_USERNAME = "GameTester"


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


def _add_verified_user(session: AsyncSession, user_id: str, username: str, email: str):
    session.add(User(id=user_id, username=username, email=email))
    session.add(
        UserIdentity(
            id=f"identity-{user_id}",
            user_id=user_id,
            provider="email",
            provider_user_id=email,
            password_hash="irrelevant",
            verified_at=datetime.now(UTC),
        )
    )


@pytest.fixture
async def seeded_db(db_session):
    _add_verified_user(db_session, USER_ID, USER_USERNAME, USER_EMAIL)
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


@pytest.fixture
async def auth_client(client):
    """Test client authenticated as the seeded user."""
    client.headers["Authorization"] = f"Bearer {create_access_token(USER_ID)}"
    return client


async def test_record_correct_answer(auth_client: AsyncClient):
    """POST /api/game/correct should record correct answer and award bits as points."""
    response = await auth_client.post("/api/game/correct", json={"bits": 8})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Correct answer recorded"
    assert data["user_id"] == USER_ID
    assert data["correct_count"] == 1
    assert data["incorrect_count"] == 0
    assert data["points"] == 8  # bits added directly as points


async def test_record_incorrect_answer(auth_client: AsyncClient):
    """POST /api/game/incorrect should increment incorrect count and deduct bits*4."""
    response = await auth_client.post("/api/game/incorrect", json={"bits": 4})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Incorrect answer recorded"
    assert data["correct_count"] == 0
    assert data["incorrect_count"] == 1
    assert data["points"] == -16  # -(4 bits * 4)


async def test_multiple_answers_accumulate(auth_client: AsyncClient):
    """Multiple game actions should accumulate stats correctly."""
    await auth_client.post("/api/game/correct", json={"bits": 4})  # +4
    await auth_client.post("/api/game/correct", json={"bits": 8})  # +8
    await auth_client.post("/api/game/incorrect", json={"bits": 4})  # -16

    response = await auth_client.post("/api/game/correct", json={"bits": 12})  # +12
    data = response.json()

    assert data["correct_count"] == 3
    assert data["incorrect_count"] == 1
    assert data["points"] == 8  # 4 + 8 - 16 + 12 = 8


@pytest.mark.parametrize("endpoint", ["/api/game/correct", "/api/game/incorrect"])
async def test_game_action_without_token_returns_401(client: AsyncClient, endpoint):
    """Game actions are protected and require a bearer token."""
    response = await client.post(endpoint, json={"bits": 8})

    assert response.status_code == 401


async def test_leaderboard_returns_wrapped_response(auth_client: AsyncClient):
    """GET /api/game/leaderboard should return wrapped entries array."""
    await auth_client.post("/api/game/correct", json={"bits": 16})

    response = await auth_client.get("/api/game/leaderboard")

    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)
    assert len(data["entries"]) == 1

    entry = data["entries"][0]
    assert entry == {
        "user_id": USER_ID,
        "username": USER_USERNAME,
        "correct_count": 1,
        "incorrect_count": 0,
        "points": 16,  # 16 bits = 16 points
    }


async def test_leaderboard_sorted_by_points(client: AsyncClient, seeded_db):
    """Leaderboard should be sorted by points descending (public, no token)."""
    _add_verified_user(seeded_db, "user-p1", "Player1", "player1@example.com")
    _add_verified_user(seeded_db, "user-p2", "Player2", "player2@example.com")
    seeded_db.add_all(
        [
            UserStats(user_id="user-p1", correct_count=1, points=50),
            UserStats(user_id="user-p2", correct_count=2, points=200),
        ]
    )
    await seeded_db.commit()

    response = await client.get("/api/game/leaderboard")

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [e["username"] for e in entries] == ["Player2", "Player1"]
    assert [e["points"] for e in entries] == [200, 50]
