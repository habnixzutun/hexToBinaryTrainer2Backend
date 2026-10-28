"""US-04: Persistent rate limiting service (ADR 001: DB instead of Redis).

Provides database-backed rate limiting to replace in-memory rate limiting.
Tracks attempts by key with automatic cleanup of expired entries.

US-32 (Rev 3 R-2, the second defect in the same service) adds two things and
deliberately changes nothing the four existing callers — ``src/routers/auth.py``
(login, register, resend-verification) and
``src/services/security_notification_service.py`` — rely on:

* :meth:`RateLimitService.check_without_cleanup`, the counting entry point for
  the ``POST /api/game/answer`` hot path. :meth:`check` deleted every expired row
  on **every** call, so at US-32's 120-answers-per-minute ceiling it was a full
  delete scan per answer on the hottest endpoint in the project. The answer path
  needs the ``COUNT`` (and the ``INSERT``) and nothing else, so it gets a
  purge-free variant, and the ``DELETE`` moved to :func:`purge_expired_attempts`,
  which ``src/main.py``'s lifespan runs once at startup and then periodically via
  :func:`run_periodic_cleanup`.
* The Wave 2a review (B-1) took the same scan off the **brute-force** path:
  ``src/utils/login_lockout.py`` dropped its own unkeyed ``DELETE … WHERE
  expires_at < now``, which ran on every ``POST /auth/login`` attempt — an endpoint
  whose request rate is the attacker's — while US-32 lets ``answer:*`` rows
  accumulate between sweeps. That pairing was cross-endpoint amplification: the
  busier the answer path made the table, the slower the login endpoint's scan got.
  The lockout's three tier counts decide expiry from ``created_at`` and never needed
  the delete, so removing it changes no verdict (``tests/test_login_lockout.py``
  pins that a lockout check issues no ``DELETE`` at all).
  The four :meth:`check` callers themselves **stay on** :meth:`check` (R-2 pins their
  statement order byte for byte; moving them to :meth:`check_without_cleanup` is the
  same verdict but a US-34 decision, not a review fix). So one unkeyed delete per
  pre-auth auth call remains, and what bounds it is now the sweep interval: a row
  lives at most ``window + RATE_LIMIT_SWEEP_INTERVAL_SECONDS``, so the scan reads one
  window plus one interval rather than an unbounded table. That is the sizing
  ``RATE_LIMIT_SWEEP_INTERVAL_SECONDS`` argues for, and the reason it was cut from 300
  to 60 (S-6).
* :meth:`check` itself keeps the statement order it was signed off for **byte for
  byte**: purge, then count, then record-and-commit — and still no ``COMMIT`` on
  the blocked path (an over-limit caller must not pay for a write).

:func:`run_periodic_cleanup` takes its session factory as an argument instead of
importing ``AsyncSessionLocal``, so this module stays free of the database layer
(it receives a session everywhere else too) and the loop is drivable from a test
without a database. The rate limiter on the answer path likewise runs on its own
short-lived session (Rev 3 R-2, Option A) — its commit can never release the
``SELECT … FOR UPDATE`` lock of the answer transaction (US-28 AC-1/AC-2).
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Delete, delete, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import RateLimitAttempt

logger = logging.getLogger(__name__)

__all__ = [
    "RATE_LIMIT_SWEEP_INTERVAL_SECONDS",
    "RateLimitService",
    "purge_expired_attempts",
    "run_periodic_cleanup",
]

# US-32, tightened by the Wave 2a review (S-6) — how often the background sweep
# deletes expired rows. After B-1 this sweep is the ONLY reclaimer for the whole
# table: no request path deletes expired rows any more, so this interval is the
# bound on how long garbage may sit there, and it has to be argued arithmetically
# rather than by feel. A row lives at most ``window + interval`` (its own window
# before it is expired, then the wait for the next sweep), so the peak table size
# is ``arrival_rate x (window + interval)``:
#
#   answer rows   120/min/user x (60 s + 60 s)  =  240 rows per farming user
#   auth rows     3/h/IP+email x (3600 s + 60 s) =  1 row per abusive identity
#
# At 300 s the answer term was 1200 rows per user — five times the row's own
# lifetime, and the factor an operator would have to size ``rate_limit_attempt``
# for. Matching the interval to the SHORTER row lifetime (60 s for the answer key,
# the highest-volume one) puts the worst case at two lifetimes. The residual is
# deliberate and tracked: the table still grows linearly with concurrent farmers,
# the sweep is a seq scan (``ix_rate_limit_attempt_key_expires`` leads with ``key``
# and cannot serve an unkeyed ``expires_at`` filter), and the cure is the index in
# v005 — deferred past this sprint by D-6 ("no v005 mid-sprint"), same bucket as
# the answer-history event table. A sweep scan is background work off every request
# path, which is exactly what Rev 3 R-2 bought by moving the DELETE out of them.
RATE_LIMIT_SWEEP_INTERVAL_SECONDS: Final[int] = 60


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
"""Anything that hands out an ``AsyncSession`` as an async context manager.

