"""Auth router: registration, email verification, login, refresh, logout."""

import json
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.config import get_settings
from src.database.engine import get_db
from src.models.auth import User, UserIdentity
from src.models.auth_schemas import RegisterRequest, RegisterResponse, VerifyEmailRequest
from src.services import auth_service
from src.services.audit_service import log_audit_action
from src.services.email_service import EmailService, MockEmailService
from src.utils.jwt_utils import create_verification_token, decode_token
from src.utils.password import hash_password, validate_password_strength

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_SECONDS = 3600

_registration_attempts: dict[str, list[float]] = {}
_default_email_service: EmailService = MockEmailService()


def get_email_service() -> EmailService:
    return _default_email_service


def set_email_service(service: EmailService) -> None:
    global _default_email_service
    _default_email_service = service


def _check_rate_limit(ip: str) -> None:
    now = datetime.now(UTC).timestamp()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    attempts = [t for t in _registration_attempts.get(ip, []) if t > window_start]
    _registration_attempts[ip] = attempts
    if len(attempts) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )
    attempts.append(now)
    _registration_attempts[ip] = attempts


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
            raise HTTPException(status_code=403, detail="Account not verified.")
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    _set_refresh_cookie(response, refresh_token)
    return LoginResponse(access_token=access_token)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    refresh_token = request.cookies.get(COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token.")

    try:
        access_token = await auth_service.refresh(session, refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

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


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> RegisterResponse:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    strength_errors = validate_password_strength(body.password)
    if strength_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": strength_errors},
        )

    existing_email = await session.execute(
        select(User).where(User.email == body.email)
    )
    if existing_email.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered.",
        )

    existing_username = await session.execute(
        select(User).where(User.username == body.username)
    )
    if existing_username.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken.",
        )

    user_id = str(uuid7())
    user = User(id=user_id, username=body.username, email=body.email)
    session.add(user)

    identity = UserIdentity(
        id=str(uuid7()),
        user_id=user_id,
        provider="email",
        provider_user_id=body.email,
        password_hash=hash_password(body.password),
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

    await session.commit()

    verification_token = create_verification_token(user_id)
    await email_service.send_verification_email(body.email, verification_token)

    return RegisterResponse(
        message="Registration successful. Check your email to verify your account.",
        user_id=user_id,
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
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token.",
        )

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
