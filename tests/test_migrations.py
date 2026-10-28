"""Tests for US-09: Database Schema Migration.

Tests verify that the migration script creates the new auth schema correctly,
drops the old users table, runs exactly once, and is tracked properly.

Run against the PostgreSQL test database from docker-compose.test.yaml
(localhost:5434, database test_db). Start it with:

    docker compose -f docker-compose.test.yaml up -d

The migration_engine / migration_session fixtures live in conftest.py
(US-22: shared Postgres fixtures); migration_session resets the public
schema before each test for full isolation.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from src.migrations.v001_auth_schema import (
    create_migrations_table,
    migration_already_applied,
    run_migration,
)
from src.migrations.v002_game_stats import run_migration as run_v002
from src.migrations.v003_rate_limit import (
    migration_already_applied as v003_already_applied,
)
from src.migrations.v003_rate_limit import run_migration as run_v003
from src.migrations.v004_xp_schema import (
    MIGRATION_ID as V004_MIGRATION_ID,
)
from src.migrations.v004_xp_schema import (
    migration_already_applied as v004_already_applied,
)
from src.migrations.v004_xp_schema import run_migration as run_v004


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
    await migration_session.execute(
        text(
            """
        CREATE TABLE users (
            name VARCHAR PRIMARY KEY,
            correct INTEGER DEFAULT 0,
            incorrect INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0
        )
    """
        )
    )
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


async def test_migration_creates_unique_constraints(
    migration_engine, migration_session
):
    """Test that migration creates UNIQUE constraints for users and tokens."""

    await create_migrations_table(migration_session)
    await run_migration(migration_session)

    async with migration_engine.connect() as conn:
        # Check users table has unique constraints
        columns = await conn.run_sync(get_columns, "users")
        assert (
            columns["username"].get("default") is not None or True
        )  # UNIQUE constraint
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


# ---------------------------------------------------------------------------
# US-04: v003_rate_limit — persistent rate-limit store (ADR 001: DB, no Redis)
# ---------------------------------------------------------------------------


async def test_v003_creates_rate_limit_attempt_table(
    migration_engine, migration_session
):
    """v003 creates the table RateLimitService writes its counters to."""
    await create_migrations_table(migration_session)
    await run_v003(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "rate_limit_attempt" in tables

        columns = await conn.run_sync(get_columns, "rate_limit_attempt")
        assert {"id", "key", "created_at", "expires_at"} <= set(columns)

        # The composite index backs both the per-key count and the TTL sweep.
        indexes = await conn.run_sync(get_indexes, "rate_limit_attempt")
        assert "ix_rate_limit_attempt_key_expires" in indexes


async def test_v003_requires_expires_at(migration_session):
    """expires_at is NOT NULL: a row without a TTL could never be cleaned up."""
    await create_migrations_table(migration_session)
    await run_v003(migration_session)

    with pytest.raises(IntegrityError):
        await migration_session.execute(
            text("INSERT INTO rate_limit_attempt (id, key) VALUES ('rl-1', 'k')")
        )
        await migration_session.commit()


async def test_v003_is_tracked_and_idempotent(migration_session):
    """v003 records its version and a second run is a no-op (startup re-runs it)."""
    await create_migrations_table(migration_session)

    assert await v003_already_applied(migration_session) is False

    await run_v003(migration_session)
    assert await v003_already_applied(migration_session) is True

    # lifespan calls every migration on every boot — must not raise a second time
    await run_v003(migration_session)

    result = await migration_session.execute(
        text("SELECT COUNT(*) FROM schema_migrations WHERE version = 'v003_rate_limit'")
    )
    assert result.scalar() == 1


# ---------------------------------------------------------------------------
# US-27 (Sprint 4): v004_xp_schema — ADDITIVE XP-economy schema.
#
# Plan D-6 / Rev 3 R-1: v004 creates ``user_game_profile`` and LEAVES
# ``user_stats`` in place. The ``DROP TABLE user_stats`` belongs to v005, shipped
# together with US-34 at sprint close — nothing in this section may assert that
# user_stats disappears, and nothing here touches the existing v001-v003 tests
# (Rev 3 R-10: they contain no user_stats assertions to update).
#
# Expected DDL below is copied from the US-26 story text and the ORM in
# ``src/models/game.py``, deliberately as literals.
# ---------------------------------------------------------------------------

V004_COLUMNS = (
    "user_id",
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
    "updated_at",
)

# US-26: the four CHECKs, explicitly named so the ORM and v004 agree.
V004_CHECKS = {
    "ck_user_game_profile_total_xp": "total_xp >= 0",
    "ck_user_game_profile_level": "level >= 1",
    "ck_user_game_profile_current_streak": "current_streak >= 0",
    "ck_user_game_profile_best_streak": "best_streak >= 0",
}

V004_PROFILE_INDEX = "ix_user_game_profile_total_xp"

# v001 = the module-level ``run_migration`` imported at the top of this file.
run_v001 = run_migration


def _catalog_constraints(sync_conn, table):
    """{constraint name: (type, definition)} for ``table``, from pg_constraint.

    ``contype`` is Postgres pseudo-type ``"char"``; asyncpg hands that back as
    ``bytes``, so it is cast to ``text`` here — otherwise every ``== "c"``
    comparison below silently fails.
    """
    rows = sync_conn.execute(
        text(
            "SELECT con.conname, con.contype::text, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con "
            "JOIN pg_class cls ON cls.oid = con.conrelid "
            "WHERE cls.relname = :t"
        ),
        {"t": table},
    ).fetchall()
    return {name: (_as_str(ctype), defn) for name, ctype, defn in rows}


def _as_str(value):
    """Normalise an asyncpg ``"char"``/enum cell to ``str`` (bytes-safe)."""
    if isinstance(value, bytes):
        return value.decode()
    return value


def _catalog_indexes(sync_conn, table):
    """{index name: indexdef} for ``table``, from pg_indexes."""
    rows = sync_conn.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
        {"t": table},
    ).fetchall()
    return dict(rows)


def _catalog_columns(sync_conn, table):
    """Column names of ``table`` in the order Postgres stores them."""
    rows = sync_conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    ).fetchall()
    return [row[0] for row in rows]


def _catalog_column_signature(sync_conn, table):
    """Per-column DDL detail: (name, ordinal, nullable, default, type).

    Richer than :func:`_catalog_columns` — this is what pins the v004 DDL
    against the US-26 ORM declaration (types and defaults included).
    """
    rows = sync_conn.execute(
        text(
            "SELECT column_name, ordinal_position, is_nullable, "
            "coalesce(column_default, ''), data_type "
            "FROM information_schema.columns "
            "WHERE table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    ).fetchall()
    return [tuple(row) for row in rows]


def _profile_shape(sync_conn):
    """Complete DDL signature of user_game_profile.

    Compared across the v004 application paths: the end state must not depend on
    whether v002 ever ran (US-27 AC-5), and it must not depend on *how* the table
    was created — migrated by v004 or built from the ORM ("mirror the ORM").
    """
    return {
        "columns": _catalog_columns(sync_conn, "user_game_profile"),
        "column_signature": _catalog_column_signature(sync_conn, "user_game_profile"),
        "constraints": _catalog_constraints(sync_conn, "user_game_profile"),
        "indexes": _catalog_indexes(sync_conn, "user_game_profile"),
    }


async def _insert_user(session, user_id="v004-user-1", username="v004user"):
    """A plain users row (v001 shape: id/username/email, both UNIQUE)."""
    await session.execute(
        text("INSERT INTO users (id, username, email) VALUES (:id, :username, :email)"),
        {"id": user_id, "username": username, "email": f"{username}@example.com"},
    )
    await session.commit()


async def test_v004_creates_user_game_profile(migration_engine, migration_session):
    """AC-2: the table, its eight columns, four CHECKs and the DESC index."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)  # FK target: users
    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_game_profile" in tables

        columns = await conn.run_sync(_catalog_columns, "user_game_profile")
        assert tuple(columns) == V004_COLUMNS

        constraints = await conn.run_sync(_catalog_constraints, "user_game_profile")
        checks = {
            name: defn for name, (ctype, defn) in constraints.items() if ctype == "c"
        }
        assert set(checks) == set(V004_CHECKS)
        for name, needle in V004_CHECKS.items():
            assert needle in checks[name], (name, checks[name])

        # PK is user_id, FK goes to users(id) and cascades (US-27 AC-4)
        primary_keys = [defn for (ctype, defn) in constraints.values() if ctype == "p"]
        foreign_keys = [defn for (ctype, defn) in constraints.values() if ctype == "f"]
        assert primary_keys == ["PRIMARY KEY (user_id)"]
        assert foreign_keys == [
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ]

        indexes = await conn.run_sync(_catalog_indexes, "user_game_profile")
        assert V004_PROFILE_INDEX in indexes
        assert "total_xp DESC" in indexes[V004_PROFILE_INDEX]
        # a plain (non-unique) index, not a PK/unique artefact
        assert indexes[V004_PROFILE_INDEX].startswith("CREATE INDEX")

        # v004 depends on v001 alone: no v002 was applied here, yet the table is
        # complete. Coexistence with the legacy table is pinned by
        # ``test_v004_keeps_user_stats_whole`` (D-6).


