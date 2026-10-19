"""Tests for US-09: Database Schema Migration.

Tests verify that the migration script creates the new auth schema correctly,
drops the old users table, and is idempotent.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.migrations.v001_auth_schema import run_migration


@pytest.fixture
async def migration_engine():
    """Create fresh async engine for migration tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migration_session(migration_engine):
    """Create session for migration tests."""
    async_session = sessionmaker(
        migration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
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
    await run_migration(migration_session)

    # Assert: new table exists and is empty
    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "users" in tables

        result = await migration_session.execute(text("SELECT COUNT(*) FROM users"))
        count = result.scalar()
        assert count == 0  # New table is empty (clean break)


async def test_migration_is_idempotent(migration_engine, migration_session):
    """Test that migration can be run multiple times without errors."""
    await run_migration(migration_session)
    await run_migration(migration_session)  # Second run should not error

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "users" in tables
        assert "user_identities" in tables
        assert "user_refresh_tokens" in tables
        assert "password_reset_tokens" in tables
        assert "user_audit_log" in tables


async def test_migration_creates_indexes(migration_engine, migration_session):
    """Test that migration creates required indexes for performance."""
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
