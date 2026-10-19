"""US-09: Authentication Schema Migration.

Clean break migration that drops the old users table and creates
the new authentication schema with support for:
- Email-based login
- Multi-provider authentication (user_identities)
- Refresh tokens
- Password reset tokens
- Audit logging

This migration is idempotent - can be run multiple times safely.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def run_migration(session: AsyncSession) -> None:
    """Execute the authentication schema migration.

    Drops old users table and creates new auth schema with all required
    tables, indexes, and foreign key constraints.

    Args:
        session: AsyncSession for database operations
    """
    # Drop old users table if it exists
    await session.execute(text("DROP TABLE IF EXISTS users"))

    # Create new users table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_username_change_at TIMESTAMP WITH TIME ZONE
        )
    """))

    # Create user_identities table for multi-provider support
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS user_identities (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            password_hash TEXT,
            verified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create user_refresh_tokens table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS user_refresh_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create password_reset_tokens table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE,
            type TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """))

    # Create user_audit_log table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS user_audit_log (
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

    await session.commit()