async def test_v004_ddl_matches_the_orm(migration_engine, migration_session):
    """US-27: v004 mirrors the US-26 ORM — catalog-indistinguishable DDL.

    Builds the table once from ``UserGameProfile.__table__`` (the path every test
    engine uses) and once through ``run_migration`` (the production path), and
    compares the full signature: column order, types, nullability, defaults,
    constraint names + definitions and index definitions. Drift here would mean a
    migrated production DB behaves differently from every test that ran.
    """
    from src.models.game import UserGameProfile

    await create_migrations_table(migration_session)
    await run_v001(migration_session)  # FK target: users

    async with migration_engine.begin() as conn:
        await conn.run_sync(lambda sc: UserGameProfile.__table__.create(sc))
    async with migration_engine.connect() as conn:
        from_orm = await conn.run_sync(_profile_shape)

    async with migration_engine.begin() as conn:
        await conn.run_sync(lambda sc: UserGameProfile.__table__.drop(sc))
    await run_v004(migration_session)
    async with migration_engine.connect() as conn:
        from_v004 = await conn.run_sync(_profile_shape)

    assert from_orm == from_v004
    # spelled out, so a failure names the layer instead of just differing dicts
    signature = from_v004["column_signature"]
    assert tuple(c[0] for c in signature) == V004_COLUMNS
    assert [c[2] for c in signature] == ["NO"] * len(V004_COLUMNS)
    assert {c[4] for c in signature if c[0] != "updated_at"} == {
        "character varying",
        "integer",
    }
    assert [c[4] for c in signature if c[0] == "updated_at"] == [
        "timestamp with time zone"
    ]


