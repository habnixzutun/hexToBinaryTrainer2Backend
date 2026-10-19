from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from starlette.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.database.engine import AsyncSessionLocal
from src.migrations.v001_auth_schema import create_migrations_table, run_migration


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create migrations tracking table
    async with AsyncSessionLocal() as session:
        await create_migrations_table(session)
        await session.commit()

    # Run auth schema migration (only executes once)
    async with AsyncSessionLocal() as session:
        await run_migration(session)

    yield


app = FastAPI(lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def health_check():
    """Health check endpoint for Docker and monitoring.

    Returns:
        dict: Status information including database connectivity
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}


# Note: Legacy endpoints (/api/leaderboard, /api/user, /api/correct, /api/incorrect)
# have been removed as part of the authentication schema migration (US-09).
# New authentication endpoints will be added in US-13, US-14, US-15, US-16, US-19.
