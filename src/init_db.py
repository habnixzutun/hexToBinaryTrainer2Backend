# src/init_db.py
import asyncio
import os

import asyncpg
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

# Importiere Base und zwingend alle Modelle, damit SQLAlchemy sie für create_all registriert
from src.database.engine import Base
from src.models.user import User

load_dotenv()
DB_NAME = os.getenv("DB_NAME")
DB_PW = os.getenv("DB_PW")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")

DATABASE_URL = os.getenv("DATABASE_URL", f"postgresql+asyncpg://{DB_USER}:{DB_PW}@{DB_HOST}:5432/{DB_NAME}")
ASYNC_PG_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

async def wait_for_db():
    while True:
        try:
            conn = await asyncpg.connect(ASYNC_PG_URL)
            await conn.close()
            break
        except Exception:
            print("Warte auf DB...")
            await asyncio.sleep(2)


async def init_models():
    await wait_for_db()
    engine = create_async_engine(DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        print("Lösche alte Tabellen (falls vorhanden)...")
        # Optional: Drop all, falls du bei jedem Init eine komplett leere DB willst.
        # ACHTUNG: Löscht alle Daten!
        # await conn.run_sync(Base.metadata.drop_all)

        print("Erstelle Tabellen...")
        await conn.run_sync(Base.metadata.create_all)

    print("Datenbank erfolgreich initialisiert!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_models())