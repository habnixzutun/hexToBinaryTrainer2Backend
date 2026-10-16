"""Tests for user repository (user_repository.py).

Verifies get_user, get_users, and update_user_data functions.
Covers God Nodes: get_user, get_users, update_user_data.
"""

import pytest
from sqlalchemy import select

from src.models.user import User
from src.repositories.user_repository import get_user, get_users, update_user_data


# ============================================================================
# get_user Tests
# ============================================================================


class TestGetUser:
    """Test get_user function."""

    @pytest.mark.asyncio
    async def test_get_user_creates_new_user(self, session):
        """Verify get_user creates user if not exists."""
        user = await get_user(session, "new_user")

        assert user.name == "new_user"
        assert user.correct == 0
        assert user.incorrect == 0
        assert user.points == 0

    @pytest.mark.asyncio
    async def test_get_user_returns_existing_user(self, session):
        """Verify get_user returns existing user without creating duplicate."""
        # Create user first
        user1 = await get_user(session, "test_user")
        user1.correct = 10
        user1.points = 50
        await session.commit()

        # Get same user again
        user2 = await get_user(session, "test_user")

        assert user2.name == "test_user"
        assert user2.correct == 10
        assert user2.points == 50

    @pytest.mark.asyncio
    async def test_get_user_auto_create_on_first_access(self, session):
        """Verify get_user auto-creates user on first access."""
        # User should not exist yet
        result = await session.execute(select(User).where(User.name == "auto_created"))
        assert result.scalar_one_or_none() is None

        # Get user (should create it)
        user = await get_user(session, "auto_created")
        assert user.name == "auto_created"

        # Verify user exists in DB
        result = await session.execute(select(User).where(User.name == "auto_created"))
        db_user = result.scalar_one()
        assert db_user.name == "auto_created"


# ============================================================================
# get_users Tests (Leaderboard)
# ============================================================================


class TestGetUsers:
    """Test get_users function (leaderboard)."""

    @pytest.mark.asyncio
    async def test_get_users_returns_empty_list(self, session):
        """Verify get_users returns empty list when no users exist."""
        users = await get_users(session)
        assert len(users) == 0

    @pytest.mark.asyncio
    async def test_get_users_returns_top_10(self, session):
        """Verify get_users returns max 10 users."""
        # Create 15 users
        for i in range(15):
            user = User(name=f"user_{i}", correct=i, incorrect=0, points=i * 10)
            session.add(user)
        await session.commit()

        users = await get_users(session)
        assert len(users) == 10

    @pytest.mark.asyncio
    async def test_get_users_sorts_by_points_desc(self, session):
        """Verify get_users sorts by points descending."""
        # Create users with different points
        user1 = User(name="low_points", correct=1, incorrect=0, points=10)
        user2 = User(name="high_points", correct=2, incorrect=0, points=100)
        user3 = User(name="mid_points", correct=3, incorrect=0, points=50)
        session.add_all([user1, user2, user3])
        await session.commit()

        users = await get_users(session)

        # Should be sorted by points DESC
        assert users[0].name == "high_points"
        assert users[1].name == "mid_points"
        assert users[2].name == "low_points"

    @pytest.mark.asyncio
    async def test_get_users_tiebreaker_by_incorrect_asc(self, session):
        """Verify get_users uses incorrect ASC as tiebreaker."""
        # Create users with same points but different incorrect counts
        user1 = User(name="many_incorrect", correct=1, incorrect=10, points=50)
        user2 = User(name="few_incorrect", correct=1, incorrect=2, points=50)
        user3 = User(name="zero_incorrect", correct=1, incorrect=0, points=50)
        session.add_all([user1, user2, user3])
        await session.commit()

        users = await get_users(session)

        # Should be sorted by incorrect ASC (lower is better)
        assert users[0].name == "zero_incorrect"
        assert users[1].name == "few_incorrect"
        assert users[2].name == "many_incorrect"

    @pytest.mark.asyncio
    async def test_get_users_limit_10(self, session):
        """Verify get_users limits to 10 users."""
        # Create exactly 10 users
        for i in range(10):
            user = User(name=f"user_{i}", correct=i, incorrect=0, points=i * 10)
            session.add(user)
        await session.commit()

        users = await get_users(session)
        assert len(users) == 10


