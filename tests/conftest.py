"""Pytest configuration and fixtures.

Test database resolution order (first match wins):

1. Real environment variables — CI sets these (GitHub Actions Postgres
   service on ``localhost:5432``, see ``.github/workflows/deploy.yml``).
2. A ``.env.test`` file in the repo root — developer-local overrides,
   gitignored (see ``.env.test.example``).
3. ``TEST_DB_*`` environment variables (documented in ``docs/test-setup.md``).
4. Defaults matching ``docker-compose.test.yaml``:
   ``test_user``/``test_password`` @ ``localhost:5434``, database ``test_db``.

Start the dedicated local test instance before running DB-backed tests::

    docker compose -f docker-compose.test.yaml up -d

NEVER point the tests at the development Postgres on port 5432 (database
``hextobin``): migration tests execute ``DROP SCHEMA public CASCADE``.
``_test_db_ready`` enforces that the target database name contains "test".

US-05: Settings now uses factory pattern (get_settings()) instead of
module-level instantiation.

``ENV_FILE`` is pinned to a path that does not exist for the whole session (see
"Env-file isolation" below), so the developer's gitignored ``.env`` never reaches
``Settings`` and local runs behave exactly like CI. Extra config for the suite
goes into ``.env.test`` (priority 2), never into ``ENV_FILE``.
"""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# --- Test environment configuration ------------------------------------------
# Must run BEFORE importing src.config / src.database.engine: engine.py
# instantiates Settings and creates its engine at module import time.

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ENV_FILE = REPO_ROOT / ".env.test"

# Target databases whose name lacks this marker are refused, because the
# migration tests destructively reset the public schema.
TEST_DB_NAME_MARKER = "test"

# --- Env-file isolation -------------------------------------------------------
# src/config.py evaluates ``env_file=os.environ.get("ENV_FILE", ".env")`` ONCE, at
# class-creation time (it lands in ``Settings.model_config``). Since 91e9ca1 that
# default is the developer's gitignored ``.env`` -- which really carries a
# JWT_SECRET -- so pydantic-settings found a usable secret on disk even in tests
# that clear ``os.environ``. The "no JWT_SECRET anywhere -> ValueError" tests in
# tests/test_settings.py therefore silently stopped asserting anything; CI stayed
# green only because it has no ``.env``. Pin ENV_FILE to a path that provably does
# not exist so no env file is read at all and local runs behave exactly like CI.
# (A missing file is silently skipped by pydantic-settings; an existing file would
# be caught by the regression guard in tests/test_settings.py.)
#
# Deliberately an assignment, not a setdefault: an ``ENV_FILE`` exported in a
# developer shell would re-introduce the exact leak this pin prevents. Extra config
# for the suite belongs in ``.env.test`` (priority 2 below), never in ENV_FILE.
TEST_ENV_FILE_PIN = REPO_ROOT / ".env.this-file-must-not-exist"
os.environ["ENV_FILE"] = str(TEST_ENV_FILE_PIN)

os.environ.setdefault("COOKIE_SECURE", "false")
# US-01: engine.py calls get_settings() at import time, so JWT_SECRET must be
# available before the import. Tests that need a specific secret clear the
# cache and patch os.environ in their own fixtures.
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-for-pytest-only-not-for-production"
)

# Priority 2: .env.test. override=False keeps real env vars (CI) winning.
if TEST_ENV_FILE.exists():
    load_dotenv(TEST_ENV_FILE, override=False)

# Priorities 3 + 4: TEST_DB_* env vars, then docker-compose.test.yaml defaults.
os.environ.setdefault("DB_NAME", os.getenv("TEST_DB_NAME", "test_db"))
os.environ.setdefault("DB_USER", os.getenv("TEST_DB_USER", "test_user"))
os.environ.setdefault("DB_PW", os.getenv("TEST_DB_PASSWORD", "test_password"))
os.environ.setdefault("DB_HOST", os.getenv("TEST_DB_HOST", "localhost"))
os.environ.setdefault("DB_PORT", os.getenv("TEST_DB_PORT", "5434"))

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.database.engine import Base  # noqa: E402
from src.models import (  # noqa: E402, F401 - ensure models are imported
    PasswordResetToken,
    RateLimitAttempt,
    User,
    UserAuditLog,
    UserIdentity,
    UserRefreshToken,
)

get_settings.cache_clear()


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


@pytest.fixture(scope="session")
def _test_db_ready(test_db_config):
    """Guard and clean the PostgreSQL test database for the whole session.

    Setup (US-22 AC 1/2/4):
    - Refuses any target database whose name lacks TEST_DB_NAME_MARKER,
      protecting the development database from DROP SCHEMA public CASCADE.
    - Probes connectivity once and fails fast with an actionable hint
      instead of an opaque OSError per test ("Connect call failed :5434").

    Teardown: resets the public schema so the test database is left clean
    for the next session.
    """
    settings = get_settings()
    masked_url = settings.async_database_url.split("@", 1)[-1]  # hide credentials

    if TEST_DB_NAME_MARKER not in settings.db_name.lower():
        pytest.fail(
            f"Refusing to run database tests against {settings.db_name!r} "
            f"({masked_url}): migration tests execute DROP SCHEMA public "
            f"CASCADE. Target database name must contain "
            f"{TEST_DB_NAME_MARKER!r} (check .env.test / TEST_DB_NAME)."
        )

    async def _probe() -> None:
        engine = create_async_engine(settings.async_database_url, echo=False)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_probe())
    except (OSError, SQLAlchemyError) as exc:
        pytest.fail(
            f"PostgreSQL test database unreachable at {masked_url}: {exc!r}\n"
            "Start the dedicated test instance:\n"
            "    docker compose -f docker-compose.test.yaml up -d\n"
            "or point tests elsewhere via a .env.test file "
            "(see .env.test.example)."
        )

    yield

    async def _reset_schema() -> None:
        engine = create_async_engine(settings.async_database_url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_reset_schema())
    except (OSError, SQLAlchemyError) as exc:
        logger.warning("Could not reset test schema after session: %r", exc)


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for anyio tests."""
    return "asyncio"


@pytest.fixture
async def async_engine(_test_db_ready):
    """Create async engine with PostgreSQL for testing.

    Each test gets a fresh engine with all tables created.
    Uses PostgreSQL (not SQLite) to match production environment and ensure
    PostgreSQL-specific features work correctly (CASCADE, constraints, etc.).

    Requires the test database from docker-compose.test.yaml on
    localhost:5434; _test_db_ready verifies reachability and resets the
    schema after the session.
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
async def migration_engine(_test_db_ready):
    """Fresh async engine for migration tests (shared via conftest, US-22).

    Unlike async_engine, NO tables are pre-created: migration tests must
    observe exactly what the migration itself creates.
    """
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migration_session(migration_engine):
    """Session for migration tests with per-test schema isolation.

    Drops and recreates the public schema before each test so every test
    starts from a pristine database regardless of execution order.
    """
    async_session = sessionmaker(
        migration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        # Drop all tables for clean slate
        await session.execute(text("DROP SCHEMA public CASCADE"))
        await session.execute(text("CREATE SCHEMA public"))
        await session.commit()
        yield session


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
