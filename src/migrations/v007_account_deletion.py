"""US-47: Account deletion tokens migration.

Creates the ``account_deletion_tokens`` table that stores time-limited
single-use tokens used to confirm account deletion requests.

The table design mirrors ``password_reset_tokens`` in v001, with an
explicit CASCADE FK so that deleting a user automatically removes any
pending deletion token rows.

Idempotent: checked against ``schema_migrations``, so a second boot is a
no-op.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_ID: Final[str] = "v007_account_deletion"


async def migration_already_applied(session: AsyncSession) -> bool:
    """Return True if this migration has already been applied."""
    try:
        result = await session.execute(
            text("SELECT 1 FROM schema_migrations WHERE version = :v"),
            {"v": MIGRATION_ID},
        )
        return result.scalar() is not None
    except Exception:
        return False


async def mark_migration_applied(session: AsyncSession) -> None:
    """Record that this migration has been applied."""
    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:v, CURRENT_TIMESTAMP)"
        ),
        {"v": MIGRATION_ID},
    )


async def run_migration(session: AsyncSession) -> None:
    """Apply migration v007: create account_deletion_tokens table.

    Args:
        session: AsyncSession for database operations

    Raises:
        Exception: If migration fails, transaction is rolled back by the caller.
    """
    if await migration_already_applied(session):
        return

    await session.execute(
        text("""
        CREATE TABLE account_deletion_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    )

    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_account_deletion_tokens_user_id "
            "ON account_deletion_tokens(user_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_account_deletion_tokens_token_hash "
            "ON account_deletion_tokens(token_hash)"
        )
    )

    await mark_migration_applied(session)
    await session.commit()
