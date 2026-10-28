"""Tests for persistent rate limiting (US-04, extended by US-32).

Tests RateLimitService for:
- Rate limit not reached (allowed)
- Rate limit reached (blocked)
- Expired entries cleanup
- Integration with /register endpoint
- Adversarial: multiple IPs, same email
- US-32 / Rev 3 R-2: a cleanup-free counting entry point for the /answer hot
  path, the existing ``check()`` order preserved for its four callers, and the
  startup + periodic sweep that owns the DELETEs now.
"""

import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
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

    async def test_register_rate_limit_429_after_3_attempts(self, client, async_engine):
        """4th registration attempt with same IP+email should return 429."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
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

    async def test_register_rate_limit_different_emails(self, client, async_engine):
        """Rate limit key should be per IP+email combination."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
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


# --------------------------------------------------------------------------- #
# US-32 (Rev 3 R-2, second defect): cleanup off the request path.
#
# ``check()`` ran an unbounded ``DELETE ... WHERE expires_at < now`` on EVERY
# call. On /answer (120 answers/min/user ceiling) that is a full delete scan per
# answer on the hottest endpoint in the project, so the counting entry point the
# answer path uses must not purge — and the four existing callers (auth.py x3,
# security_notification_service.py x1) must keep the purge-first behaviour they
# were verified against, byte for byte.
# --------------------------------------------------------------------------- #


@contextmanager
def _statement_log(engine) -> Iterator[list[str]]:
    """Every SQL statement the engine receives, in order (sync driver level).

    The listener is registered on ``sync_engine`` because the async wrappers
    execute there; aiosqlite runs it on a worker thread, which is why the
    project's coverage config already enables thread concurrency.
    """
    log: list[str] = []

    def _capture(sync_conn, cursor, statement, *args, **kwargs) -> None:
        log.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        yield log
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)


def _kinds(log: list[str]) -> list[str]:
    """The statement verbs only, so the assertions read as an order claim."""
    return [entry.split(" ", 1)[0].upper() for entry in log]


def _is_delete_of_attempt_table(entry: str) -> bool:
    return entry.upper().startswith("DELETE") and "rate_limit_attempt" in entry.lower()


def _is_count_of_attempt_table(entry: str) -> bool:
    return entry.upper().startswith("SELECT") and "count(" in entry.lower()


@contextmanager
def _transaction_log(engine) -> Iterator[list[str]]:
    """Every transaction end the engine is asked for, in order.

    ``before_cursor_execute`` cannot answer "did anything COMMIT?" — a commit is not
    a statement, it is the end of the transaction that carried the statements. S-1 is
    exactly that question: a purge on someone else's session must not end that
    session's transaction, because ending it releases whatever row locks the
    transaction holds (US-28 AC-2). So this listens on the engine's ``commit`` /
    ``rollback`` events instead.
    """
    log: list[str] = []

    def _committed(conn) -> None:
        log.append("COMMIT")

    def _rolled_back(conn) -> None:
        log.append("ROLLBACK")

    event.listen(engine.sync_engine, "commit", _committed)
    event.listen(engine.sync_engine, "rollback", _rolled_back)
    try:
        yield log
    finally:
        event.remove(engine.sync_engine, "commit", _committed)
        event.remove(engine.sync_engine, "rollback", _rolled_back)


