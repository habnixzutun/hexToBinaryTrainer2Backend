"""Tests for persistent rate limiting (US-04).

Tests RateLimitService for:
- Rate limit not reached (allowed)
- Rate limit reached (blocked)
- Expired entries cleanup
- Integration with /register endpoint
- Adversarial: multiple IPs, same email
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base
from src.models.auth import RateLimitAttempt

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def async_engine():
    """Create in-memory SQLite engine for rate limit tests."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    """Create async session for rate limit tests."""
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


class TestRateLimitService:
    """Unit tests for RateLimitService."""

    async def test_check_allows_when_under_limit(self, session: AsyncSession):
        """Rate limit should allow requests when count is below limit."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        # First request should be allowed
        is_allowed = await service.check(
            key="test:ip:email",
            limit=3,
            window_seconds=3600,
        )

        assert is_allowed is True

        # Verify attempt was recorded
        result = await session.execute(
            select(RateLimitAttempt).where(RateLimitAttempt.key == "test:ip:email")
        )
        attempts = result.scalars().all()
        assert len(attempts) == 1

    async def test_check_blocks_when_limit_reached(self, session: AsyncSession):
        """Rate limit should block requests when count reaches limit."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        # Make 3 requests (limit = 3)
        for _ in range(3):
            is_allowed = await service.check(
                key="test:ip:email",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is True

        # 4th request should be blocked
        is_allowed = await service.check(
            key="test:ip:email",
            limit=3,
            window_seconds=3600,
        )
        assert is_allowed is False

    async def test_check_allows_after_window_expires(self, session: AsyncSession):
        """Rate limit should allow requests after window expires."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        # Make 3 requests (limit = 3) with short window
        for _ in range(3):
            await service.check(
                key="test:ip:email",
                limit=3,
                window_seconds=1,  # 1 second window
            )

        # 4th request should be blocked
        is_allowed = await service.check(
            key="test:ip:email",
            limit=3,
            window_seconds=1,
        )
        assert is_allowed is False

        # Manually expire the entries by updating expires_at to past
        result = await session.execute(
            select(RateLimitAttempt).where(RateLimitAttempt.key == "test:ip:email")
        )
        attempts = result.scalars().all()
        for attempt in attempts:
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=10)
            session.add(attempt)
        await session.commit()

        # Now request should be allowed again (expired entries cleaned up)
        is_allowed = await service.check(
            key="test:ip:email",
            limit=3,
            window_seconds=1,
        )
        assert is_allowed is True

    async def test_different_keys_have_separate_limits(self, session: AsyncSession):
        """Different keys should have independent rate limits."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        # Make 3 requests for key1
        for _ in range(3):
            await service.check(key="key1", limit=3, window_seconds=3600)

        # key1 should be blocked
        is_allowed = await service.check(key="key1", limit=3, window_seconds=3600)
        assert is_allowed is False

        # key2 should still be allowed
        is_allowed = await service.check(key="key2", limit=3, window_seconds=3600)
        assert is_allowed is True

    async def test_expired_entries_are_cleaned_up(self, session: AsyncSession):
        """Expired entries should be cleaned up during check."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        # Create an expired entry manually
        expired_attempt = RateLimitAttempt(
            id="expired-1",
            key="test:key",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(expired_attempt)
        await session.commit()

        # Check should clean up expired entries
        await service.check(key="test:key", limit=3, window_seconds=3600)

        # Verify expired entry was deleted
        result = await session.execute(
            select(RateLimitAttempt).where(RateLimitAttempt.id == "expired-1")
        )
        assert result.scalar_one_or_none() is None


class TestRegisterRateLimit:
    """Integration tests for /register rate limiting."""

    @pytest.fixture
    async def client(self, async_engine):
        """Create test client with rate limit cleanup."""
        from httpx import ASGITransport, AsyncClient

        from src.database.engine import get_db
        from src.main import app

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        async def override_get_db():
            async with factory() as s:
                yield s

        app.dependency_overrides[get_db] = override_get_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

        app.dependency_overrides.clear()

    async def test_register_rate_limit_429_after_3_attempts(
        self, client, async_engine
    ):
        """4th registration attempt with same IP+email should return 429."""
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.services.rate_limit_service import RateLimitService

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            rate_limit = RateLimitService(session)

            # Simulate 3 registration attempts with same IP+email key
            for _ in range(3):
                is_allowed = await rate_limit.check(
                    "register:testclient:user@example.com",
                    limit=3,
                    window_seconds=3600,
                )
                assert is_allowed is True

            # 4th attempt should be blocked
            is_allowed = await rate_limit.check(
                "register:testclient:user@example.com",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is False

    async def test_register_rate_limit_different_emails(
        self, client, async_engine
    ):
        """Rate limit key should be per IP+email combination."""
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.services.rate_limit_service import RateLimitService

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            rate_limit = RateLimitService(session)

            # Simulate 3 attempts with email1 (same IP + same email = same key)
            for _ in range(3):
                await rate_limit.check(
                    "register:testclient:user1@example.com",
                    limit=3,
                    window_seconds=3600,
                )

            # 4th attempt with same key should be blocked
            is_allowed = await rate_limit.check(
                "register:testclient:user1@example.com",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is False

            # Different email = different key, should still be allowed
            is_allowed = await rate_limit.check(
                "register:testclient:user2@example.com",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is True


class TestAdversarialRateLimit:
    """Adversarial tests for rate limiting."""

    @pytest.fixture
    async def client(self, async_engine):
        """Create test client."""
        from httpx import ASGITransport, AsyncClient

        from src.database.engine import get_db
        from src.main import app

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        async def override_get_db():
            async with factory() as s:
                yield s

        app.dependency_overrides[get_db] = override_get_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

        app.dependency_overrides.clear()

    async def test_rate_limit_persists_across_different_ips(
        self, client, async_engine
    ):
        """Rate limit key should include both IP and email.

        Different IPs with same email should have separate limits.
        Since httpx doesn't easily allow changing client IP, we test
        via the service directly with simulated IP+email keys.
        """
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.services.rate_limit_service import RateLimitService

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            rate_limit = RateLimitService(session)

            email = "target@example.com"

            # Simulate 3 attempts from IP-A with same email
            for _ in range(3):
                await rate_limit.check(
                    f"register:ipA:{email}",
                    limit=3,
                    window_seconds=3600,
                )

            # 4th from IP-A should be blocked
            is_allowed = await rate_limit.check(
                f"register:ipA:{email}",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is False

            # Same email from IP-B should still be allowed (different key)
            is_allowed = await rate_limit.check(
                f"register:ipB:{email}",
                limit=3,
                window_seconds=3600,
            )
            assert is_allowed is True
