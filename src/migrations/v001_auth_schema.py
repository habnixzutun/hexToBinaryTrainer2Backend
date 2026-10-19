"""US-09: Authentication Schema Migration.

Clean break migration that drops the old users table and creates
the new authentication schema with support for:
- Email-based login
- Multi-provider authentication (user_identities)
- Refresh tokens
- Password reset tokens
- Audit logging

This migration runs exactly once. It tracks execution in schema_migrations
table to prevent re-execution on subsequent app starts.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check if migration v001 has already been applied.

    Args:
        session: AsyncSession for database operations

    Returns:
        True if migration already applied, False otherwise
    """
    try:
        result = await session.execute(
            text(
                "SELECT 1 FROM schema_migrations WHERE version = 'v001_auth_schema'"
            )
        )
        return result.scalar() is not None
    except Exception:
        # Table doesn't exist yet
        return False


async def mark_migration_applied(session: AsyncSession) -> None:
    """Record that migration v001 has been applied.

    Args:
        session: AsyncSession for database operations
    """
    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES ('v001_auth_schema', CURRENT_TIMESTAMP)"
        )
    )


async def create_migrations_table(session: AsyncSession) -> None:
    """Create schema_migrations table if it doesn't exist.

    This table tracks which migrations have been applied to prevent
    re-execution on subsequent app starts.

    Args:
        session: AsyncSession for database operations
    """
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))


async def run_migration(session: AsyncSession) -> None:
    """Execute the authentication schema migration.

    This migration runs exactly once. It checks if it has already been
    applied via the schema_migrations table. If not, it:
    1. Drops the old users table (clean break, no data migration)
    2. Creates new auth tables with proper constraints
    3. Records that the migration has been applied

    Args:
        session: AsyncSession for database operations

    Raises:
        Exception: If migration fails, transaction is rolled back
    """
    # Check if migration already applied
    if await migration_already_applied(session):
        return

    # Drop old users table if it exists (clean break)
    # CASCADE required to drop dependent tables (user_identities, tokens, audit_log)
    await session.execute(text("DROP TABLE IF EXISTS users CASCADE"))

    # Create new users table with UNIQUE constraints
    await session.execute(text("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_username_change_at TIMESTAMP WITH TIME ZONE
        )
    """))

    # Create user_identities table with UNIQUE constraint on provider+provider_user_id
    await session.execute(text("""
        CREATE TABLE user_identities (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            password_hash TEXT,
            verified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(provider, provider_user_id)
        )
    """))

    # Create user_refresh_tokens table
    await session.execute(text("""
        CREATE TABLE user_refresh_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create password_reset_tokens table
    await session.execute(text("""
        CREATE TABLE password_reset_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE,
            type TEXT NOT NULL CHECK(type IN ('password_reset', 'email_change')),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create user_audit_log table
    # Note: user_id is NOT NULL for MVP; future enhancement could make it nullable
    # to log failed login attempts. ON DELETE CASCADE chosen for MVP simplicity;
    # future enhancement could use RESTRICT for audit trail preservation.
    await session.execute(text("""
        CREATE TABLE user_audit_log (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            ip_address TEXT,
            user_agent TEXT,
            metadata_json TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create indexes for performance
    await session.execute(
        text("CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)")
    )
    await session.execute(
        text("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)")
    )
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_user_identities_provider "
        "ON user_identities(provider, provider_user_id)"
    ))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_user_refresh_tokens_token_hash "
        "ON user_refresh_tokens(token_hash)"
    ))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash "
        "ON password_reset_tokens(token_hash)"
    ))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_user_audit_log_user_timestamp "
        "ON user_audit_log(user_id, timestamp)"
    ))

    # Mark migration as applied
    await mark_migration_applied(session)
    await session.commit()
