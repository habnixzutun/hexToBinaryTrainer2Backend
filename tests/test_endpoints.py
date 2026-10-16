"""Tests for API endpoints (main.py).

Verifies /api/leaderboard, /api/user, /api/correct, /api/incorrect endpoints.
Covers God Nodes: correct_answer, incorrect_answer, get_leaderboard.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


# ============================================================================
# GET /api/leaderboard Tests
# ============================================================================


class TestGetLeaderboard:
    """Test GET /api/leaderboard endpoint."""

    @pytest.mark.asyncio
    async def test_leaderboard_empty(self, app_dependency_override):
        """Verify leaderboard returns empty list when no users."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/leaderboard")

            assert response.status_code == 200
            data = response.json()
            assert "entries" in data
            assert len(data["entries"]) == 0

    @pytest.mark.asyncio
    async def test_leaderboard_returns_users(self, app_dependency_override):
        """Verify leaderboard returns users sorted by points."""
        # Create some users via /api/correct
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Create user1 with 10 points
            await client.post("/api/correct", json={"name": "user1", "bits": 10})

            # Create user2 with 5 points
            await client.post("/api/correct", json={"name": "user2", "bits": 5})

            # Get leaderboard
            response = await client.get("/api/leaderboard")

            assert response.status_code == 200
            data = response.json()
            assert len(data["entries"]) == 2
            # Should be sorted by points DESC
            assert data["entries"][0]["name"] == "user1"
            assert data["entries"][0]["points"] == 10
            assert data["entries"][1]["name"] == "user2"
            assert data["entries"][1]["points"] == 5


# ============================================================================
# POST /api/user Tests
# ============================================================================


class TestGetUserEndpoint:
    """Test POST /api/user endpoint."""

    @pytest.mark.asyncio
    async def test_get_user_creates_new_user(self, app_dependency_override):
        """Verify /api/user creates user if not exists."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/user", json={"name": "new_user"})

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "new_user"
            assert data["correct"] == 0
            assert data["incorrect"] == 0
            assert data["points"] == 0

    @pytest.mark.asyncio
    async def test_get_user_returns_existing_user(self, app_dependency_override):
        """Verify /api/user returns existing user with data."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Create user with some data
            await client.post("/api/correct", json={"name": "test_user", "bits": 10})

            # Get user
            response = await client.post("/api/user", json={"name": "test_user"})

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "test_user"
            assert data["correct"] == 1
            assert data["points"] == 10


# ============================================================================
# POST /api/correct Tests
# ============================================================================


class TestCorrectAnswer:
    """Test POST /api/correct endpoint."""

    @pytest.mark.asyncio
    async def test_correct_answer_adds_bits(self, app_dependency_override):
        """Verify /api/correct adds bits to user."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/correct", json={"name": "test_user", "bits": 5})

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Success"
            assert data["user"]["name"] == "test_user"
            assert data["user"]["correct"] == 1
            assert data["user"]["incorrect"] == 0
            assert data["user"]["points"] == 5

    @pytest.mark.asyncio
    async def test_correct_answer_accumulates(self, app_dependency_override):
        """Verify /api/correct accumulates points."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # First correct answer
            await client.post("/api/correct", json={"name": "test_user", "bits": 5})

            # Second correct answer
            response = await client.post("/api/correct", json={"name": "test_user", "bits": 10})

            assert response.status_code == 200
            data = response.json()
            assert data["user"]["correct"] == 2
            assert data["user"]["points"] == 15  # 5 + 10

    @pytest.mark.asyncio
    async def test_correct_answer_creates_user(self, app_dependency_override):
        """Verify /api/correct creates user if not exists."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/correct", json={"name": "new_user", "bits": 10})

            assert response.status_code == 200
            data = response.json()
            assert data["user"]["name"] == "new_user"
            assert data["user"]["correct"] == 1


# ============================================================================
# POST /api/incorrect Tests
# ============================================================================


class TestIncorrectAnswer:
    """Test POST /api/incorrect endpoint."""

    @pytest.mark.asyncio
    async def test_incorrect_answer_subtracts_4x_bits(self, app_dependency_override):
        """Verify /api/incorrect subtracts 4*bits from user."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Create user with some points
            await client.post("/api/correct", json={"name": "test_user", "bits": 100})

            # Answer incorrectly
            response = await client.post("/api/incorrect", json={"name": "test_user", "bits": 5})

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Success"
            assert data["user"]["name"] == "test_user"
            assert data["user"]["correct"] == 1
            assert data["user"]["incorrect"] == 1
            assert data["user"]["points"] == 100 - (5 * 4)  # 100 - 20 = 80

    @pytest.mark.asyncio
    async def test_incorrect_answer_accumulates(self, app_dependency_override):
        """Verify /api/incorrect accumulates incorrect count."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Create user
            await client.post("/api/correct", json={"name": "test_user", "bits": 50})

            # Two incorrect answers
            await client.post("/api/incorrect", json={"name": "test_user", "bits": 5})
            response = await client.post("/api/incorrect", json={"name": "test_user", "bits": 10})

            assert response.status_code == 200
            data = response.json()
            assert data["user"]["correct"] == 1
            assert data["user"]["incorrect"] == 2
            # 50 - (5*4) - (10*4) = 50 - 20 - 40 = -10
            assert data["user"]["points"] == -10

    @pytest.mark.asyncio
    async def test_incorrect_answer_creates_user(self, app_dependency_override):
        """Verify /api/incorrect creates user if not exists."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/incorrect", json={"name": "new_user", "bits": 5})

            assert response.status_code == 200
            data = response.json()
            assert data["user"]["name"] == "new_user"
            assert data["user"]["correct"] == 0
            assert data["user"]["incorrect"] == 1
            assert data["user"]["points"] == -20  # 0 - (5*4)
