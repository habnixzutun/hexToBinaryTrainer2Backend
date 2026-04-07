# src/repositories/user_repository.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.models.user import User

async def getUsers(db: AsyncSession):
    # Gibt alle Nutzer zurück, sortiert nach Punkten (absteigend)
    result = await db.execute(select(User).order_by(User.points.desc()))
    return result.scalars().all()

async def updateUserData(db: AsyncSession, name: str, is_correct: bool):
    # Prüfen, ob der User existiert
    result = await db.execute(select(User).where(User.name == name))
    user = result.scalars().first()

    # User anlegen, falls er nicht existiert
    if not user:
        user = User(name=name, correct=0, incorrect=0, points=0)
        db.add(user)

    # Statistiken aktualisieren
    if is_correct:
        user.correct += 1
        user.points += 1  # 1 Punkt für eine richtige Antwort
    else:
        user.incorrect += 1
        # user.points -= 1  # Einkommentieren, falls falsche Antworten Punkte kosten sollen

    await db.commit()
    await db.refresh(user)
    return user