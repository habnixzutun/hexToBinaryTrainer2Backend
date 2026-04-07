# src/repositories/user_repository.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.models.user import User
from src.pydantic_types import AnswerRequest


async def get_users(db: AsyncSession):
    result = await db.execute(select(User).order_by(User.points.desc(), User.incorrect.asc()).limit(10))
    return result.scalars().all()

async def update_user_data(db: AsyncSession, data: AnswerRequest, is_correct: bool):
    result = await db.execute(select(User).where(User.name == data.name))
    user = result.scalars().first()

    if not user:
        user = User(name=data.name, correct=0, incorrect=0, points=0)
        db.add(user)

    if is_correct:
        user.correct += 1
        user.points += user.correct
    else:
        user.incorrect += 1
        user.points -= user.incorrect * 4

    await db.commit()
    await db.refresh(user)
    return user