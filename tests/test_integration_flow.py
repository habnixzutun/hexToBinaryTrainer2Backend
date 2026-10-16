"""Integration test: Full user flow across API → Repository → Model.

Verifies cross-community connections by testing a complete user journey.
Tests the flow: POST /api/user → POST /api/correct → GET /api/leaderboard
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_full_user_flow_integration(app_dependency_override):
    """Integration test: Complete user journey across all layers.

    This test verifies cross-community connections:
    - API endpoints (main.py)
    - Repository (user_repository.py)
    - Models (user.py)
    - Pydantic schemas (pydantic_types.py)

    Flow:
    1. Create user via POST /api/user
    2. Answer correctly via POST /api/correct
    3. Verify leaderboard via GET /api/leaderboard
    """
    async with AsyncClient(
        transport=ASGITransport(app=app_dependency_override),
        base_url="http://test",
    ) as client:
        # Step 1: Create user
        user_response = await client.post("/api/user", json={"name": "integration_user"})
        assert user_response.status_code == 200
        user_data = user_response.json()
        assert user_data["name"] == "integration_user"
        assert user_data["correct"] == 0
        assert user_data["points"] == 0

        # Step 2: Answer correctly
        correct_response = await client.post(
            "/api/correct",
            json={"name": "integration_user", "bits": 10}
        )
        assert correct_response.status_code == 200
        correct_data = correct_response.json()
        assert correct_data["message"] == "Success"
        assert correct_data["user"]["name"] == "integration_user"
        assert correct_data["user"]["correct"] == 1
        assert correct_data["user"]["points"] == 10

        # Step 3: Verify leaderboard
        leaderboard_response = await client.get("/api/leaderboard")
        assert leaderboard_response.status_code == 200
        leaderboard_data = leaderboard_response.json()
        assert len(leaderboard_data["entries"]) >= 1

        # Find our user in leaderboard
        integration_user_entry = next(
            (entry for entry in leaderboard_data["entries"]
             if entry["name"] == "integration_user"),
            None
        )
        assert integration_user_entry is not None
        assert integration_user_entry["correct"] == 1
        assert integration_user_entry["points"] == 10


@pytest.mark.asyncio
async def test_multiple_users_competition_integration(app_dependency_override):
    """Integration test: Multiple users competing on leaderboard.

    Tests sorting and ranking logic across multiple users.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app_dependency_override),
        base_url="http://test",
    ) as client:
        # Create 3 users with different performance
        # User 1: 2 correct, 20 points
        await client.post("/api/correct", json={"name": "user_alice", "bits": 10})
        await client.post("/api/correct", json={"name": "user_alice", "bits": 10})

        # User 2: 1 correct, 30 points (higher points, fewer correct)
        await client.post("/api/correct", json={"name": "user_bob", "bits": 30})

        # User 3: 1 correct, 1 incorrect, 6 points (10 - 4)
        await client.post("/api/correct", json={"name": "user_charlie", "bits": 10})
        await client.post("/api/incorrect", json={"name": "user_charlie", "bits": 1})

        # Verify leaderboard sorting
        leaderboard_response = await client.get("/api/leaderboard")
        assert leaderboard_response.status_code == 200
        leaderboard_data = leaderboard_response.json()

        # Should be sorted by points DESC
        entries = leaderboard_data["entries"]
        assert len(entries) >= 3

        # Find positions
        alice_pos = next(i for i, e in enumerate(entries) if e["name"] == "user_alice")
        bob_pos = next(i for i, e in enumerate(entries) if e["name"] == "user_bob")
        charlie_pos = next(i for i, e in enumerate(entries) if e["name"] == "user_charlie")

        # Bob should be first (30 points)
        assert bob_pos < alice_pos
        assert bob_pos < charlie_pos

        # Alice should be second (20 points)
        assert alice_pos < charlie_pos

        # Verify exact points
        assert entries[bob_pos]["points"] == 30
        assert entries[alice_pos]["points"] == 20
        assert entries[charlie_pos]["points"] == 6
