"""Tests for US-05: Adaptive Login Lockout.

Tests progressive lockout for /login endpoint:
- 5 failures → 1 minute lockout
- 10 failures → 5 minute lockout
- 20 failures → 15 minute lockout
- Successful login resets counter
- Different IPs have separate counters
- Lockout expiry, and the Wave 2a B-1 rule that expiry comes from the ``created_at``
  window while the physical rows are reclaimed ONLY by the periodic sweep
  (``purge_expired_attempts``) — never by a ``DELETE`` inside ``check_login_lockout``
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base
from src.models.auth import RateLimitAttempt

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# How far past a tier's boundary the aged rows are pushed. One second is enough to
# make "past the window" unambiguous against wall-clock jitter inside a test run.
EXPIRY_GRACE_SECONDS = 1


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


@contextmanager
def _write_log(engine) -> Iterator[list[str]]:
    """Every write statement (``INSERT``/``UPDATE``/``DELETE``) inside the block.

    Deliberately narrower than the full statement log
    ``tests/test_rate_limit.py`` builds for its ordering claims: the lockout
    guarantee (Wave 2a B-1) is a *negative* one — ``check_login_lockout`` must issue
    no ``DELETE`` — so only writes are interesting here, and the tier counts remain
    the ``SELECT`` reads the function is supposed to do.
    """
    writes: list[str] = []

    def _capture(sync_conn, cursor, statement, *args, **kwargs) -> None:
        verb = statement.strip().split(maxsplit=1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        yield writes
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)


async def _row_count(session: AsyncSession, key_pattern: str) -> int:
    """How many rate-limit rows a key pattern still holds, counted in SQL."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(RateLimitAttempt)
            .where(RateLimitAttempt.key.like(key_pattern))
        )
        or 0
    )


async def _expire_tier(
    session: AsyncSession, key_prefix: str, *, window_seconds: int
) -> None:
    """Age one tier's rows so its window has really elapsed (B-1 semantics).

    Both clocks move, because that is what real ageing does — and they are read by
    two different owners: ``created_at`` leaves the tier's counting window, which is
    what :func:`src.utils.login_lockout.check_login_lockout` decides on, and
    ``expires_at`` crosses into the past, which is the predicate
    :func:`src.services.rate_limit_service.purge_expired_attempts` reclaims rows by.
    Setting only ``expires_at`` (as the pre-B-1 tests did) changes no verdict,
    because the check never looked at that column.

    A bulk ``UPDATE`` rather than loaded instances, because ``created_at`` is a
    ``server_default`` column: reading it back off the identity map after the
    inserting ``COMMIT`` would lazy-load inside the async context.
    """
    moment = datetime.now(UTC)
    await session.execute(
        update(RateLimitAttempt)
        .where(RateLimitAttempt.key.like(f"{key_prefix}%"))
        .values(
            created_at=moment
            - timedelta(seconds=window_seconds + EXPIRY_GRACE_SECONDS),
            expires_at=moment - timedelta(seconds=EXPIRY_GRACE_SECONDS),
        )
    )
    await session.commit()