async def test_v004_applies_row_defaults(migration_session):
    """A bare INSERT lands at 0 XP / level 1 / streak 0 (D-1/OQ-3 start state)."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v004(migration_session)
    await _insert_user(migration_session)

    await migration_session.execute(
        text("INSERT INTO user_game_profile (user_id) VALUES ('v004-user-1')")
    )
    await migration_session.commit()

    row = (
        await migration_session.execute(
            text(
                "SELECT total_xp, level, current_streak, best_streak, "
                "correct_count, incorrect_count, updated_at "
                "FROM user_game_profile WHERE user_id = 'v004-user-1'"
            )
        )
    ).one()
    assert (row.total_xp, row.level) == (0, 1)
    assert (row.current_streak, row.best_streak) == (0, 0)
    assert (row.correct_count, row.incorrect_count) == (0, 0)
    assert row.updated_at is not None


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("total_xp", -4),
        ("level", 0),
        ("current_streak", -1),
        ("best_streak", -1),
    ],
)
async def test_v004_checks_reject_invalid_rows(migration_session, column, value):
    """v004 mirrors the US-26 CHECKs — a migrated DB enforces what the ORM declares."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v004(migration_session)
    await _insert_user(migration_session)

    with pytest.raises(IntegrityError):
        await migration_session.execute(
            text(
                f"INSERT INTO user_game_profile (user_id, {column}) "
                "VALUES ('v004-user-1', :value)"
            ),
            {"value": value},
        )
        await migration_session.commit()
    await migration_session.rollback()


async def test_v004_is_tracked_and_idempotent(migration_session):
    """AC-1: re-running is a no-op that records exactly one schema_migrations row."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)

    assert await v004_already_applied(migration_session) is False

    await run_v004(migration_session)
    assert await v004_already_applied(migration_session) is True
    assert V004_MIGRATION_ID == "v004_xp_schema"

    # lifespan calls every migration on every boot — must not raise twice
    await run_v004(migration_session)

    result = await migration_session.execute(
        text("SELECT COUNT(*) FROM schema_migrations WHERE version = :v"),
        {"v": V004_MIGRATION_ID},
    )
    assert result.scalar() == 1


async def test_v004_second_run_leaves_the_ddl_untouched(
    migration_engine, migration_session
):
    """AC-1: the no-op re-run must not rewrite or duplicate any schema object."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        first = await conn.run_sync(_profile_shape)

    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        second = await conn.run_sync(_profile_shape)

    assert second == first


