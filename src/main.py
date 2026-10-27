from contextlib import asynccontextmanager
from datetime import UTC, datetime

import jwt
from fastapi import FastAPI, Response
from sqlalchemy import select, text
from starlette.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.database.engine import AsyncSessionLocal
from src.migrations.v001_auth_schema import create_migrations_table
from src.migrations.v001_auth_schema import run_migration as run_v001
from src.migrations.v002_game_stats import run_migration as run_v002
from src.migrations.v003_rate_limit import run_migration as run_v003
from src.models.auth import UserIdentity
from src.routers.auth import router as auth_router
from src.routers.game import router as game_router
from src.routers.user import router as user_router
from src.services.audit_service import log_audit_action
from src.services.email_wiring import install_email_service
from src.utils.jwt_utils import decode_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        await create_migrations_table(session)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await run_v001(session)
        await run_v002(session)
        await run_v003(session)

    install_email_service(get_settings())

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


_PAGE_STYLE = "font-family: sans-serif; text-align: center; padding: 50px;"


@app.get("/verify-email")
async def verify_email_link(token: str) -> Response:
    """Handle email verification link from email client.

    Processes verification token and returns HTML page with result.
    Redirects to frontend after successful verification.
    """
    frontend_url = settings.frontend_url

    def error_html(message: str) -> str:
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>Email Verification</title></head>
        <body style="{_PAGE_STYLE}">
            <h1>❌ Verification Failed</h1>
            <p>{message}</p>
            <p><a href="{frontend_url}">Go to login page</a></p>
        </body>
        </html>
        """

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        return Response(
            content=error_html("Verification token has expired."),
            media_type="text/html",
        )
    except jwt.InvalidTokenError:
        return Response(
            content=error_html("Invalid verification token."),
            media_type="text/html",
        )

    if payload.get("type") != "email_verification":
        return Response(
            content=error_html("Invalid token type."),
            media_type="text/html",
        )

    user_id: str = payload["sub"]

    async with AsyncSessionLocal() as session:
        identity_result = await session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == "email",
            )
        )
        identity = identity_result.scalar_one_or_none()
        if identity is None:
            return Response(
                content=error_html("User identity not found."),
                media_type="text/html",
            )

        if identity.verified_at is not None:
            return Response(
                content=f"""
                <!DOCTYPE html>
                <html>
                <head><title>Email Verification</title></head>
                <body style="{_PAGE_STYLE}">
                    <h1>✓ Email Already Verified</h1>
                    <p>Your email is already verified. You can log in now.</p>
                    <p><a href="{frontend_url}">Go to login page</a></p>
                </body>
                </html>
                """,
                media_type="text/html",
            )

        identity.verified_at = datetime.now(UTC)
        session.add(identity)

        await log_audit_action(
            session=session,
            user_id=user_id,
            action="email_verified",
        )

        await session.commit()

    return Response(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Email Verification</title>
            <meta http-equiv="refresh" content="3;url={frontend_url}">
        </head>
        <body style="{_PAGE_STYLE}">
            <h1>✓ Email Verified Successfully!</h1>
            <p>Your email has been verified. You can now log in.</p>
            <p>Redirecting to login page...</p>
            <p><a href="{frontend_url}">Go to login page</a></p>
        </body>
        </html>
        """,
        media_type="text/html",
    )
