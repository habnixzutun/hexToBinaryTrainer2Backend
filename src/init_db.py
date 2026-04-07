# src/init_db.py
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine

# Importiere Base und zwingend alle Modelle, damit SQLAlchemy sie für create_all registriert
from src.database.engine import Base
from src.models.user import User
from src.models.ip_address import IpAddress

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:1234@localhost:5432/postgres")


async def init_models():
    engine = create_async_engine(DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        print("Lösche alte Tabellen (falls vorhanden)...")
        # Optional: Drop all, falls du bei jedem Init eine komplett leere DB willst.
        # ACHTUNG: Löscht alle Daten!
        await conn.run_sync(Base.metadata.drop_all)

        print("Erstelle Tabellen...")
        await conn.run_sync(Base.metadata.create_all)

    print("Datenbank erfolgreich initialisiert!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_models())