"""US-44: Device Tracking Migration.

Adds four new nullable TEXT columns to user_refresh_tokens to store device
information at login time:
- ip_address:       Client IP address
- user_agent:       Browser / client user-agent string
- location_city:    City resolved from IP via GeolocationService
- location_country: Country resolved from IP via GeolocationService

All columns are nullable so that existing rows (written before this migration)
are not broken — they will carry NULL in all four new columns after the upgrade.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_ID = "v006_device_tracking"


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check whether v006 has already been applied.

    Args:
        session: AsyncSession for database operations

    Returns:
        True if migration already applied, False otherwise
    """
    try:
        result = await session.execute(
            text(
                "SELECT 1 FROM schema_migrations WHERE version = :v"
            ),
            {"v": MIGRATION_ID},
        )
        return result.scalar() is not None
    except Exception:
        return False


async def run_migration(session: AsyncSession) -> None:
    """Add device-tracking columns to user_refresh_tokens.

    Idempotent: the migration is tracked in schema_migrations so a second
    call on a DB that already has the columns is a no-op (no ADD COLUMN,
    no duplicate row in schema_migrations).

    Args:
        session: AsyncSession for database operations
    """
    if await migration_already_applied(session):
        return

    await session.execute(
        text(
            "ALTER TABLE user_refresh_tokens "
            "ADD COLUMN ip_address TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE user_refresh_tokens "
            "ADD COLUMN user_agent TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE user_refresh_tokens "
            "ADD COLUMN location_city TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE user_refresh_tokens "
            "ADD COLUMN location_country TEXT"
        )
    )

    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:v, CURRENT_TIMESTAMP)"
        ),
        {"v": MIGRATION_ID},
    )
    await session.commit()
