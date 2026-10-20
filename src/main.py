from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.database.engine import AsyncSessionLocal
from src.migrations.v001_auth_schema import create_migrations_table, run_migration as run_v001
from src.migrations.v002_game_stats import run_migration as run_v002
from src.routers.auth import router as auth_router
from src.routers.user import router as user_router
from src.routers.game import router as game_router
from src.services.email_wiring import install_email_service


class CacheRequestBodyMiddleware(BaseHTTPMiddleware):
    """Cache request body bytes on request.state.

    Rate-limit key functions can read the body from state._body.
    """

    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        request.state._body = body
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        await create_migrations_table(session)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await run_v001(session)
        await run_v002(session)

    install_email_service(get_settings())

    yield


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CacheRequestBodyMiddleware)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(game_router)


@app.get("/healthz")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}
