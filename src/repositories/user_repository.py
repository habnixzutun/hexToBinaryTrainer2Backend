# src/repositories/user_repository.py
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.user import User


async def get_users(db: AsyncSession) -> list[User] | Sequence[Any]:
    result = await db.execute(
        select(User).order_by(User.points.desc(), User.incorrect.asc()).limit(10)
    )
    return result.scalars().all()

async def get_user(db: AsyncSession, username: str) -> User:
    result = await db.execute(select(User).where(User.name == username))
    user = result.scalars().first()

    if not user:
        user = User(name=username, correct=0, incorrect=0, points=0)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


async def update_user_data(
    db: AsyncSession, name: str, bits: int, is_correct: bool
) -> User:
    result = await db.execute(select(User).where(User.name == name))
    user = result.scalars().first()

    if not user:
        user = User(name=name, correct=0, incorrect=0, points=0)
        db.add(user)

    if is_correct:
        user.correct += 1
        user.points += bits
    else:
        user.incorrect += 1
        user.points -= bits * 4

    await db.commit()
    await db.refresh(user)
    return user
