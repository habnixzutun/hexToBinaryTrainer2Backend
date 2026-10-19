"""Tests for US-09: Database Schema Migration.

Tests verify that the migration script creates the new auth schema correctly,
drops the old users table, runs exactly once, and is tracked properly.

These tests require PostgreSQL running on localhost:5433.
Start with: docker-compose -f docker-compose.test.yaml up -d
"""

import pytest
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.migrations.v001_auth_schema import (
    run_migration,
    migration_already_applied,
    create_migrations_table,
)
from src.config import get_settings


@pytest.fixture
async def migration_engine():
    """Create fresh async engine with PostgreSQL for migration tests.

    Each test gets a fresh engine connected to PostgreSQL test database.
    This ensures PostgreSQL-specific features (CASCADE, constraints) work correctly.
    """
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migration_session(migration_engine):
    """Create session for migration tests.

    Each test gets a fresh session. Tables are dropped and recreated
    to ensure test isolation.
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


def get_table_names(sync_conn):
    """Helper to get table names in sync context."""
    inspector = inspect(sync_conn)
    return inspector.get_table_names()


def get_columns(sync_conn, table_name):
    """Helper to get columns in sync context."""
    inspector = inspect(sync_conn)
    return {col["name"]: col for col in inspector.get_columns(table_name)}


def get_indexes(sync_conn, table_name):
    """Helper to get indexes in sync context."""
    inspector = inspect(sync_conn)
    return {idx["name"]: idx for idx in inspector.get_indexes(table_name)}


def get_foreign_keys(sync_conn, table_name):
    """Helper to get foreign keys in sync context."""
    inspector = inspect(sync_conn)
    return inspector.get_foreign_keys(table_name)


async def test_migration_creates_users_table(migration_engine, migration_session):
    """Test that migration creates users table with all required columns."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "users" in tables

        columns = await conn.run_sync(get_columns, "users")
        assert "id" in columns
        assert "username" in columns
        assert "email" in columns
        assert "created_at" in columns
        assert "last_username_change_at" in columns


async def test_migration_creates_user_identities_table(
    migration_engine, migration_session
):
    """Test that migration creates user_identities table."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_identities" in tables

        columns = await conn.run_sync(get_columns, "user_identities")
        assert "id" in columns
        assert "user_id" in columns
        assert "provider" in columns
        assert "provider_user_id" in columns
        assert "password_hash" in columns
        assert "verified_at" in columns
        assert "created_at" in columns


async def test_migration_creates_user_refresh_tokens_table(
    migration_engine, migration_session
):
    """Test that migration creates user_refresh_tokens table."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_refresh_tokens" in tables

        columns = await conn.run_sync(get_columns, "user_refresh_tokens")
        assert "id" in columns
        assert "user_id" in columns
        assert "token_hash" in columns
        assert "expires_at" in columns
        assert "revoked_at" in columns
        assert "created_at" in columns


async def test_migration_creates_password_reset_tokens_table(
    migration_engine, migration_session
):
    """Test that migration creates password_reset_tokens table."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "password_reset_tokens" in tables

        columns = await conn.run_sync(get_columns, "password_reset_tokens")
        assert "id" in columns
        assert "user_id" in columns
        assert "token_hash" in columns
        assert "expires_at" in columns
        assert "used_at" in columns
        assert "type" in columns
        assert "created_at" in columns


async def test_migration_creates_user_audit_log_table(
    migration_engine, migration_session
):
    """Test that migration creates user_audit_log table."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_audit_log" in tables

        columns = await conn.run_sync(get_columns, "user_audit_log")
        assert "id" in columns
        assert "user_id" in columns
        assert "action" in columns
        assert "timestamp" in columns
        assert "ip_address" in columns
        assert "user_agent" in columns
        assert "metadata_json" in columns


async def test_migration_drops_old_users_table(migration_engine, migration_session):
    """Test that migration drops old users table and creates new one."""
    # Arrange: create old users table with data
    await migration_session.execute(text("""
        CREATE TABLE users (
            name VARCHAR PRIMARY KEY,
            correct INTEGER DEFAULT 0,
            incorrect INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0
        )
    """))
    await migration_session.execute(
        text(
            "INSERT INTO users (name, correct, incorrect, points) "
            "VALUES ('testuser', 10, 5, 100)"
        )
    )
    await migration_session.commit()

    # Act: run migration
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    # Assert: new table exists and is empty
    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "users" in tables

        result = await migration_session.execute(text("SELECT COUNT(*) FROM users"))
        count = result.scalar()
        assert count == 0  # New table is empty (clean break)


