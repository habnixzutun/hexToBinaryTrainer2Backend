"""Tests for game endpoints: score tracking and leaderboard."""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import app
from src.models.auth import User
from src.models.game import UserStats
from src.services.auth_service import create_user


@pytest.fixture
async def test_user(session: AsyncSession) -> User:
    """Create a test user."""
    user = await create_user(
        session=session,
        email="game_test@example.com",
        username="GameTester",
        password="Test1234!",
    )
    await session.commit()
    return user


@pytest.fixture
async def auth_client(test_user: User) -> AsyncClient:
    """Create an authenticated test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_response = await client.post(
            "/api/auth/login",
            json={"email": "game_test@example.com", "password": "Test1234!"},
        )
        token = login_response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest.mark.asyncio
async def test_record_correct_answer(auth_client: AsyncClient, session: AsyncSession):
    """POST /api/game/correct should record correct answer and award bits as points."""
    response = await auth_client.post("/api/game/correct", json={"bits": 8})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Correct answer recorded"
    assert data["user_id"] == data["user_id"]
    assert data["correct_count"] == 1
    assert data["incorrect_count"] == 0
    assert data["points"] == 8  # bits added directly as points


@pytest.mark.asyncio
async def test_record_incorrect_answer(auth_client: AsyncClient, session: AsyncSession):
    """POST /api/game/incorrect should increment incorrect count and deduct bits*4 points."""
    response = await auth_client.post("/api/game/incorrect", json={"bits": 4})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Incorrect answer recorded"
    assert data["correct_count"] == 0
    assert data["incorrect_count"] == 1
    assert data["points"] == -16  # -(4 bits * 4)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_leaderboard_returns_wrapped_response(auth_client: AsyncClient):
    """GET /api/game/leaderboard should return wrapped entries array."""
    await auth_client.post("/api/game/correct", json={"bits": 16})

    response = await auth_client.get("/api/game/leaderboard")

    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)
    assert len(data["entries"]) >= 1

    entry = data["entries"][0]
    assert "user_id" in entry
    assert "username" in entry
    assert "correct_count" in entry
    assert "incorrect_count" in entry
    assert "points" in entry
    assert entry["username"] == "GameTester"
    assert entry["correct_count"] == 1
    assert entry["points"] == 16  # 16 bits = 16 points


@pytest.mark.asyncio
async def test_leaderboard_sorted_by_points(session: AsyncSession):
    """Leaderboard should be sorted by points descending."""
    from src.services.auth_service import create_user

    user1 = await create_user(
        session=session,
        email="player1@example.com",
        username="Player1",
        password="Test1234!",
    )
    user2 = await create_user(
        session=session,
        email="player2@example.com",
        username="Player2",
        password="Test1234!",
    )

    stats1 = UserStats(user_id=user1.id, correct_count=1, points=50)
    stats2 = UserStats(user_id=user2.id, correct_count=2, points=200)
    session.add_all([stats1, stats2])
    await session.commit()

    from httpx import AsyncClient, ASGITransport
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/game/leaderboard")
        data = response.json()

    entries = data["entries"]
    assert len(entries) >= 2
    assert entries[0]["username"] == "Player2"
    assert entries[0]["points"] == 200
    assert entries[1]["username"] == "Player1"
    assert entries[1]["points"] == 50
