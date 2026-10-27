"""Auth router: registration, email verification, login, refresh, logout."""

import json
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.config import get_settings
from src.database.engine import get_db
from src.models.auth import User, UserIdentity
from src.models.auth_schemas import (
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    VerifyEmailRequest,
)
from src.routers.user import _extract_bearer_user_id
from src.services import auth_service, password_reset_service
from src.services.audit_service import log_audit_action
from src.services.email_service import EmailService, MockEmailService
from src.services.rate_limit_service import RateLimitService
from src.utils.jwt_utils import create_verification_token, decode_token
from src.utils.password import hash_password, validate_password_strength

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_SECONDS = 3600

_default_email_service: EmailService = MockEmailService()


def get_email_service() -> EmailService:
    return _default_email_service


def set_email_service(service: EmailService) -> None:
    global _default_email_service
    _default_email_service = service


def _email_key(request: Request) -> str:
    """Rate limit key derived from login email, falls back to IP."""
    try:
        raw = getattr(request.state, "_body", b"")
        data = json.loads(raw)
        email = data.get("email", "")
        if email:
            return f"login:{email}"
    except Exception:
        pass
    return get_remote_address(request)


limiter = Limiter(key_func=_email_key)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="strict",
        max_age=COOKIE_MAX_AGE,
    )


