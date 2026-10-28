"""Game router: score tracking and leaderboard.

US-33 adds ``POST /api/game/answer`` — the Sprint-4 economy endpoint — alongside the
legacy ``/correct`` / ``/incorrect`` / ``/leaderboard`` handlers, which stay on
``user_stats`` until US-34 removes them (D-6). The new handler deliberately holds
nothing but the **gate order** and its HTTP mappings:

    authenticate (401) -> validate body (422) -> rate-limit (429)
        -> unlock gate (403) -> transact (200 / 404)

Every number in the response comes from a service module
(:mod:`src.services.answer_service`, :mod:`src.services.game_service`), because
arithmetic in a router is arithmetic no other caller can reuse and no unit test can
reach without HTTP.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import AsyncSessionLocal, get_db
from src.models.auth import User
from src.models.game import UserStats
from src.models.game_schemas import AnswerRequest, AnswerResponse
from src.routers.user import _extract_bearer_user_id
from src.services.answer_service import (
    answer_response,
    economy_seam,
    rate_limit_key,
    unlock_gate_detail,
    user_exists,
)
from src.services.game_config import (
    ANSWER_RATE_LIMIT,
    ANSWER_RATE_WINDOW_SECONDS,
)
from src.services.game_service import pre_answer_level, record_answer
from src.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/api/game", tags=["game"])


class GameActionRequest(BaseModel):
    """Request body for correct/incorrect answer submission."""

    bits: int


class GameSubmitResponse(BaseModel):
    """Response after recording a game action."""

    message: str
    user_id: str
    correct_count: int
    incorrect_count: int
    points: int


class LeaderboardEntry(BaseModel):
    """Single leaderboard entry."""

    user_id: str
    username: str
    correct_count: int
    incorrect_count: int
    points: int


class LeaderboardResponse(BaseModel):
    """Wrapped leaderboard response."""

    entries: list[LeaderboardEntry]


@router.post(
    "/correct", status_code=status.HTTP_200_OK, response_model=GameSubmitResponse
)
async def record_correct_answer(  # pragma: no cover — legacy, gone in US-34 (D-6)
    request: Request,
    body: GameActionRequest,
    session: AsyncSession = Depends(get_db),
) -> GameSubmitResponse:
    """Record a correct answer and update user stats."""
    user_id = _extract_bearer_user_id(request)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    stats_result = await session.execute(
        select(UserStats).where(UserStats.user_id == user_id)
    )
    stats = stats_result.scalar_one_or_none()

    if stats is None:
        stats = UserStats(
            user_id=user_id,
            correct_count=1,
            incorrect_count=0,
            points=body.bits,
        )
        session.add(stats)
    else:
        stats.correct_count += 1
        stats.points += body.bits

    await session.commit()
    await session.refresh(stats)

    return GameSubmitResponse(
        message="Correct answer recorded",
        user_id=user_id,
        correct_count=stats.correct_count,
        incorrect_count=stats.incorrect_count,
        points=stats.points,
    )


@router.post(
    "/incorrect", status_code=status.HTTP_200_OK, response_model=GameSubmitResponse
)
async def record_incorrect_answer(  # pragma: no cover — legacy, gone in US-34 (D-6)
    request: Request,
    body: GameActionRequest,
    session: AsyncSession = Depends(get_db),
) -> GameSubmitResponse:
    """Record an incorrect answer and update user stats."""
    user_id = _extract_bearer_user_id(request)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    stats_result = await session.execute(
        select(UserStats).where(UserStats.user_id == user_id)
    )
    stats = stats_result.scalar_one_or_none()

    if stats is None:
        stats = UserStats(
            user_id=user_id,
            correct_count=0,
            incorrect_count=1,
            points=-(body.bits * 4),
        )
        session.add(stats)
    else:
        stats.incorrect_count += 1
        stats.points -= body.bits * 4

    await session.commit()
    await session.refresh(stats)

    return GameSubmitResponse(
        message="Incorrect answer recorded",
        user_id=user_id,
        correct_count=stats.correct_count,
        incorrect_count=stats.incorrect_count,
        points=stats.points,
    )


@router.get(
    "/leaderboard",
    status_code=status.HTTP_200_OK,
    response_model=LeaderboardResponse,
)
async def get_leaderboard(  # pragma: no cover — legacy, gone in US-34 (D-6)
    session: AsyncSession = Depends(get_db),
    limit: int = 50,
) -> LeaderboardResponse:
    """Get top players leaderboard sorted by points descending."""
    result = await session.execute(
        select(UserStats, User.username)
        .join(User, UserStats.user_id == User.id)
        .order_by(desc(UserStats.points))
        .limit(limit)
    )

    entries = [
        LeaderboardEntry(
            user_id=stats.user_id,
            username=username,
            correct_count=stats.correct_count,
            incorrect_count=stats.incorrect_count,
            points=stats.points,
        )
        for stats, username in result.all()
    ]

    return LeaderboardResponse(entries=entries)


# --------------------------------------------------------------------------- #
# US-33: POST /api/game/answer
# --------------------------------------------------------------------------- #

# The body cap of US-32 (``src/middleware/body_size.py``) is keyed to this exact
# path; ``tests/test_answer_endpoint.py`` asserts the two literals still agree,
# because a drift would silently leave the endpoint uncapped.
ANSWER_PATH = "/answer"


async def require_answer_identity(request: Request) -> str:
    """Gate step 1 — the caller, from the bearer token and nowhere else (401).

    An ``async def`` wrapper around ``user.py``'s parser so FastAPI keeps the work
    on the event loop instead of handing a header lookup to a threadpool. Nothing
    here touches the database: ``_extract_bearer_user_id`` raises its 401 before
    any session dependency is entered, which is what makes "no valid auth, no SQL"
    (N17/N18) a property of the boundary rather than of the handler's discipline.

    Returns:
        The ``sub`` claim — the only user identity this endpoint ever accepts (N8).
    """
    return _extract_bearer_user_id(request)


async def answer_rate_limit_session():
    """The rate limiter's OWN short-lived session (Rev 3 R-2, Option A).

    Not ``get_db``: ``RateLimitService`` commits, and a commit on the *answer*
    session would release the ``FOR UPDATE`` row lock mid-transaction (US-28
    AC-2/AC-1). Two sessions, two transactions, one request — but NOT two concurrent
    checkouts (Wave 2a review F2, which flagged the claim in the previous revision of
    this docstring as unmeasured): this session ends its transaction at its commit,
    and a SQLAlchemy session returns its connection to the pool at that point, while
    the answer transaction's first statement — the ``FOR UPDATE`` read — comes after
    it. So one answer in flight holds one pooled connection, which is what
    ``src/database/engine.py`` sizes for. Holding both at once is what an uncommitted
    limiter session would do, and it is the failure mode the pool probes against
    ``pool_size=1`` in the engine tests.
    """
    async with AsyncSessionLocal() as session:
        yield session


@router.post(
    ANSWER_PATH,
    status_code=status.HTTP_200_OK,
    response_model=AnswerResponse,
    summary="Record one answered question (Sprint 4 economy)",
)
async def submit_answer(
    body: AnswerRequest,
    user_id: str = Depends(require_answer_identity),
    rate_limit_session: AsyncSession = Depends(answer_rate_limit_session),
    session: AsyncSession = Depends(get_db),
) -> AnswerResponse:
    """Score one answer and return the caller's new economy state.

    The gates run in a fixed order, and each one is cheaper than the next:

    1. **Authenticate** (``require_answer_identity``) — 401 with zero SQL, before a
       session exists.
    2. **Validate** (``AnswerRequest``, US-32) — 422 from pydantic: per-mode width
       whitelist (D-7/OQ-7), strict ``is_correct``/``bit_width`` typing (D-8), no
       extra fields, so a client-computed ``total_xp`` or a body ``user_id`` cannot
       be smuggled in (N8). Measured order (Wave 2a review, MEDIUM): FastAPI reads
       the body and ENTERS this endpoint's dependencies before it validates, so a 422
       is raised before the handler body runs with the identity dependency already
       returned and **both session dependencies entered but inert** — no SQL, no pool
       checkout, no rate-limit row, because a session checks its connection out lazily
       at its first statement. That is the whole difference from step 1, where a 401
       short-circuits before the session dependencies are reached at all.
    3. **Rate-limit** (429) — on its own session, ahead of any row lock, so a
       throttled caller cannot hold another caller's transaction waiting. The
       counting entry point is ``check_without_cleanup`` (US-32): no ``DELETE`` runs
       on this path, and a refusal writes no partial state (the limiter commits only
       on the allowed branch, and the profile is never touched either way).
    4. **Unlock gate** (403) — inside the answer transaction, on the ``FOR UPDATE``
       locked **pre-answer** level (finding F-2), so an answer worth enough XP to
       reach the level it asks about is still refused. The locked read is
       ``game_service.pre_answer_level``, never routed through the seam.
    5. **Transact** (200) — ``record_answer`` with the composed ``economy_seam``,
       one commit at the very end (US-28 AC-2).

    Raises:
        HTTPException: 401 (no/bad/expired token), 422 (invalid body, FastAPI's own
            handling of ``AnswerRequest``), 429 (over budget, with ``Retry-After``),
            403 (combo locked at the pre-answer level), 404 (the token's user is
            gone — checked after the limiter and before the lock, so the lazy profile
            insert cannot trip the foreign key).
    """
    # (3) rate limiting — its own session, its own commit, nothing else's state.
    allowed = await RateLimitService(rate_limit_session).check_without_cleanup(
        rate_limit_key(user_id),
        ANSWER_RATE_LIMIT,
        ANSWER_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many answers for this user.",
            headers={"Retry-After": str(ANSWER_RATE_WINDOW_SECONDS)},
        )

    # (4a) the identity has to still exist before anything can be scored for it.
    # Checked here — after the limiter, before the lock — because the profile row is
    # created lazily by the first answer (US-28 AC-3) and the foreign key would
    # answer a deleted user with a 500.
    if not await user_exists(session, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # (4b) the unlock gate, on the locked PRE-answer level (finding F-2). This read
    # and the transaction below share `session`, so the row lock taken here is the
    # one the answer runs under — a 403 therefore rolls back a transaction that
    # never wrote anything.
    pre_level = await pre_answer_level(session, user_id)
    gate_detail = unlock_gate_detail(body.mode, body.bit_width, pre_level)
    if gate_detail is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=gate_detail)

    # (5) the answer itself: streak lifecycle plus the composed economy seam.
    outcome = await record_answer(
        session,
        user_id,
        is_correct=body.is_correct,
        economy=economy_seam(body.mode, body.bit_width),
    )
    return answer_response(outcome)
