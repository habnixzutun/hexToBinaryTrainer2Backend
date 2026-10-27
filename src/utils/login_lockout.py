"""US-05: Adaptive Login Lockout.

Provides progressive lockout for login attempts to prevent brute-force attacks.
After multiple failed attempts, the IP is temporarily blocked with increasing durations.

Thresholds:
- 5 failures → 1 minute lockout
- 10 failures → 5 minute lockout
- 20 failures → 15 minute lockout
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import RateLimitAttempt
from src.services.rate_limit_service import RateLimitService

# Lockout thresholds and durations (in seconds)
LOCKOUT_THRESHOLD_1MIN = 5
LOCKOUT_DURATION_1MIN = 60

LOCKOUT_THRESHOLD_5MIN = 10
LOCKOUT_DURATION_5MIN = 300

LOCKOUT_THRESHOLD_15MIN = 20
LOCKOUT_DURATION_15MIN = 900


async def check_login_lockout(ip_address: str, session: AsyncSession) -> bool:
    """Check if login is allowed for the given IP address.

    Checks if the IP is currently locked out based on failed login attempts.
    Uses progressive lockout durations: 1min, 5min, 15min.

    Args:
        ip_address: Client IP address
        session: AsyncSession for database operations

    Returns:
        True if login is allowed, False if locked out
    """
    now = datetime.now(UTC)

    # Cleanup expired entries first
    await session.execute(
        delete(RateLimitAttempt).where(RateLimitAttempt.expires_at < now)
    )

    # Check 15-minute lockout (highest threshold)
    key_15min = f"login_lockout_15min:{ip_address}"
    result = await session.execute(
        select(func.count()).where(
            RateLimitAttempt.key == key_15min,
            RateLimitAttempt.created_at >= now - timedelta(seconds=LOCKOUT_DURATION_15MIN),
        )
    )
    count_15min = result.scalar() or 0
    if count_15min >= LOCKOUT_THRESHOLD_15MIN:
        return False

    # Check 5-minute lockout (medium threshold)
    key_5min = f"login_lockout_5min:{ip_address}"
    result = await session.execute(
        select(func.count()).where(
            RateLimitAttempt.key == key_5min,
            RateLimitAttempt.created_at >= now - timedelta(seconds=LOCKOUT_DURATION_5MIN),
        )
    )
    count_5min = result.scalar() or 0
    if count_5min >= LOCKOUT_THRESHOLD_5MIN:
        return False

    # Check 1-minute lockout (lowest threshold)
    key_1min = f"login_lockout_1min:{ip_address}"
    result = await session.execute(
        select(func.count()).where(
            RateLimitAttempt.key == key_1min,
            RateLimitAttempt.created_at >= now - timedelta(seconds=LOCKOUT_DURATION_1MIN),
        )
    )
    count_1min = result.scalar() or 0
    if count_1min >= LOCKOUT_THRESHOLD_1MIN:
        return False

    return True


async def record_login_failure(ip_address: str, session: AsyncSession) -> None:
    """Record a failed login attempt for the given IP address.

    Creates entries in rate_limit_attempt table with different TTLs to track
    progressive lockout thresholds.

    Args:
        ip_address: Client IP address
        session: AsyncSession for database operations
    """
    now = datetime.now(UTC)

    # Create entry for 1-minute lockout tracking
    entry_1min = RateLimitAttempt(
        id=str(uuid7()),
        key=f"login_lockout_1min:{ip_address}",
        expires_at=now + timedelta(seconds=LOCKOUT_DURATION_1MIN),
    )
    session.add(entry_1min)

    # Create entry for 5-minute lockout tracking
    entry_5min = RateLimitAttempt(
        id=str(uuid7()),
        key=f"login_lockout_5min:{ip_address}",
        expires_at=now + timedelta(seconds=LOCKOUT_DURATION_5MIN),
    )
    session.add(entry_5min)

    # Create entry for 15-minute lockout tracking
    entry_15min = RateLimitAttempt(
        id=str(uuid7()),
        key=f"login_lockout_15min:{ip_address}",
        expires_at=now + timedelta(seconds=LOCKOUT_DURATION_15MIN),
    )
    session.add(entry_15min)

    await session.commit()


async def reset_login_lockout(
    ip_address: str, session: AsyncSession, email: str | None = None
) -> None:
    """Reset all lockout counters for the given IP address.

    Called after successful login to clear the lockout state.
    Also clears US-04 rate limit entries if email is provided.

    Args:
        ip_address: Client IP address
        session: AsyncSession for database operations
        email: Optional email to also clear US-04 rate limit for this IP+email
    """
    # Delete all lockout entries for this IP (US-05)
    await session.execute(
        delete(RateLimitAttempt).where(
            RateLimitAttempt.key.like(f"login_lockout_%:{ip_address}")
        )
    )

    # Also clear US-04 rate limit entries for this IP+email if provided
    if email:
        await session.execute(
            delete(RateLimitAttempt).where(
                RateLimitAttempt.key == f"login:{ip_address}:{email}"
            )
        )

    await session.commit()