class TestLoginLockoutUtility:
    """Unit tests for login lockout utility functions."""

    async def test_check_login_lockout_allows_initial_request(
        self, session: AsyncSession
    ):
        """First login attempt should be allowed."""
        from src.utils.login_lockout import check_login_lockout

        is_allowed = await check_login_lockout("192.168.1.1", session)
        assert is_allowed is True

    async def test_check_login_lockout_allows_4_failures(self, session: AsyncSession):
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

    async def test_reset_login_lockout_clears_all_counters(self, session: AsyncSession):
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

    async def test_different_ips_have_separate_counters(self, session: AsyncSession):
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

    async def test_check_login_lockout_issues_no_write(
        self, session: AsyncSession, async_engine
    ):
        """B-1: the lockout check is a pure read — it writes nothing at all.

        It used to open with an unkeyed ``DELETE … WHERE expires_at < now``, on the
        pre-authentication brute-force path where the attacker sets the request rate,
        while US-32 lets ``answer:*`` rows accumulate between sweeps: the busier the
        answer path made the table, the slower an unauthenticated login got. The
        three tier counts decide everything from ``created_at``, so the delete was
        pure cost. This pins the absence of the write, on both a locked and an
        unlocked IP (the counts differ; the statement mix must not).
        """
        from src.utils.login_lockout import (
            LOCKOUT_THRESHOLD_1MIN,
            check_login_lockout,
            record_login_failure,
        )

        ip = "192.168.1.99"
        for _ in range(LOCKOUT_THRESHOLD_1MIN):  # -> locked on the 1-min tier
            await record_login_failure(ip, session)

        with _write_log(async_engine) as writes:
            assert await check_login_lockout(ip, session) is False
            assert await check_login_lockout("192.168.1.100", session) is True

        assert not [w for w in writes if w.upper().startswith("DELETE")], writes
        assert not [w for w in writes if w.upper().startswith(("INSERT", "UPDATE"))], (
            writes
        )

    async def test_lockout_1min_expires_after_window(
        self, session: AsyncSession, async_engine
    ):
        """1-minute lockout lapses when its window is past; the sweep takes the rows.

        Two separate facts, both pinned here (Wave 2a B-1): the *verdict* comes from
        the ``created_at`` window, and the *rows* are reclaimed only by
        :func:`src.services.rate_limit_service.purge_expired_attempts` — a lockout
        check leaves them physically on the table.
        """
        from src.services.rate_limit_service import purge_expired_attempts
        from src.utils.login_lockout import (
            LOCKOUT_DURATION_1MIN,
            check_login_lockout,
            record_login_failure,
        )

        ip = "192.168.1.1"

        # Record 5 failures -> locked
        for _ in range(5):
            await record_login_failure(ip, session)
        assert await check_login_lockout(ip, session) is False

        await _expire_tier(
            session,
            f"login_lockout_1min:{ip}",
            window_seconds=LOCKOUT_DURATION_1MIN,
        )

        # Window elapsed -> allowed, even though nothing was deleted to make it so
        with _write_log(async_engine) as writes:
            assert await check_login_lockout(ip, session) is True
        assert not [w for w in writes if w.upper().startswith("DELETE")], writes

        # The rows are still there — the check does not clean up behind itself
        assert await _row_count(session, f"login_lockout_1min:{ip}%") == 5

        # The sweep is the reclaimer: exactly the 5 aged rows, shorter tier untouched
        assert await purge_expired_attempts(session) == 5
        assert await _row_count(session, f"login_lockout_1min:{ip}%") == 0
        assert await _row_count(session, f"login_lockout_5min:{ip}%") == 5
        assert await _row_count(session, f"login_lockout_15min:{ip}%") == 5

    async def test_lockout_5min_expires_after_window(
        self, session: AsyncSession, async_engine
    ):
        """5-minute lockout lapses only once the shorter tiers lapse too.

        Ten failures trip every tier at once, so the 5-min tier alone expiring must
        NOT unlock the IP — the 1-min tier still sees 10 rows inside its window. The
        rows of the lapsed tiers stay on the table until the sweep collects them.
        """
        from src.services.rate_limit_service import purge_expired_attempts
        from src.utils.login_lockout import (
            LOCKOUT_DURATION_1MIN,
            LOCKOUT_DURATION_5MIN,
            check_login_lockout,
            record_login_failure,
        )

        ip = "192.168.1.1"

        # Record 10 failures -> locked on the 1-min and 5-min tiers
        for _ in range(10):
            await record_login_failure(ip, session)
        assert await check_login_lockout(ip, session) is False

        # Only the 5-min tier lapses -> still locked, by the 1-min tier (10 >= 5)
        await _expire_tier(
            session,
            f"login_lockout_5min:{ip}",
            window_seconds=LOCKOUT_DURATION_5MIN,
        )
        assert await check_login_lockout(ip, session) is False
        assert await _row_count(session, f"login_lockout_5min:{ip}%") == 10

        # The shorter tier lapses too -> allowed, with nothing deleted to get there
        await _expire_tier(
            session,
            f"login_lockout_1min:{ip}",
            window_seconds=LOCKOUT_DURATION_1MIN,
        )
        with _write_log(async_engine) as writes:
            assert await check_login_lockout(ip, session) is True
        assert not [w for w in writes if w.upper().startswith("DELETE")], writes

        # The sweep collects both lapsed tiers (20 rows), leaves the 15-min tier
        assert await purge_expired_attempts(session) == 20
        assert await _row_count(session, f"login_lockout_1min:{ip}%") == 0
        assert await _row_count(session, f"login_lockout_5min:{ip}%") == 0
        assert await _row_count(session, f"login_lockout_15min:{ip}%") == 10

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

        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
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

    async def test_successful_login_resets_lockout_counter(self, client, async_engine):
        """Successful login should reset the lockout counter."""
        from datetime import UTC, datetime

        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
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

        # 5th failure after reset triggers lockout (returns 401, lockout now active)
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