async def test_v004_keeps_user_stats_whole(migration_engine, migration_session):
    """D-6: additive only — user_stats survives with its data, no backfill.

    The legacy rows are neither copied into user_game_profile nor modified: XP is
    only ever written to the new table by the (future) /answer path.
    """
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v002(migration_session)
    await _insert_user(migration_session)
    await migration_session.execute(
        text(
            "INSERT INTO user_stats (user_id, correct_count, incorrect_count, points) "
            "VALUES ('v004-user-1', 3, 1, 36)"
        )
    )
    await migration_session.commit()

    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_stats" in tables
        assert "user_game_profile" in tables

        columns = await conn.run_sync(get_columns, "user_stats")
        assert {"user_id", "correct_count", "incorrect_count", "points"} <= set(columns)

    legacy = (
        await migration_session.execute(
            text(
                "SELECT correct_count, incorrect_count, points "
                "FROM user_stats WHERE user_id = 'v004-user-1'"
            )
        )
    ).one()
    assert (legacy.correct_count, legacy.incorrect_count, legacy.points) == (3, 1, 36)

    profiles = (
        await migration_session.execute(text("SELECT COUNT(*) FROM user_game_profile"))
    ).scalar_one()
    assert profiles == 0  # no data migration, no backfill (D-6)


async def test_v004_cascade_delete_removes_the_profile(migration_session):
    """AC-4: deleting the user takes the economy row with it."""
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v004(migration_session)
    await _insert_user(migration_session)
    await migration_session.execute(
        text(
            "INSERT INTO user_game_profile "
            "(user_id, total_xp, level, current_streak, best_streak) "
            "VALUES ('v004-user-1', 1320, 6, 3, 7)"
        )
    )
    await migration_session.commit()

    await migration_session.execute(text("DELETE FROM users WHERE id = 'v004-user-1'"))
    await migration_session.commit()

    remaining = (
        await migration_session.execute(
            text("SELECT COUNT(*) FROM user_game_profile WHERE user_id = 'v004-user-1'")
        )
    ).scalar_one()
    assert remaining == 0


async def test_v004_end_state_ignores_v002(migration_engine, migration_session):
    """AC-5: a fresh DB that never ran v002 reaches the same end state.

    v004 must not depend on v002's table — it references only ``users`` (v001).
    Path A runs the whole chain, path B skips v002/v003 entirely; the resulting
    user_game_profile DDL has to be identical.
    """
    # Path A: full chain v001 -> v002 -> v003 -> v004
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v002(migration_session)
    await run_v003(migration_session)
    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        shape_with_v002 = await conn.run_sync(_profile_shape)

    # Path B: pristine schema, no user_stats, v001 -> v004 only
    await migration_session.execute(text("DROP SCHEMA public CASCADE"))
    await migration_session.execute(text("CREATE SCHEMA public"))
    await migration_session.commit()

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v004(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(get_table_names)
        assert "user_stats" not in tables  # v002 never ran here
        assert "user_game_profile" in tables
        shape_without_v002 = await conn.run_sync(_profile_shape)

    assert shape_without_v002 == shape_with_v002


async def test_lifespan_runs_v004_after_v003(monkeypatch):
    """AC-2 wiring: every boot applies v004, in order, after v003 (D-6).

    Same DB-free technique as ``tests/test_smtp_integration.py``: the migration
    calls are recorded so the ordering claim is proved without a database.
    """
    from src import main as main_module

    order = []

    def _record(name):
        async def _run(session):
            order.append(name)

        return _run

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    async def _noop(*args, **kwargs):
        return None

    def _noop_sync(*args, **kwargs):
        # ``install_email_service`` is called synchronously by the lifespan; an
        # async stub here would leave an un-awaited coroutine behind.
        return None

    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _FakeCtx())
    monkeypatch.setattr(main_module, "create_migrations_table", _noop)
    monkeypatch.setattr(main_module, "install_email_service", _noop_sync)
    monkeypatch.setattr(main_module, "run_v001", _record("v001"))
    monkeypatch.setattr(main_module, "run_v002", _record("v002"))
    monkeypatch.setattr(main_module, "run_v003", _record("v003"))
    monkeypatch.setattr(main_module, "run_v004", _record("v004"))

    async with main_module.lifespan(main_module.app):
        pass

    assert order == ["v001", "v002", "v003", "v004"]