# ============================================================================
# update_user_data Tests
# ============================================================================


class TestUpdateUserData:
    """Test update_user_data function."""

    @pytest.mark.asyncio
    async def test_update_user_data_correct_answer(self, session):
        """Verify update_user_data adds bits on correct answer."""
        # Create user
        user = User(name="test_user", correct=0, incorrect=0, points=0)
        session.add(user)
        await session.commit()

        # Update with correct answer
        updated_user = await update_user_data(session, "test_user", 5, is_correct=True)

        assert updated_user.correct == 1
        assert updated_user.incorrect == 0
        assert updated_user.points == 5

    @pytest.mark.asyncio
    async def test_update_user_data_incorrect_answer(self, session):
        """Verify update_user_data subtracts 4*bits on incorrect answer."""
        # Create user
        user = User(name="test_user", correct=0, incorrect=0, points=100)
        session.add(user)
        await session.commit()

        # Update with incorrect answer
        updated_user = await update_user_data(session, "test_user", 5, is_correct=False)

        assert updated_user.correct == 0
        assert updated_user.incorrect == 1
        assert updated_user.points == 100 - (5 * 4)  # 100 - 20 = 80

    @pytest.mark.asyncio
    async def test_update_user_data_creates_user_if_not_exists(self, session):
        """Verify update_user_data creates user if not exists."""
        # User should not exist yet
        result = await session.execute(select(User).where(User.name == "new_user"))
        assert result.scalar_one_or_none() is None

        # Update with answer
        updated_user = await update_user_data(session, "new_user", 5, is_correct=True)

        # User should be created
        assert updated_user.name == "new_user"
        assert updated_user.correct == 1
        assert updated_user.points == 5

    @pytest.mark.asyncio
    async def test_update_user_data_multiple_correct_answers(self, session):
        """Verify update_user_data accumulates correct answers."""
        # Create user
        user = User(name="test_user", correct=0, incorrect=0, points=0)
        session.add(user)
        await session.commit()

        # Update with 3 correct answers
        for i in range(3):
            await update_user_data(session, "test_user", 5, is_correct=True)

        # Verify final state
        result = await session.execute(select(User).where(User.name == "test_user"))
        final_user = result.scalar_one()
        assert final_user.correct == 3
        assert final_user.incorrect == 0
        assert final_user.points == 15  # 3 * 5

    @pytest.mark.asyncio
    async def test_update_user_data_multiple_incorrect_answers(self, session):
        """Verify update_user_data accumulates incorrect answers."""
        # Create user with some points
        user = User(name="test_user", correct=0, incorrect=0, points=100)
        session.add(user)
        await session.commit()

        # Update with 2 incorrect answers
        for i in range(2):
            await update_user_data(session, "test_user", 5, is_correct=False)

        # Verify final state
        result = await session.execute(select(User).where(User.name == "test_user"))
        final_user = result.scalar_one()
        assert final_user.correct == 0
        assert final_user.incorrect == 2
        assert final_user.points == 100 - (2 * 5 * 4)  # 100 - 40 = 60

    @pytest.mark.asyncio
    async def test_update_user_data_mixed_answers(self, session):
        """Verify update_user_data handles mix of correct and incorrect."""
        # Create user
        user = User(name="test_user", correct=0, incorrect=0, points=0)
        session.add(user)
        await session.commit()

        # Update with mixed answers
        await update_user_data(session, "test_user", 5, is_correct=True)  # +5

        await update_user_data(session, "test_user", 3, is_correct=False)  # -12

        await update_user_data(session, "test_user", 10, is_correct=True)  # +10

        # Verify final state
        result = await session.execute(select(User).where(User.name == "test_user"))
        final_user = result.scalar_one()
        assert final_user.correct == 2
        assert final_user.incorrect == 1
        assert final_user.points == 5 - 12 + 10  # 3
