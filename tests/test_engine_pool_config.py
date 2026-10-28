"""US-32: the app engine's pool is configured explicitly, not by accident.

Before US-32 ``src/database/engine.py`` called ``create_async_engine(URL,
echo=False)`` and inherited SQLAlchemy's defaults (pool 5, overflow 10, timeout
30 s). Sprint 4 puts a second SQL consumer on that engine: the
``POST /api/game/answer`` path opens TWO sessions per request — the rate limiter's
own short-lived one (Rev 3 R-2, Option A) and the answer transaction that holds the
``SELECT … FOR UPDATE`` row lock (US-28 AC-1).

Those two are SEQUENTIAL, not simultaneous. The previous revision of this docstring
claimed both were "held at the same time", which the Wave 2a review (F2) rightly
called out as unmeasured prose: a SQLAlchemy session returns its connection to the
pool when its transaction ENDS, the limiter commits before the answer transaction
issues its first statement, so one in-flight answer holds ONE pooled connection.
``test_two_answer_sessions_do_not_overlap`` below pins that against a
``pool_size=1`` engine instead of leaving it to wording. What the
sizing has to cover is therefore concurrent *requests* (plus the auth and
maintenance paths), and under the old pool of 5 a burst of answers makes whichever
session checks out last wait on the pool instead of answering — the ``pool_timeout``
that ``DB_POOL_TIMEOUT_SECONDS`` exists to bound.

So the numbers are pinned here as an explicit contract, and the ``FOR UPDATE``
hold time is bounded by the pool the answer path can actually get. The liveness pair
(``pool_pre_ping``, ``pool_recycle``) is pinned at the bottom with the reason the
review asked for (S-2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import engine as engine_module
from src.database.engine import (
    DB_MAX_OVERFLOW,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE_SECONDS,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_SECONDS,
    AsyncSessionLocal,
    engine,
)


def test_pool_constants_are_explicit_and_sane():
    """The three knobs exist as named module constants (no magic numbers inline)."""
    assert isinstance(DB_POOL_SIZE, int) and DB_POOL_SIZE > 0
    assert isinstance(DB_MAX_OVERFLOW, int) and DB_MAX_OVERFLOW >= 0
    assert isinstance(DB_POOL_TIMEOUT_SECONDS, int) and DB_POOL_TIMEOUT_SECONDS > 0
    # Held connections are bounded by requests, not by sessions-per-request (F2):
    # an answer holds one at a time. The pool still has to cover a dozen concurrent
    # answers plus the auth and maintenance paths, or the sizing claim in the module
    # docstring is hollow.
    assert DB_POOL_SIZE + DB_MAX_OVERFLOW >= 2 * 10


def test_engine_declares_the_pool_size():
    """``pool_size`` reaches the real pool, not just the call site."""
    assert engine.pool.size() == DB_POOL_SIZE


def test_engine_declares_the_max_overflow():
    """``max_overflow`` reaches the real pool."""
    # ``_max_overflow`` is the attribute SQLAlchemy stores it on; there is no
    # public getter, and asserting the private name is the only way to prove the
    # engine was constructed with it rather than with the default of 10.
    assert engine.pool._max_overflow == DB_MAX_OVERFLOW


def test_engine_declares_the_pool_timeout():
    """``pool_timeout`` reaches the real pool, as seconds."""
    assert engine.pool.timeout() == float(DB_POOL_TIMEOUT_SECONDS)


def test_session_factory_keeps_expire_on_commit_off():
    """``AnswerOutcome`` reads ORM attributes AFTER the commit (US-28 AC-2).

    ``record_answer`` commits once and then builds the outcome from the very same
    instance. With the SQLAlchemy default (``expire_on_commit=True``) every one of
    those reads would fire a fresh SELECT after the row lock is already gone —
    slower, and not the state the transaction decided on. The flag is therefore
    part of what this module promises its callers.
    """
    assert AsyncSessionLocal.kw.get("expire_on_commit") is False


def test_engine_url_is_still_the_settings_url():
    """The pool knobs did not move the engine off the configured database."""
    from src.config import get_settings

    settings = get_settings()
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == settings.db_name
    assert engine.url.host == settings.db_host
    assert engine.url.port == settings.db_port
    assert engine.url.username == settings.db_user


def test_pool_kwargs_are_not_passed_to_a_dialect_without_a_queue_pool():
    """Guard against a future SQLite-in-engine mistake (StaticPool has no pool_size).

    ``create_async_engine`` rejects ``pool_size`` for dialects that do not use a
    QueuePool, so if the module-level engine ever stopped being the Postgres one
    from settings, this is the test that explains the boot failure.
    """
    from sqlalchemy.exc import ArgumentError

    assert engine.url.get_backend_name() == "postgresql"
    with pytest.raises((ArgumentError, TypeError)):
        engine_module.create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_timeout=DB_POOL_TIMEOUT_SECONDS,
        )


# --------------------------------------------------------------------------- #
# S-2 (Wave 2a review): the liveness pair. ``pool_timeout=30`` is only worth having
# if a checkout returns a USABLE connection: an idle socket that Postgres — or the
# container boundary in this Docker Compose topology — has already dropped turns the
# next answer into an error on a dead connection, or into a wait for the kernel TCP
# timeout, which is the same 30 s the pool timeout was sized to avoid. ``pre_ping``
# catches the dead connection at checkout, ``recycle`` retires it first.
# --------------------------------------------------------------------------- #


def test_engine_pings_a_connection_before_handing_it_out():
    """``pool_pre_ping`` reaches the real pool, not just the call site."""
    assert DB_POOL_PRE_PING is True
    assert engine.pool._pre_ping is DB_POOL_PRE_PING


def test_engine_recycles_connections_before_anyone_else_closes_them():
    """``pool_recycle`` reaches the real pool, and is under the usual idle cut-off."""
    assert DB_POOL_RECYCLE_SECONDS > 0
    # At or under the conventional idle cut-off (Postgres ``idle_session_timeout``,
    # most firewalls and load balancers: 30 minutes). The age comparison happens at
    # checkout, so a connection that has sat past the boundary is retired before a
    # request ever sees it; ``pool_pre_ping`` covers the racing remainder.
    assert DB_POOL_RECYCLE_SECONDS <= 30 * 60
    assert engine.pool._recycle == DB_POOL_RECYCLE_SECONDS


async def test_two_answer_sessions_do_not_overlap(tmp_path: Path):
    """F2, measured instead of asserted in prose.

    The answer path's two sessions are the reason ``pool_size`` was doubled, so the
    docstrings have to say whether one request holds one connection or two. This is
    the experiment that settles it, run against ``pool_size=1, max_overflow=0``:

    * the limiter's pattern — a statement, then a **commit** — leaves the pool, so the
      second session checks out and the answer transaction proceeds;
    * the uncommitted pattern (a statement and no commit) holds it, and the next
      checkout times out.

    The second half is the control: without it, the first half would pass just as
    happily on a pool big enough to hide the difference, and the claim "sequential,
    not simultaneous" would be untested again.
    """
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    db_path = tmp_path / "pool-probe.sqlite"
    probe_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    factory = async_sessionmaker(
        probe_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with probe_engine.begin() as conn:
            await conn.execute(text("CREATE TABLE probe (n int)"))
            await conn.execute(text("INSERT INTO probe VALUES (1)"))

        # The real sequence: limiter reads and commits, then the answer transaction.
        async with factory() as limiter, factory() as answer:
            await limiter.execute(select(text("n")).select_from(text("probe")))
            await limiter.commit()
            await answer.execute(select(text("n")).select_from(text("probe")))

        # Control: a session that never ends its transaction holds the only slot.
        async with factory() as held, factory() as blocked:
            await held.execute(select(text("n")).select_from(text("probe")))
            with pytest.raises(PoolTimeoutError):
                await blocked.execute(select(text("n")).select_from(text("probe")))
    finally:
        await probe_engine.dispose()