def _delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="strict",
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    try:
        access_token, refresh_token = await auth_service.login(
            session, body.email, body.password
        )
    except ValueError as exc:
        if str(exc) == "unverified":
            raise HTTPException(
                status_code=403, detail="Account not verified."
            ) from None
        raise HTTPException(status_code=401, detail="Invalid credentials.") from None

    _set_refresh_cookie(response, refresh_token)
    return LoginResponse(access_token=access_token)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> RefreshResponse:
    refresh_token = request.cookies.get(COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token.")

    try:
        access_token, new_refresh_token = await auth_service.refresh(
            session=session,
            refresh_token=refresh_token,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            email_service=email_service,
        )
    except ValueError as exc:
        if str(exc) == "replay_detected":
            # Replay attack: all sessions have been revoked by the service
            # layer. Clear the cookie so the client does not retry with it,
            # and surface a distinct message so the user knows to
            # re-authenticate.
            _delete_refresh_cookie(response)
            raise HTTPException(
                status_code=401,
                detail="Security violation detected. All sessions have been revoked.",
            ) from None
        raise HTTPException(
            status_code=401, detail="Invalid or expired refresh token."
        ) from None

    # Rotation: the old token has been revoked server-side and a fresh one
    # was issued. Persist the new refresh token to the client.
    _set_refresh_cookie(response, new_refresh_token)

    return RefreshResponse(access_token=access_token)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict:
    refresh_token = request.cookies.get(COOKIE_NAME)
    if refresh_token:
        await auth_service.logout(session, refresh_token)

    _delete_refresh_cookie(response)
    return {"message": "Logged out."}


class LogoutAllResponse(BaseModel):
    message: str
    count: int


async def get_current_user_id(request: Request) -> str:
    """Resolve the caller from the ``Authorization: Bearer <access token>`` header.

    Delegates to the Bearer parser in the user router so that every
    auth-required endpoint answers with identical 401 details instead of
    drifting into two divergent token validators.
    """
    return _extract_bearer_user_id(request)


@router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> LogoutAllResponse:
    """Revoke all active refresh tokens of the authenticated user (US-06)."""
    count = await auth_service.logout_all(session, user_id)

    # Only real revocations are audited: a no-op (count == 0) must not add a
    # duplicate entry when the user calls again.
    if count > 0:
        await log_audit_action(
            session=session,
            user_id=user_id,
            action="all_sessions_revoked",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata={"session_count": count},
        )
        await session.commit()

    return LogoutAllResponse(message="All sessions revoked.", count=count)


@router.post(
    "/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse
)
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> RegisterResponse:
    client_ip = request.client.host if request.client else "unknown"

    # US-04: Persistent rate limit (DB-based, not in-memory)
    rate_limit = RateLimitService(session)
    if not await rate_limit.check(
        key=f"register:{client_ip}:{body.email}",
        limit=RATE_LIMIT_MAX,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )

    strength_errors = validate_password_strength(body.password)
    if strength_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": strength_errors},
        )

    # Password IMMER hashen VOR den Existenz-Checks (Timing-Schutz!)
    # bcrypt kostet ~190ms, dadurch sind beide Pfade zeitlich identisch.
    password_hash = hash_password(body.password)

    # Existenz-Checks durchführen (aber nicht früh zurückkehren!)
    existing_email = await session.execute(select(User).where(User.email == body.email))
    email_exists = existing_email.scalar_one_or_none() is not None

    existing_username = await session.execute(
        select(User).where(User.username == body.username)
    )
    username_exists = existing_username.scalar_one_or_none() is not None

    is_duplicate = email_exists or username_exists

    # User und Identity IMMER erstellen (Timing-Schutz!)
    # Bei Duplicate wird die Transaktion später zurückgesetzt.
    user_id = str(uuid7())
    user = User(id=user_id, username=body.username, email=body.email)
    session.add(user)

    identity = UserIdentity(
        id=str(uuid7()),
        user_id=user_id,
        provider="email",
        provider_user_id=body.email,
        password_hash=password_hash,
        verified_at=None,
    )
    session.add(identity)

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="registration",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )

    # Bei Duplicate: Rollback statt Commit
    if is_duplicate:
        await session.rollback()
    else:
        try:
            await session.commit()
        except IntegrityError:
            # Race Condition: Ein anderer Request hat zwischen SELECT und INSERT committed
            # → Behandle als Duplicate (silent 201)
            await session.rollback()
            is_duplicate = True  # Markiere als Duplicate für Response-Logik

    # Verification-Token IMMER erstellen (Timing-Schutz!)
    verification_token = create_verification_token(user_id)

    # Email nur bei frischen Registrierungen senden
    if not is_duplicate:
        try:
            await email_service.send_verification_email(body.email, verification_token)
        except Exception as e:
            # Log but don't fail registration if email can't be sent
            print(f"[WARN] Could not send verification email: {e}")

    return RegisterResponse(
        message="If this email is available, a verification mail has been sent.",
        user_id=None,
    )


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    body: VerifyEmailRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        payload = decode_token(body.token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired.",
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token.",
        ) from None

    if payload.get("type") != "email_verification":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token type.",
        )

    user_id: str = payload["sub"]

    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email",
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User identity not found.",
        )

    if identity.verified_at is not None:
        return {"message": "Email already verified."}

    identity.verified_at = datetime.now(UTC)
    session.add(identity)

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="email_verified",
    )

    await session.commit()

    return {"message": "Email verified successfully."}


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification(
    request: Request,
    body: ResendVerificationRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    """US-04: Resend verification email for unverified accounts.

    Silent handling: Always returns the same generic message regardless of
    whether the email exists, is verified, or is unverified. This prevents
    email enumeration attacks.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit: 3/h per IP+email
    rate_limit = RateLimitService(session)
    if not await rate_limit.check(
        key=f"resend:{client_ip}:{body.email}",
        limit=3,
        window_seconds=3600,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests.",
        )

    # Look up user by email
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is not None:
        # Check if user has an unverified email identity
        identity_result = await session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == "email",
            )
        )
        identity = identity_result.scalar_one_or_none()

        # Only send email if identity exists and is not yet verified
        if identity and identity.verified_at is None:
            verification_token = create_verification_token(user.id)
            try:
                await email_service.send_verification_email(
                    body.email, verification_token
                )
            except Exception:
                # Silent failure: don't leak whether email was sent
                pass

    # Audit log (only if user exists)
    if user is not None:
        await log_audit_action(
            session=session,
            user_id=user.id,
            action="verification_resend_requested",
            ip_address=client_ip,
        )
        await session.commit()

    return {
        "message": "If this email is registered and unverified, a new mail has been sent."
    }


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/request-password-reset", status_code=status.HTTP_200_OK)
async def request_password_reset(
    body: RequestPasswordResetRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    await password_reset_service.request_password_reset(
        session=session,
        email=body.email,
        email_service=email_service,
    )
    return {"message": "If this email is registered, a reset link has been sent."}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    strength_errors = validate_password_strength(body.new_password)
    if strength_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": strength_errors},
        )

    try:
        await password_reset_service.reset_password(
            session=session,
            raw_token=body.token,
            new_password=body.new_password,
            email_service=email_service,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        reason = str(exc)
        if reason == "expired":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired.",
            ) from None
        if reason == "already_used":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has already been used.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        ) from None

    return {"message": "Password reset successful."}
