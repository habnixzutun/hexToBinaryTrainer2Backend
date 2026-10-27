"""US-04: Persistent rate limiting service (ADR 001: DB instead of Redis).

Provides database-backed rate limiting to replace in-memory rate limiting.
Tracks attempts by key with automatic cleanup of expired entries.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import RateLimitAttempt


class RateLimitService:
    """Database-backed rate limiting service.

    Tracks rate limit attempts in the database instead of memory,
    ensuring rate limits persist across server restarts and work
    correctly in multi-instance deployments.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize rate limit service.

        Args:
            session: AsyncSession for database operations
        """
        self.session = session

    async def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Check if request is within rate limit.

        Cleans up expired entries, counts recent attempts for the key,
        and records a new attempt if within limit.

        Args:
            key: Rate limit key (e.g., 'register:ip:email')
            limit: Maximum number of attempts allowed in window
            window_seconds: Time window in seconds

        Returns:
            True if request is allowed (under limit), False if blocked
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=window_seconds)

        # Cleanup expired entries first
        await self.session.execute(
            delete(RateLimitAttempt).where(RateLimitAttempt.expires_at < now)
        )

        # Count recent attempts for this key within the window
        result = await self.session.execute(
            select(func.count())
            .where(
                RateLimitAttempt.key == key,
                RateLimitAttempt.created_at >= cutoff,
            )
        )
        count = result.scalar() or 0

        if count >= limit:
            return False

        # Record this attempt
        new_attempt = RateLimitAttempt(
            id=str(uuid7()),
            key=key,
            expires_at=now + timedelta(seconds=window_seconds),
        )
        self.session.add(new_attempt)
        await self.session.commit()

        return True
