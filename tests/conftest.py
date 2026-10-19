"""Pytest configuration and fixtures.

Sets test environment variables for Settings.
US-05: Settings now uses factory pattern (get_settings()) instead of module-level instantiation.
"""

import os

# Set test environment variables for PostgreSQL test database
# Use TEST_DB_* env vars if set, otherwise default to test_db
os.environ.setdefault("DB_NAME", os.getenv("TEST_DB_NAME", "test_db"))
os.environ.setdefault("DB_USER", os.getenv("TEST_DB_USER", "test_user"))
os.environ.setdefault("DB_PW", os.getenv("TEST_DB_PASSWORD", "test_password"))
os.environ.setdefault("DB_HOST", os.getenv("TEST_DB_HOST", "localhost"))
os.environ.setdefault("DB_PORT", os.getenv("TEST_DB_PORT", "5434"))

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base
from src.models import (  # noqa: F401 - ensure models are imported
    User,
    UserIdentity,
    UserRefreshToken,
    PasswordResetToken,
    UserAuditLog,
)
from src.config import get_settings


@pytest.fixture(scope="session")
def test_db_config():
    """Test database configuration."""
    settings = get_settings()
    return {
        "db_name": settings.db_name,
        "db_user": settings.db_user,
        "db_pw": settings.db_pw,
        "db_host": settings.db_host,
        "db_port": settings.db_port,
    }


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for anyio tests."""
    return "asyncio"


@pytest.fixture
async def async_engine():
    """Create async engine with PostgreSQL for testing.

    Each test gets a fresh engine with all tables created.
    Uses PostgreSQL (not SQLite) to match production environment and ensure
    PostgreSQL-specific features work correctly (CASCADE, constraints, etc.).

    Requires PostgreSQL test database running on localhost:5433.
    Start with: docker-compose -f docker-compose.test.yaml up -d
    """
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)

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