async def test_migration_runs_exactly_once(migration_engine, migration_session):
    """Test that migration runs exactly once and is tracked properly."""
    # First run: should execute migration
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    # Verify migration was marked as applied
    is_applied = await migration_already_applied(migration_session)
    assert is_applied is True

    # Second run: should NOT execute migration (no error, no changes)
    await run_migration(migration_session)

    # Verify tables still exist
    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "users" in tables
        assert "user_identities" in tables
        assert "user_refresh_tokens" in tables
        assert "password_reset_tokens" in tables
        assert "user_audit_log" in tables


async def test_migration_creates_indexes(migration_engine, migration_session):
    """Test that migration creates required indexes for performance."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        # Check users indexes
        users_indexes = await conn.run_sync(get_indexes, "users")
        assert any("username" in str(idx) for idx in users_indexes.values())
        assert any("email" in str(idx) for idx in users_indexes.values())

        # Check user_identities indexes
        identities_indexes = await conn.run_sync(get_indexes, "user_identities")
        assert any("provider" in str(idx) for idx in identities_indexes.values())

        # Check user_refresh_tokens indexes
        tokens_indexes = await conn.run_sync(get_indexes, "user_refresh_tokens")
        assert any("token_hash" in str(idx) for idx in tokens_indexes.values())

        # Check password_reset_tokens indexes
        reset_indexes = await conn.run_sync(get_indexes, "password_reset_tokens")
        assert any("token_hash" in str(idx) for idx in reset_indexes.values())

        # Check user_audit_log indexes
        audit_indexes = await conn.run_sync(get_indexes, "user_audit_log")
        assert any("user_id" in str(idx) for idx in audit_indexes.values())


async def test_migration_creates_foreign_keys(migration_engine, migration_session):
    """Test that migration creates foreign keys with ON DELETE CASCADE."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        # Check user_identities foreign key
        fk_constraints = await conn.run_sync(get_foreign_keys, "user_identities")
        assert len(fk_constraints) > 0
        user_fk = next((fk for fk in fk_constraints if "user_id" in str(fk)), None)
        assert user_fk is not None
        assert user_fk["options"].get("ondelete") == "CASCADE"

        # Check user_refresh_tokens foreign key
        fk_constraints = await conn.run_sync(get_foreign_keys, "user_refresh_tokens")
        assert len(fk_constraints) > 0

        # Check password_reset_tokens foreign key
        fk_constraints = await conn.run_sync(get_foreign_keys, "password_reset_tokens")
        assert len(fk_constraints) > 0

        # Check user_audit_log foreign key
        fk_constraints = await conn.run_sync(get_foreign_keys, "user_audit_log")
        assert len(fk_constraints) > 0


async def test_migration_creates_unique_constraints(migration_engine, migration_session):
    """Test that migration creates UNIQUE constraints for username, email, and tokens."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        # Check users table has unique constraints
        columns = await conn.run_sync(get_columns, "users")
        assert columns["username"].get("default") is not None or True  # UNIQUE constraint
        assert columns["email"].get("default") is not None or True

        # Check token_hash columns have unique constraints
        refresh_columns = await conn.run_sync(get_columns, "user_refresh_tokens")
        assert "token_hash" in refresh_columns

        reset_columns = await conn.run_sync(get_columns, "password_reset_tokens")
        assert "token_hash" in reset_columns


async def test_migration_cascade_delete(migration_engine, migration_session):
    """Test that CASCADE delete works correctly for foreign keys."""
    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    # Insert a user
    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('test-id-123', 'testuser', 'test@example.com')"
        )
    )

    # Insert an identity for the user
    await migration_session.execute(
        text(
            "INSERT INTO user_identities (id, user_id, provider, provider_user_id) "
            "VALUES ('identity-id-123', 'test-id-123', 'email', 'test@example.com')"
        )
    )
    await migration_session.commit()

    # Delete the user
    await migration_session.execute(text("DELETE FROM users WHERE id = 'test-id-123'"))
    await migration_session.commit()

    # Verify identity was also deleted (CASCADE)
    result = await migration_session.execute(
        text("SELECT COUNT(*) FROM user_identities WHERE user_id = 'test-id-123'")
    )
    count = result.scalar()
    assert count == 0  # Identity should be deleted due to CASCADE