class TestCleanupFreeCounting:
    """US-32: ``check_without_cleanup`` counts without the per-call DELETE scan."""

    async def test_check_without_cleanup_issues_no_delete(self, session, async_engine):
        """The answer path's entry point never touches the DELETE statement."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        with _statement_log(async_engine) as log:
            allowed = await service.check_without_cleanup(
                key="answer:user-1", limit=120, window_seconds=60
            )

        assert allowed is True
        assert not [entry for entry in log if _is_delete_of_attempt_table(entry)], log
        assert any(_is_count_of_attempt_table(entry) for entry in log), log

    async def test_check_without_cleanup_still_records_the_attempt(
        self, session, async_engine
    ):
        """Counting is useless without recording: the attempt must be persisted."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        await service.check_without_cleanup(
            key="answer:user-2", limit=120, window_seconds=60
        )

        rows = (
            (
                await session.execute(
                    select(RateLimitAttempt).where(
                        RateLimitAttempt.key == "answer:user-2"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        stored = rows[0].expires_at
        if stored.tzinfo is None:  # SQLite reads DateTime(timezone=True) back naive
            stored = stored.replace(tzinfo=UTC)
        assert stored > datetime.now(UTC)  # window_seconds=60 -> still in the future

    async def test_check_without_cleanup_blocks_at_the_limit(self, session):
        """The ceiling is the same ceiling: 120 in, 121st out."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)
        verdicts = [
            await service.check_without_cleanup(
                key="answer:user-3", limit=3, window_seconds=3600
            )
            for _ in range(4)
        ]

        assert verdicts == [True, True, True, False]

    async def test_check_without_cleanup_leaves_expired_rows_alone(self, session):
        """No purge means an expired row survives until the sweep gets it."""
        from src.services.rate_limit_service import RateLimitService

        expired = RateLimitAttempt(
            id="expired-hot-1",
            key="answer:user-4",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(expired)
        await session.commit()

        await RateLimitService(session).check_without_cleanup(
            key="answer:user-4", limit=3, window_seconds=3600
        )

        kept = (
            await session.execute(
                select(RateLimitAttempt).where(RateLimitAttempt.id == "expired-hot-1")
            )
        ).scalar_one_or_none()
        assert kept is not None  # the periodic sweep owns this row

    async def test_existing_check_still_purges_before_it_counts(
        self, session, async_engine
    ):
        """R-2: the four existing callers keep their verified statement order.

        ``check()`` is what auth.py (x3) and security_notification_service.py (x1)
        call; its DELETE-then-COUNT order is the behaviour US-04 was signed off
        for, so the refactor had to leave it byte-for-byte identical.
        """
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)

        with _statement_log(async_engine) as log:
            allowed = await service.check(
                key="login:ip:x@y.z", limit=3, window_seconds=3600
            )

        assert allowed is True
        delete_at = next(
            (i for i, entry in enumerate(log) if _is_delete_of_attempt_table(entry)),
            None,
        )
        count_at = next(
            (i for i, entry in enumerate(log) if _is_count_of_attempt_table(entry)),
            None,
        )
        assert delete_at is not None, log  # the purge is still on this path
        assert count_at is not None, log
        assert delete_at < count_at, log  # ... and still precedes the count

    async def test_both_entry_points_agree_on_the_verdict(self, session):
        """Same limit, same window, same answer: the cleanup is the only difference."""
        from src.services.rate_limit_service import RateLimitService

        service = RateLimitService(session)
        purge_key, hot_key = "parity:purging", "parity:hot"

        verdicts = []
        for _ in range(4):
            verdicts.append(
                (
                    await service.check(key=purge_key, limit=3, window_seconds=3600),
                    await service.check_without_cleanup(
                        key=hot_key, limit=3, window_seconds=3600
                    ),
                )
            )

        assert verdicts == [(True, True)] * 3 + [(False, False)]


class TestExpiredSweep:
    """US-32: the cleanup now runs at startup and periodically, not per request."""

    async def test_purge_removes_exactly_the_expired_rows(self, session):
        """One sweep: expired rows gone, live rows kept, count returned."""
        from src.services.rate_limit_service import purge_expired_attempts

        now = datetime.now(UTC)
        session.add_all(
            [
                RateLimitAttempt(
                    id="sweep-expired-1", key="k", expires_at=now - timedelta(seconds=1)
                ),
                RateLimitAttempt(
                    id="sweep-expired-2", key="k", expires_at=now - timedelta(hours=2)
                ),
                RateLimitAttempt(
                    id="sweep-live-1", key="k", expires_at=now + timedelta(hours=1)
                ),
            ]
        )
        await session.commit()

        removed = await purge_expired_attempts(session)

        assert removed == 2
        remaining = (await session.execute(select(RateLimitAttempt.id))).scalars().all()
        assert remaining == ["sweep-live-1"]

    async def test_purge_is_a_no_op_on_an_empty_table(self, session):
        """A boot against a fresh database must not raise on nothing to do."""
        from src.services.rate_limit_service import purge_expired_attempts

        assert await purge_expired_attempts(session) == 0

    async def test_purge_does_not_commit_a_dirty_caller_it_has_no_work_for(
        self, session, async_engine
    ):
        """S-1: the no-op purge ends no transaction, because it takes none.

        ``purge_expired_attempts`` is handed someone else's session, and a ``COMMIT``
        ENDS the transaction it commits — which releases that transaction's row locks.
        The caller that must never discover that is US-28's answer transaction
        (AC-2: exactly one ``COMMIT``, only after the streak row was locked); the
        obvious ``execute(delete(...)); commit()`` would hand it back an unlocked
        streak mid-answer, silently, and the economy would then compute on a row
        another session may be writing. So the purge looks first, and the answer to
        the common question ("is anything expired?", every sweep against a table its
        own last pass drained) leaves the connection exactly as it found it: no
        ``DELETE``, no ``COMMIT``, and — thanks to ``no_autoflush`` — not even the
        caller's own pending row on the wire.
        """
        from src.services.rate_limit_service import purge_expired_attempts

        pending = RateLimitAttempt(
            id="dirty-pending-1",
            key="answer:dirty-1",
            expires_at=datetime.now(UTC) - timedelta(seconds=30),
        )
        session.add(pending)  # pending on purpose, never flushed

        with (
            _transaction_log(async_engine) as tx,
            _statement_log(async_engine) as log,
        ):
            removed = await purge_expired_attempts(session)

        assert removed == 0
        assert tx == [], log  # no COMMIT and no ROLLBACK: the tx is the caller's
        assert not [e for e in log if _is_delete_of_attempt_table(e)], log
        assert not [e for e in log if e.upper().startswith("INSERT")], log
        assert any(_is_count_of_attempt_table(e) for e in log), log
        # the read did not push it out, so the purge never saw (and never deleted) it
        assert pending in session.new

    async def test_purge_with_real_work_commits_exactly_once(
        self, session, async_engine
    ):
        """Look-first bounds the COMMIT; it does not remove the delete.

        With an expired row present the purge has to end the transaction it worked in
        — the same statement stream US-04 shipped, and the periodic sweep owns its own
        session so no request transaction's locks are involved. What this pins is that
        it is ONE transaction end, that the ``DELETE`` is really on the wire before it,
        and — the reason the no-op case is the one that must stay silent — that a
        caller's pending row is flushed along with that commit.
        """
        from src.services.rate_limit_service import purge_expired_attempts

        now = datetime.now(UTC)
        session.add(
            RateLimitAttempt(
                id="dirty-expired-1", key="k", expires_at=now - timedelta(seconds=1)
            )
        )
        await session.commit()  # the expired row is real, committed state

        pending = RateLimitAttempt(
            id="dirty-pending-2",
            key="answer:dirty-2",
            expires_at=now + timedelta(hours=1),
        )
        session.add(pending)

        with (
            _transaction_log(async_engine) as tx,
            _statement_log(async_engine) as log,
        ):
            removed = await purge_expired_attempts(session)

        assert removed == 1
        assert tx == ["COMMIT"], (tx, log)
        assert _kinds(log) == ["SELECT", "DELETE", "INSERT"], log
        kept = (await session.execute(select(RateLimitAttempt.id))).scalars().all()
        assert kept == ["dirty-pending-2"], kept

    async def test_run_periodic_cleanup_reports_what_it_removed(self, caplog):
        """A non-empty sweep says so: the background task is invisible otherwise.

        The count is the whole diagnostic — "the table grew by 40 000 rows and the
        sweep reclaimed them" is otherwise indistinguishable from a sweep that found
        nothing, and the sweep no longer runs on any request path (US-32) where a
        slow query or a stuck table could be noticed.
        """
        import logging

        from src.services import rate_limit_service

        async def _busy_purge(session):
            return 7

        @asynccontextmanager
        async def _factory():
            yield "session"

        with (
            patch.object(rate_limit_service, "purge_expired_attempts", _busy_purge),
            caplog.at_level(logging.INFO, logger="src.services.rate_limit_service"),
        ):
            task = asyncio.create_task(
                rate_limit_service.run_periodic_cleanup(_factory, interval_seconds=0.01)
            )
            await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert "removed 7 expired attempt(s)" in caplog.text

    async def test_run_periodic_cleanup_sweeps_once_immediately_then_per_interval(self):
        """Startup sweep + periodic sweep, in one cancellable coroutine."""
        from src.services import rate_limit_service

        opened: list[str] = []
        swept: list = []

        async def _fake_purge(session):
            swept.append(session)
            return 0

        @asynccontextmanager
        async def _factory():
            opened.append("open")
            try:
                yield "session-under-test"
            finally:
                opened.append("close")

        with patch.object(rate_limit_service, "purge_expired_attempts", _fake_purge):
            task = asyncio.create_task(
                rate_limit_service.run_periodic_cleanup(_factory, interval_seconds=0.01)
            )
            await asyncio.sleep(0.005)
            assert swept == ["session-under-test"]  # the startup sweep already ran
            await asyncio.sleep(0.08)
            assert len(swept) >= 3  # and it repeats on the interval
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert opened.count("open") == opened.count("close")  # sessions released
        assert opened.count("open") == len(swept)  # one session per sweep

    async def test_run_periodic_cleanup_survives_a_failing_sweep(self):
        """A broken sweep logs and retries — it must not kill the loop."""
        from src.services import rate_limit_service

        attempts: list[int] = []

        async def _flaky_purge(session):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError("SELECT 1", {}, Exception("db down"))
            return 0

        @asynccontextmanager
        async def _factory():
            yield "session"

        with patch.object(rate_limit_service, "purge_expired_attempts", _flaky_purge):
            task = asyncio.create_task(
                rate_limit_service.run_periodic_cleanup(_factory, interval_seconds=0.01)
            )
            await asyncio.sleep(0.08)
            assert len(attempts) >= 2  # survived the first failure
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_run_periodic_cleanup_handles_cancellation_while_sleeping(self):
        """Shutdown during the sleep: CancelledError propagates, nothing dangles."""
        from src.services import rate_limit_service

        swept: list = []

        async def _fake_purge(session):
            swept.append(session)
            return 0

        @asynccontextmanager
        async def _factory():
            yield "session"

        with patch.object(rate_limit_service, "purge_expired_attempts", _fake_purge):
            task = asyncio.create_task(
                rate_limit_service.run_periodic_cleanup(_factory, interval_seconds=3600)
            )
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert swept == ["session"]
        assert task.cancelled()


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

    async def test_rate_limit_persists_across_different_ips(self, client, async_engine):
        """Rate limit key should include both IP and email.

        Different IPs with same email should have separate limits.
        Since httpx doesn't easily allow changing client IP, we test
        via the service directly with simulated IP+email keys.
        """
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
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
