"""Pytest configuration and fixtures.

Sets test environment variables for Settings.
US-05: Settings now uses factory pattern (get_settings()) instead of module-level instantiation.
"""

import os

# Set test environment variables
# These override the defaults in Settings class for test isolation
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PW", "test_password")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base
from src.models import User  # noqa: F401 - ensure model is imported


@pytest.fixture(scope="session")
def test_db_config():
    """Test database configuration."""
    return {
        "db_name": "test_db",
        "db_user": "test_user",
        "db_pw": "test_password",
        "db_host": "localhost",
        "db_port": 5432,
    }


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for anyio tests."""
    return "asyncio"


@pytest.fixture
async def async_engine():
    """Create async engine with in-memory SQLite for testing.

    Each test gets a fresh engine with all tables created.
    Uses SQLite instead of PostgreSQL because:
    - No external dependencies (no Docker/PostgreSQL needed)
    - Fast (in-memory, no I/O)
    - Isolated per test
    - Sufficient for testing business logic (not asyncpg-specific features)
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    """Create async session for testing.

    Each test gets a fresh session with isolated data.
    Uses the async_engine fixture to ensure tables exist.
    """
    async_session = sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def app_dependency_override(async_engine):
    """Override FastAPI's get_db dependency to use test DB.

    This allows endpoint tests to use the same in-memory DB
    as repository tests, ensuring consistency.
    """
    from src.database.engine import get_db
    from src.main import app

    async_session = sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app

    # Cleanup
    app.dependency_overrides.clear()
