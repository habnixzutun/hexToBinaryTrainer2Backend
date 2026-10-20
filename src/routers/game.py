"""Game router: score tracking and leaderboard."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import get_db
from src.models.auth import User
from src.models.game import UserStats
from src.routers.user import _extract_bearer_user_id

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
async def record_correct_answer(
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
async def record_incorrect_answer(
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
async def get_leaderboard(
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
