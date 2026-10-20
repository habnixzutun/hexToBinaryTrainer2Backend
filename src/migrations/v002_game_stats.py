"""Migration v002: Create user_stats table for game statistics."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_ID = "v002_game_stats"


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check if migration v002 has already been applied."""
    try:
        result = await session.execute(
            text("SELECT 1 FROM schema_migrations WHERE version = :v"),
            {"v": MIGRATION_ID},
        )
        return result.scalar() is not None
    except Exception:
        return False


async def run_migration(session: AsyncSession) -> None:
    """Apply migration: create user_stats table."""
    if await migration_already_applied(session):
        return

    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id VARCHAR(36) NOT NULL,
            correct_count INTEGER NOT NULL DEFAULT 0,
            incorrect_count INTEGER NOT NULL DEFAULT 0,
            points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:v, CURRENT_TIMESTAMP)"
        ),
        {"v": MIGRATION_ID},
    )
    await session.commit()
