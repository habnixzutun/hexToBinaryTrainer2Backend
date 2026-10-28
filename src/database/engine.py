"""The application's database engine and session factory.

US-32 made the pool settings EXPLICIT instead of inheriting SQLAlchemy's
defaults. Reason: ``POST /api/game/answer`` is a two-session request now — the
rate limiter's own short-lived session (Rev 3 R-2, Option A) and the answer
transaction that holds the ``SELECT … FOR UPDATE`` row lock (US-28 AC-1) — while
the auth endpoints keep their single-session pattern. The two are entered per
request but they do NOT hold a connection at the same time: a session checks one
out lazily at its first statement and returns it when its transaction ends, and
the limiter commits before the answer transaction reads anything. One in-flight
answer therefore holds ONE connection, so what has to be sized here is concurrent
*requests* — pinned, with its control case, by
``tests/test_engine_pool_config.py::test_two_answer_sessions_do_not_overlap``.
(Wave 2a review F2: an earlier revision of this docstring claimed both sessions
were checked out simultaneously, without having measured it.) On the old default
(pool 5, overflow 10) a burst of answers could make the *limiter* wait on the pool,
turning a 429 decision into a ``pool_timeout``. The numbers below are pinned by
that same test module, and the sizing rationale lives there too.

The Wave 2a review (S-2) added the liveness pair — ``pool_pre_ping`` and
``pool_recycle`` — because a 30-second ``pool_timeout`` is only worth having if a
checkout returns a usable connection instead of one the server already closed.

``expire_on_commit=False`` is load-bearing, not a preference: ``record_answer``
commits once and then reads the ORM instance to build the ``AnswerOutcome``
(US-28 AC-2), and a project-wide convention already relies on attribute access
surviving a commit.
"""

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from src.config import get_settings

settings = get_settings()
DATABASE_URL: Final[str] = settings.async_database_url

# US-32 — explicit pool sizing (see the module docstring and
# tests/test_engine_pool_config.py). Held connections are bounded by
# pool_size + max_overflow; every answer path peaks at two of them.
DB_POOL_SIZE: Final[int] = 10
DB_MAX_OVERFLOW: Final[int] = 20
DB_POOL_TIMEOUT_SECONDS: Final[int] = 30

# Wave 2a review S-2 — keep a held connection ALIVE rather than letting it age out.
# ``DB_POOL_TIMEOUT_SECONDS = 30`` only pays off if the checkout itself succeeds: an
# idle connection that Postgres (or the firewall in between) has already closed turns
# the next answer into a ``RuntimeError`` on a dead socket, or into a wait for the
# kernel TCP timeout — the exact 30 s the pool timeout was sized to avoid. This is a
# Docker Compose topology, where the DB container can restart under a running API.
#
# ``pool_pre_ping`` sends a liveness probe on checkout, so a dead connection is
# discarded before a request ever sees it. ``pool_recycle`` retires a connection
# BEFORE anything downstream can drop it first (1800 s is well under the usual
# 30-minute idle cut-off, and under ``tcp_keepalives`` defaults), which also bounds
# how long a stale session-level setting can survive a reuse.
DB_POOL_PRE_PING: Final[bool] = True
DB_POOL_RECYCLE_SECONDS: Final[int] = 1800

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT_SECONDS,
    pool_pre_ping=DB_POOL_PRE_PING,
    pool_recycle=DB_POOL_RECYCLE_SECONDS,
)
AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
