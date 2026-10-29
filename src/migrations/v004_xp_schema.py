"""US-27: additive XP-economy schema (``user_game_profile``).

Sprint 4, plan ``docs/sprint4-plan.md`` D-6 (Rev 3, R-1). The schema change is
split across TWO migrations on purpose:

* **v004 (this file) — CREATE only.** Creates ``user_game_profile`` and leaves the
  legacy Sprint-2 counters table completely in place, so that era's two legacy
  score writers kept working and every commit in the cascade booted with a green
  suite (sprint gate #1).
* v005 — the ``DROP``, delivered with **US-34** at sprint close in the same release
  train as the frontend switch, once US-32/US-33 removed every writer and
  US-35/US-36/US-37 every reader. Dropping the table here would have broken
  ``src/routers/game.py`` / ``src/routers/user.py`` at import time.

Rev 2 had v004 drop the table on Day 1; Rev 3 corrected that (R-1, CRITICAL).
Equally out of scope, because the DB is empty (BE-11, PO clarification #2):
**no backfill, no data migration, no value dual-write, no compatibility view.**
The two tables co-exist for the whole window, and XP is only ever written to
``user_game_profile`` by the answer path (US-28/US-33).

The DDL mirrors the :class:`src.models.game.UserGameProfile` ORM class declared
in US-26 — same columns in the same order, same types, same defaults (0 / level
1), the same four explicitly named CHECK constraints, and the same ``total_xp
DESC`` index that backs the US-36 leaderboard ordering — so a database created by
``Base.metadata.create_all`` (tests) and one migrated by this file are
indistinguishable in the catalog (pinned by
``tests/test_migrations.py::test_v004_ddl_matches_the_orm``). The PRIMARY KEY and
the FOREIGN KEY are deliberately left unnamed here, exactly as the ORM leaves
them: Postgres then derives the same automatic names (``user_game_profile_pkey``,
``user_game_profile_user_id_fkey``) on both paths.

``total_xp >= 0`` is a **backstop only** (D-5/OQ-4): the real floor is
``cum(level)``, enforced by the engine, and is not expressible as a SQL CHECK.

Idempotent via the shared ``migration_already_applied`` / ``schema_migrations``
pattern: the app lifespan re-runs every migration on every boot.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_ID = "v004_xp_schema"


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check if migration v004 has already been applied.

    Args:
        session: AsyncSession for database operations

    Returns:
        True if migration already applied, False otherwise
    """
    try:
        result = await session.execute(
            text("SELECT 1 FROM schema_migrations WHERE version = :v"),
            {"v": MIGRATION_ID},
        )
        return result.scalar() is not None
    except Exception:
        # schema_migrations does not exist yet (first boot)
        return False


async def mark_migration_applied(session: AsyncSession) -> None:
    """Record that migration v004 has been applied.

    Args:
        session: AsyncSession for database operations
    """
    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:v, CURRENT_TIMESTAMP)"
        ),
        {"v": MIGRATION_ID},
    )


async def run_migration(session: AsyncSession) -> None:
    """Apply migration v004: create the XP-economy profile table.

    ADDITIVE ONLY — never touches the legacy counters table (D-6 / Rev 3 R-1);
    v005 (US-34) owns the ``DROP``. Depends only on ``users`` from v001, so a
    database that never ran v002/v003 still reaches the same end state (US-27 AC-5).

    Args:
        session: AsyncSession for database operations

    Raises:
        Exception: If migration fails, the transaction is rolled back by the
            caller — nothing is committed half-way.
    """
    if await migration_already_applied(session):
        return

    await session.execute(
        text("""
        CREATE TABLE IF NOT EXISTS user_game_profile (
            user_id VARCHAR(36) NOT NULL,
            total_xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            current_streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            incorrect_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            CONSTRAINT ck_user_game_profile_total_xp CHECK (total_xp >= 0),
            CONSTRAINT ck_user_game_profile_level CHECK (level >= 1),
            CONSTRAINT ck_user_game_profile_current_streak
                CHECK (current_streak >= 0),
            CONSTRAINT ck_user_game_profile_best_streak CHECK (best_streak >= 0)
        )
    """)
    )

    # Leaderboard ordering (US-36): total_xp DESC, tiebreak incorrect_count ASC
    # then user_id ASC — the index only covers the primary sort key.
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_user_game_profile_total_xp "
            "ON user_game_profile(total_xp DESC)"
        )
    )

    await mark_migration_applied(session)
    await session.commit()
