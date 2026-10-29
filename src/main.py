import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import jwt
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from starlette.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.database.engine import AsyncSessionLocal
from src.middleware.body_size import AnswerBodySizeLimitMiddleware
from src.migrations.v001_auth_schema import create_migrations_table
from src.migrations.v001_auth_schema import run_migration as run_v001
from src.migrations.v002_game_stats import run_migration as run_v002
from src.migrations.v003_rate_limit import run_migration as run_v003
from src.migrations.v004_xp_schema import run_migration as run_v004
from src.migrations.v005_drop_user_stats import run_migration as run_v005
from src.models.auth import UserIdentity
from src.routers.auth import router as auth_router
from src.routers.game import router as game_router
from src.routers.user import router as user_router
from src.services.audit_service import log_audit_action
from src.services.email_wiring import install_email_service
from src.services.rate_limit_service import run_periodic_cleanup
from src.utils.jwt_utils import decode_token

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot: migrations, email wiring, then the rate-limit sweep.

    Wave 2a review S-4 — the migration block is wrapped so a startup failure is
    LOUD and terminal instead of quiet. Every migration here opens its own
    transaction (no shared session across them), so a failure mid-way leaves the
    already-committed ones applied and the rest not: the schema is then whatever
    prefix succeeded. Serving traffic in that state means ``POST /api/game/answer``
    writes to a table that may not exist, and the only signal a operator would have
    had was an unstructured traceback plus a ``/healthz`` that answers 200 with
    ``str(exception)`` in its body (US-18's contract, left untouched on purpose —
    see the residual note below).

    ``logger.critical`` + re-raise is the containment: the exception propagates out
    of the lifespan, uvicorn logs ``Application startup failed`` and exits nonzero,
    so an orchestrator restarts or halts the rollout instead of shipping a pod that
    answers 200 while its economy is broken. Swallowing the error and yielding
    anyway — the other reading of "containment" — would be the worse choice here,
    because nothing downstream can tell a half-migrated schema from a whole one.

    Residual, deliberately not taken in this commit: ``/healthz`` still returns 200
    with ``{"status": "error", "database": "disconnected", "error": str(e)}`` on a
    failed probe. Both halves of that — the status code and the leak of a driver
    message to an unauthenticated caller (CWE-200) — belong to US-18's contract and
    to the ops dashboard that reads it, so changing them is not a review fix. Still
    open as follow-up now that US-34 has closed the legacy game routes.
    """
    try:
        async with AsyncSessionLocal() as session:
            await create_migrations_table(session)
            await session.commit()

        async with AsyncSessionLocal() as session:
            await run_v001(session)
            await run_v002(session)
            await run_v003(session)
            # US-27 (D-6 / Rev 3 R-1): ADDITIVE only — creates user_game_profile and
            # leaves the legacy Sprint-2 table in place, so the co-existence window
            # stays green on every commit.
            await run_v004(session)
            # US-34, sprint close: the destructive half of that split. Drops the
            # legacy table now that /answer (US-32/33) is the only writer and
            # /profile, /leaderboard and /me (US-35/36/37) are the only readers.
            # Idempotent, and a no-op on a database that never ran v002.
            await run_v005(session)
    except Exception:
        # exc_info, not str(e): the exception TYPE is the diagnostic here (a lost
        # connection, a permission denial and a half-applied migration all look the
        # same as one line of text). CRITICAL because this is the one failure mode
        # that leaves the process unable to serve its own economy correctly.
        logger.critical(
            "startup migrations failed — refusing to serve traffic", exc_info=True
        )
        raise

    install_email_service(get_settings())

    # US-32 (Rev 3 R-2): expired rate-limit rows are swept here — once on startup
    # and then on ``RATE_LIMIT_SWEEP_INTERVAL_SECONDS`` — because the answer
    # endpoint's counting entry point no longer deletes anything per request. The
    # task is scheduled, never awaited, so a cold table cannot delay boot; it owns
    # its own session and its failures, and shutdown cancels it.
    cleanup_task = asyncio.create_task(
        run_periodic_cleanup(AsyncSessionLocal), name="rate-limit-sweep"
    )
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass  # the expected ending: shutdown stopped the sweep
        except Exception:  # pragma: no cover - the loop swallows its own failures
            logger.exception("rate-limit sweep ended unexpectedly")


app = FastAPI(lifespan=lifespan)

settings = get_settings()
# Order matters: ``add_middleware`` PREPENDS, so registering the body-size cap before
# CORSMiddleware leaves CORS as the outermost layer and a 413 carries the same CORS
# headers as every other response (a browser can read the refusal instead of seeing an
# opaque network error). The cap itself still runs before FastAPI parses the body,
# which is the only place a 4 KB ceiling can be enforced for free (US-32 AC-5).
app.add_middleware(AnswerBodySizeLimitMiddleware)
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 without the echo (Wave 2a review R-1, CWE-200).

    FastAPI's default handler serialises every error with ``input`` and ``ctx``
    filled in, so the body a client sent comes straight back in the refusal. The
    answer endpoint takes an unbounded-in-content ``mode`` and a strict ``int``
    ``bit_width``, which turns a malformed request into free outbound bandwidth and
    puts whatever was in the payload — including anything a proxy or error tracker
    later logs — into a response body.

    Only three keys survive: ``loc`` (WHERE), ``msg`` (WHY) and ``type`` (machine
    readable). The field path stays because a client cannot act without knowing
    which field was refused; the VALUE is what has no legitimate reader here.

    Residual, stated rather than hidden: ``extra="forbid"`` reports the offending
    KEY inside ``loc``, so a client-chosen field name is still reflected. It is
    bounded by the 4 KiB ceiling of ``AnswerBodySizeLimitMiddleware`` — the refusal
    can carry at most what the client already paid to send, so the amplification
    ratio stays at ~1 and no value is echoed. Field-value echoes are closed twice
    over: ``input`` is gone here, and :meth:`AnswerRequest._validate_payload_budget`
    keeps the messages this handler forwards short for anything that DID parse.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": [
                {
                    "loc": list(err.get("loc", ())),
                    "msg": err.get("msg"),
                    "type": err.get("type"),
                }
                for err in exc.errors()
            ]
        },
    )


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
