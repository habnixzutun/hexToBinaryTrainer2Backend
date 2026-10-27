"""Tests for US-05: Adaptive Login Lockout.

Tests progressive lockout for /login endpoint:
- 5 failures → 1 minute lockout
- 10 failures → 5 minute lockout
- 20 failures → 15 minute lockout
- Successful login resets counter
- Different IPs have separate counters
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base
from src.models.auth import RateLimitAttempt

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def async_engine():
    """Create in-memory SQLite engine for lockout tests."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    """Create async session for lockout tests."""
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


class TestLoginLockoutUtility:
    """Unit tests for login lockout utility functions."""

    async def test_check_login_lockout_allows_initial_request(
        self, session: AsyncSession
    ):
        """First login attempt should be allowed."""
        from src.utils.login_lockout import check_login_lockout

        is_allowed = await check_login_lockout("192.168.1.1", session)
        assert is_allowed is True

    async def test_check_login_lockout_allows_4_failures(
        self, session: AsyncSession
    ):
        """First 4 failures should be allowed (under 5 threshold)."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 4 failures
        for _ in range(4):
            await record_login_failure(ip, session)

        # Should still be allowed
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is True

    async def test_check_login_lockout_blocks_after_5_failures(
        self, session: AsyncSession
    ):
        """5th failure should trigger 1-minute lockout."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 5 failures
        for _ in range(5):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

    async def test_check_login_lockout_blocks_after_10_failures(
        self, session: AsyncSession
    ):
        """10th failure should trigger 5-minute lockout."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 10 failures
        for _ in range(10):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

    async def test_check_login_lockout_blocks_after_20_failures(
        self, session: AsyncSession
    ):
        """20th failure should trigger 15-minute lockout."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 20 failures
        for _ in range(20):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

    async def test_reset_login_lockout_clears_all_counters(
        self, session: AsyncSession
    ):
        """Reset should clear all lockout counters for an IP."""
        from src.utils.login_lockout import (
            check_login_lockout,
            record_login_failure,
            reset_login_lockout,
        )

        ip = "192.168.1.1"

        # Record 10 failures (triggers 5-min lockout)
        for _ in range(10):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

        # Reset lockout
        await reset_login_lockout(ip, session)

        # Should be allowed again
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is True

    async def test_different_ips_have_separate_counters(
        self, session: AsyncSession
    ):
        """Different IPs should have independent lockout counters."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip1 = "192.168.1.1"
        ip2 = "192.168.1.2"

        # Record 5 failures for IP1 (triggers lockout)
        for _ in range(5):
            await record_login_failure(ip1, session)

        # IP1 should be blocked
        is_allowed_ip1 = await check_login_lockout(ip1, session)
        assert is_allowed_ip1 is False

        # IP2 should still be allowed (different IP, separate counter)
        is_allowed_ip2 = await check_login_lockout(ip2, session)
        assert is_allowed_ip2 is True

    async def test_lockout_1min_expires_after_window(
        self, session: AsyncSession
    ):
        """1-minute lockout should expire after 60 seconds."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 5 failures
        for _ in range(5):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

        # Manually expire the 1-min lockout entries
        result = await session.execute(
            select(RateLimitAttempt).where(
                RateLimitAttempt.key.like(f"login_lockout_1min:{ip}%")
            )
        )
        attempts = result.scalars().all()
        for attempt in attempts:
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=70)
            session.add(attempt)
        await session.commit()

        # Now should be allowed again (expired entries cleaned up)
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is True

    async def test_lockout_5min_expires_after_window(
        self, session: AsyncSession
    ):
        """5-minute lockout should expire after 300 seconds."""
        from src.utils.login_lockout import check_login_lockout, record_login_failure

        ip = "192.168.1.1"

        # Record 10 failures
        for _ in range(10):
            await record_login_failure(ip, session)

        # Should be blocked
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is False

        # Manually expire the 5-min lockout entries
        result = await session.execute(
            select(RateLimitAttempt).where(
                RateLimitAttempt.key.like(f"login_lockout_5min:{ip}%")
            )
        )
        attempts = result.scalars().all()
        for attempt in attempts:
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=310)
            session.add(attempt)
        await session.commit()

        # Should still be blocked by 1-min lockout (5 < 10)
        # But if we also expire 1-min lockout...
        result = await session.execute(
            select(RateLimitAttempt).where(
                RateLimitAttempt.key.like(f"login_lockout_1min:{ip}%")
            )
        )
        attempts = result.scalars().all()
        for attempt in attempts:
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=70)
            session.add(attempt)
        await session.commit()

        # Now should be allowed
        is_allowed = await check_login_lockout(ip, session)
        assert is_allowed is True

    async def test_record_login_failure_creates_correct_keys(
        self, session: AsyncSession
    ):
        """record_login_failure should create entries with correct keys."""
        from src.utils.login_lockout import record_login_failure

        ip = "192.168.1.1"

        # Record 1 failure
        await record_login_failure(ip, session)

        # Verify entries were created with correct keys
        result = await session.execute(
            select(RateLimitAttempt).where(
                RateLimitAttempt.key.like(f"login_lockout_1min:{ip}%")
            )
        )
        attempts = result.scalars().all()
        assert len(attempts) == 1


class TestLoginLockoutIntegration:
    """Integration tests for /login endpoint with lockout."""

    @pytest.fixture
    async def client(self, async_engine):
        """Create test client with lockout cleanup."""
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

    async def test_login_lockout_returns_429_after_5_failures(
        self, client, async_engine
    ):
        """6th login attempt after 5 failures should return 429."""
        from datetime import UTC, datetime

        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.models.auth import User, UserIdentity
        from src.utils.password import hash_password

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        # Create a test user
        async with factory() as session:
            user = User(
                id="user-lockout-test",
                username="lockoutuser",
                email="lockout@example.com",
            )
            session.add(user)

            identity = UserIdentity(
                id="identity-lockout-test",
                user_id="user-lockout-test",
                provider="email",
                provider_user_id="lockout@example.com",
                password_hash=hash_password("CorrectPass1!"),
                verified_at=datetime.now(UTC),
            )
            session.add(identity)
            await session.commit()

        # Make 5 failed login attempts with wrong password
        for _ in range(5):
            response = await client.post(
                "/api/auth/login",
                json={"email": "lockout@example.com", "password": "WrongPass1!"},
            )
            assert response.status_code in [401, 403]  # Wrong password or unverified

        # 6th attempt should return 429 (locked out)
        response = await client.post(
            "/api/auth/login",
            json={"email": "lockout@example.com", "password": "WrongPass1!"},
        )
        assert response.status_code == 429
        assert "locked out" in response.json()["detail"].lower()

    async def test_successful_login_resets_lockout_counter(
        self, client, async_engine
    ):
        """Successful login should reset the lockout counter."""
        from datetime import UTC, datetime

        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.models.auth import User, UserIdentity
        from src.utils.password import hash_password

        factory = sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        # Create a test user
        async with factory() as session:
            user = User(
                id="user-reset-test",
                username="resetuser",
                email="reset@example.com",
            )
            session.add(user)

            identity = UserIdentity(
                id="identity-reset-test",
                user_id="user-reset-test",
                provider="email",
                provider_user_id="reset@example.com",
                password_hash=hash_password("CorrectPass1!"),
                verified_at=datetime.now(UTC),
            )
            session.add(identity)
            await session.commit()

        # Make 4 failed login attempts
        for _ in range(4):
            await client.post(
                "/api/auth/login",
                json={"email": "reset@example.com", "password": "WrongPass1!"},
            )

        # Successful login should reset counter
        response = await client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "CorrectPass1!"},
        )
        assert response.status_code == 200

        # Now make 4 more failed attempts - should still be allowed
        for _ in range(4):
            response = await client.post(
                "/api/auth/login",
                json={"email": "reset@example.com", "password": "WrongPass1!"},
            )
            assert response.status_code in [401, 403]

        # 5th failure after reset triggers lockout (returns 401, but lockout is now active)
        response = await client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "WrongPass1!"},
        )
        assert response.status_code == 401

        # 6th attempt should return 429 (locked out)
        response = await client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "WrongPass1!"},
        )
        assert response.status_code == 429