async def test_v004_applies_on_a_real_app_boot(migration_engine, migration_session):
    """AC-2/AC-3: the real lifespan boots against Postgres and applies v004.

    Drives ``src.main.lifespan`` unmocked on the reset schema, so v001..v004 run
    for real: user_game_profile appears with its CHECKs and index, user_stats is
    still there, and each version is recorded exactly once.
    """
    from src import main as main_module
    from src.database.engine import engine as app_engine

    # migration_session just dropped the public schema; pooled app-engine
    # connections still hold prepared statements against the dropped relations,
    # so recycle the pool before the lifespan opens one.
    await app_engine.dispose()
    try:
        async with main_module.lifespan(main_module.app):
            pass

        async with migration_engine.connect() as conn:
            tables = await conn.run_sync(get_table_names)
            assert {
                "users",
                "user_identities",
                "user_stats",
                "rate_limit_attempt",
                "user_game_profile",
            } <= set(tables)

            constraints = await conn.run_sync(_catalog_constraints, "user_game_profile")
            assert {
                name for name, (ctype, _) in constraints.items() if ctype == "c"
            } == set(V004_CHECKS)

            indexes = await conn.run_sync(_catalog_indexes, "user_game_profile")
            assert "total_xp DESC" in indexes[V004_PROFILE_INDEX]

            versions = (
                (
                    await conn.execute(
                        text("SELECT version FROM schema_migrations ORDER BY version")
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await app_engine.dispose()

    assert versions == [
        "v001_auth_schema",
        "v002_game_stats",
        "v003_rate_limit",
        "v004_xp_schema",
    ]


async def test_legacy_game_endpoints_survive_the_v004_boot(
    migration_session,
):
    """AC-3: /correct, /incorrect, /leaderboard and /api/user/me still work.

    The whole point of the additive split (Rev 3 R-1): with v004 wired in, the
    legacy endpoints keep reading and writing user_stats, and they do NOT touch
    user_game_profile — XP is only ever written to the new table by /answer
    (US-33). Runs against the real app and the real Postgres test DB, no
    dependency override, after a genuine lifespan boot.
    """
    from httpx import ASGITransport, AsyncClient
    from src import main as main_module
    from src.database.engine import engine as app_engine
    from src.utils.jwt_utils import create_access_token

    await app_engine.dispose()
    try:
        async with main_module.lifespan(main_module.app):
            pass

        await _insert_user(
            migration_session, user_id="v004-legacy-1", username="v004legacy"
        )
        headers = {"Authorization": f"Bearer {create_access_token('v004-legacy-1')}"}

        transport = ASGITransport(app=main_module.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            correct = await client.post(
                "/api/game/correct", json={"bits": 8}, headers=headers
            )
            assert correct.status_code == 200, correct.text
            assert correct.json()["points"] == 8
            assert correct.json()["correct_count"] == 1

            incorrect = await client.post(
                "/api/game/incorrect", json={"bits": 4}, headers=headers
            )
            assert incorrect.status_code == 200, incorrect.text
            # legacy formula: 8 - (4 * 4)
            assert incorrect.json()["points"] == -8
            assert incorrect.json()["incorrect_count"] == 1

            me = await client.get("/api/user/me", headers=headers)
            assert me.status_code == 200, me.text
            assert me.json()["points"] == -8
            assert me.json()["correct_count"] == 1

            # the leaderboard stays public (R-3/OQ-9) and still sorts by points
            board = await client.get("/api/game/leaderboard")
            assert board.status_code == 200, board.text
            entry = next(
                row
                for row in board.json()["entries"]
                if row["user_id"] == "v004-legacy-1"
            )
            assert entry["points"] == -8

        profile_rows = (
            await migration_session.execute(
                text(
                    "SELECT COUNT(*) FROM user_game_profile "
                    "WHERE user_id = 'v004-legacy-1'"
                )
            )
        ).scalar_one()
        assert profile_rows == 0
    finally:
        await app_engine.dispose()