``src.database.engine.AsyncSessionLocal`` is the production member; a test fake is
the other one, which is why :func:`run_periodic_cleanup` takes it as an argument.
"""


def _delete_expired(sync_conn: Connection, stmt: Delete) -> None:
    """Execute the purge DELETE on the raw DBAPI-facing ``Connection``.

    The callback form is what :meth:`AsyncConnection.run_sync` needs: it hands the
    sync ``Connection`` to a **plain ``def``**, which is the only place a sync
    statement is legal without leaving the event loop. Executing there rather than
    through ``AsyncSession.execute`` is the point: the session's flush machinery is
    never consulted, so no pending ORM object of the caller's can ride along with
    the purge. The row count is not read here — the caller already measured it (see
    :func:`purge_expired_attempts`) and reports that figure, which keeps one number
    in the function instead of two that could disagree.
    """
    sync_conn.execute(stmt)


async def purge_expired_attempts(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete every expired rate-limit row and commit. Returns the row count.

    The cleanup :meth:`RateLimitService.check` used to run inline on every single
    request; it runs here, on the maintenance path (the periodic sweep plus the
    three pre-auth ``check()`` callers and the one security-notification caller).

    Wave 2a review S-1 — **the purge never commits when it has nothing to purge, and
    it never touches the ORM's write path.** The obvious shape,
    ``await session.execute(delete(...))`` followed by ``await session.commit()``,
    is unsafe for a function that hands back someone else's session, because
    :meth:`AsyncSession.commit` autoflushes that caller's pending state first and
    then emits a ``COMMIT`` on the shared connection. A ``COMMIT`` ends the
    transaction, and ending the transaction releases its row locks — so a caller
    inside US-28's ``SELECT … FOR UPDATE`` would lose the lock (AC-2) in the middle
    of an answer, and the lost lock is silent: the economy would keep computing on
    a streak another transaction may be writing at the same moment. Flushing before
    the commit does not fix this (it only orders the statements); removing the
    commit does, so the purge is now structured as look → delete → commit:

    * **look** — ``SELECT count(*) … WHERE expires_at < now`` inside
      ``session.no_autoflush``, so even the read cannot push the caller's pending
      objects to the database. Nothing expired means nothing to commit: the function
      returns ``0`` and issues no ``DELETE``, no ``FLUSH`` and no ``COMMIT``. The
      common case is the empty one — the sweep runs every
      ``RATE_LIMIT_SWEEP_INTERVAL_SECONDS`` against a table its own last pass
      drained — which is why this path is worth a statement of its own.
    * **delete** — :func:`_delete_expired` on the session's own ``Connection``
      (same transaction, so the caller's rollback still takes the purge back,
      exactly as before), never through the session.
    * **commit** — only after a real delete, which is the only case where ending the
      transaction is this function's business at all.

    Args:
        session: session to run the delete on. Caller-owned: its transaction
            encloses the purge, and this function commits that transaction when and
            only when it deleted something.
        now: override the clock; production callers leave it ``None``, tests pass
            a fixed instant so "expired" is not a race against wall time.

    Returns:
        How many rows the purge removed — the ``count`` measured immediately before
        the delete, on the same transaction, so the two can only differ by a row
        another session committed in that same instant. It feeds a log line and the
        sweep's own "did anything happen" decision, never a security-relevant
        verdict, and one number is better than a count plus a ``rowcount`` that
        might disagree. ``0`` when the table holds nothing expired, and also ``0``
        when the delete itself failed — that case is logged, not raised: a statement
        error aborts the whole transaction on Postgres, so raising would only trade
        a 500 for the same 500 while killing the sweep loop. The teardown of the
        caller's session rolls the poisoned transaction back; this function
        deliberately does not roll back on its own, because the caller's pending
        writes are not its to discard.
    """
    moment = now if now is not None else datetime.now(UTC)
    expired = RateLimitAttempt.expires_at < moment

    with session.no_autoflush:
        # S-1, step 1: look before touching anything. Nothing expired -> the
        # connection is left exactly as it was found, COMMIT included.
        remaining = (
            await session.scalar(
                select(func.count()).select_from(RateLimitAttempt).where(expired)
            )
            or 0
        )
        if not remaining:
            return 0
        # S-1, step 2: the delete goes to the Connection, not the Session, so no
        # autoflush can order anyone else's writes around it.
        try:
            sync_conn = await session.connection()
            await sync_conn.run_sync(
                _delete_expired, delete(RateLimitAttempt).where(expired)
            )
        except SQLAlchemyError:
            logger.warning(
                "rate-limit purge failed; leaving it to the next sweep", exc_info=True
            )
            return 0

    await session.commit()
    return remaining


