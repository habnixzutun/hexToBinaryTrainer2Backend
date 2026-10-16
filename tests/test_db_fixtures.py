"""Tests for database fixtures.

Verifies that in-memory DB fixtures work correctly for testing.
"""

import pytest
from sqlalchemy import select

from src.models.user import User


@pytest.mark.asyncio
async def test_db_fixture_creates_tables(async_engine):
    """Verify DB fixture creates tables."""
    async with async_engine.begin() as conn:
        result = await conn.execute(select(User.__table__))
        # Table exists, no exception raised
        assert result is not None


@pytest.mark.asyncio
async def test_session_fixture_provides_isolated_db(session):
    """Verify session fixture provides isolated DB per test."""
    # Create a user in this session
    user = User(name="test_user_1", correct=10, points=5, incorrect=2)
    session.add(user)
    await session.commit()

    # Query the user
    result = await session.execute(select(User).where(User.name == "test_user_1"))
    fetched_user = result.scalar_one()
    assert fetched_user.correct == 10


@pytest.mark.asyncio
async def test_session_isolation_between_tests(session):
    """Verify each test gets a fresh, isolated DB."""
    # This test should NOT see the user from test_session_fixture_provides_isolated_db
    result = await session.execute(select(User))
    users = result.scalars().all()
    assert len(users) == 0, "Session should be isolated - no users from other tests"


@pytest.mark.asyncio
async def test_async_engine_fixture(async_engine):
    """Verify async_engine fixture works."""
    assert async_engine is not None

    # Verify we can create tables
    from src.database.engine import Base
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Verify tables exist
    async with async_engine.connect() as conn:
        result = await conn.execute(select(User.__table__))
        assert result is not None
