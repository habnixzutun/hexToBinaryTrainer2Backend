"""US-04: Create rate_limit_attempt table for persistent rate limiting.

ADR 001: Use database table instead of Redis (no Redis in project).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check if migration v003 has already been applied.

    Args:
        session: AsyncSession for database operations

    Returns:
        True if migration already applied, False otherwise
    """
    try:
        result = await session.execute(
            text("SELECT 1 FROM schema_migrations WHERE version = 'v003_rate_limit'")
        )
        return result.scalar() is not None
    except Exception:
        # Table doesn't exist yet
        return False


async def mark_migration_applied(session: AsyncSession) -> None:
    """Record that migration v003 has been applied.

    Args:
        session: AsyncSession for database operations
    """
    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES ('v003_rate_limit', CURRENT_TIMESTAMP)"
        )
    )


async def run_migration(session: AsyncSession) -> None:
    """Execute the rate_limit_attempt table migration.

    Creates a persistent rate limiting table to replace in-memory rate limiting.
    Tracks attempts by key (e.g., 'register:ip:email') with expiration.

    Args:
        session: AsyncSession for database operations

    Raises:
        Exception: If migration fails, transaction is rolled back
    """
    # Check if migration already applied
    if await migration_already_applied(session):
        return

    # Create rate_limit_attempt table
    await session.execute(
        text("""
        CREATE TABLE rate_limit_attempt (
            id TEXT PRIMARY KEY,
            key TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    )

    # Create composite index for efficient lookups and cleanup
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_rate_limit_attempt_key_expires "
            "ON rate_limit_attempt(key, expires_at)"
        )
    )

    # Mark migration as applied
    await mark_migration_applied(session)
    await session.commit()