async def run_periodic_cleanup(
    session_factory: SessionFactory,
    *,
    interval_seconds: float = RATE_LIMIT_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Sweep expired rows now, then every ``interval_seconds``, until cancelled.

    The first sweep happens before the first ``await asyncio.sleep``, which is what
    makes this one coroutine both the startup sweep and the periodic one — and why
    ``src/main.py`` only has to schedule it: a request path never waits on a
    cleanup, and a boot against an empty table costs one ``count`` and nothing else
    (S-1: no expired row, no ``DELETE``, no ``COMMIT``).

    ``interval_seconds`` is a ``float`` rather than an ``int`` only because the test
    that drives the loop passes ``0.01`` to keep it under a second (F10); production
    passes nobody anything and takes ``RATE_LIMIT_SWEEP_INTERVAL_SECONDS``.

    A failed sweep is logged and retried on the next interval rather than allowed
    to kill the loop: the sweep is maintenance, and losing it would quietly turn
    ``rate_limit_attempt`` into an unbounded table. That is why the clause catches
    ``Exception`` and not only the two database types a sweep normally meets — a
    session factory that is not a database at all is a legitimate configuration for
    this loop (the DB-free lifespan tests inject a fake with a single ``commit()``
    method, and a missing attribute there is an ``AttributeError``, not a
    ``SQLAlchemyError``), and letting it end the task would trade a log line for an
    unbounded table. ``CancelledError`` is a ``BaseException``, so it is
    deliberately **not** caught — shutdown must be able to stop this task.
    """
    while True:
        try:
            async with session_factory() as session:
                removed = await purge_expired_attempts(session)
            if removed:
                logger.info("rate-limit sweep removed %d expired attempt(s)", removed)
        except Exception as exc:  # noqa: BLE001 - see the docstring: never fatal
            logger.warning(
                "rate-limit sweep failed (%r); retrying in %d s",
                exc,
                interval_seconds,
            )
        await asyncio.sleep(interval_seconds)


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
        return await self._check(
            key, limit, window_seconds, purge_expired=True
        )  # US-04 behaviour, unchanged (R-2)

    async def check_without_cleanup(
        self, key: str, limit: int, window_seconds: int
    ) -> bool:
        """Count and record WITHOUT the per-call delete of expired rows (US-32).

        The entry point for a hot path — today ``POST /api/game/answer`` at up to
        120 calls per minute per user, where :meth:`check`'s unbounded
        ``DELETE … WHERE expires_at < now`` would be one full scan per answer. The
        rows it leaves behind are exactly what :func:`purge_expired_attempts`
        collects on startup and on the periodic sweep.

        Args:
            key: Rate limit key (e.g. ``answer:<user_id>``).
            limit: Maximum number of attempts allowed in window.
            window_seconds: Time window in seconds.

        Returns:
            True if request is allowed (under limit), False if blocked. Same
            verdict as :meth:`check`; only the cleanup differs.
        """
        return await self._check(
            key, limit, window_seconds, purge_expired=False
        )  # US-32: no DELETE on the request path

    async def _check(
        self, key: str, limit: int, window_seconds: int, *, purge_expired: bool
    ) -> bool:
        """The shared count-and-record body behind both entry points.

        ``purge_expired`` is the ONLY difference between the two public methods,
        and it is applied as the first statement, exactly where :meth:`check` has
        always applied it — so the existing callers' statement stream is
        unchanged (asserted by ``tests/test_rate_limit.py``).
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=window_seconds)

        if purge_expired:
            # Cleanup expired entries first (US-04); the hot path skips this
            # statement entirely and leaves it to the periodic sweep (US-32).
            await self.session.execute(
                delete(RateLimitAttempt).where(RateLimitAttempt.expires_at < now)
            )

        # Count recent attempts for this key within the window
        result = await self.session.execute(
            select(func.count()).where(
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
