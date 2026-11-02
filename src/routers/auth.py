"""Auth router: registration, email verification, login, refresh, logout."""

import logging
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.config import get_settings
from src.database.engine import get_db
from src.models.auth import User, UserIdentity, UserRefreshToken
from src.models.auth_schemas import (
    DeviceResponse,
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
from src.utils.hash_token import hash_token as _hash_token
from src.utils.jwt_utils import create_verification_token, decode_token
from src.utils.login_lockout import (
    check_login_lockout,
    record_login_failure,
    reset_login_lockout,
)
from src.utils.password import hash_password, validate_password_strength

logger = logging.getLogger(__name__)

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600

# US-04: persistent (DB-backed) rate limits. Keys combine the client IP with
# the submitted email, so neither a rotating IP pool nor a server restart can
# lift a limit. Limits and windows are declared per endpoint because the abuse
# profiles differ (brute force vs. registration bombing vs. mail bombing).
LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = SECONDS_PER_MINUTE
REGISTER_RATE_LIMIT_MAX = 3
REGISTER_RATE_LIMIT_WINDOW_SECONDS = SECONDS_PER_HOUR
RESEND_RATE_LIMIT_MAX = 3
RESEND_RATE_LIMIT_WINDOW_SECONDS = SECONDS_PER_HOUR

# US-04: single generic answer for /resend-verification. Every outcome —
# unknown address, already verified, mail sent — returns exactly this text so
# the endpoint cannot be used to enumerate registered emails.
RESEND_VERIFICATION_MESSAGE = (
    "If this email is registered and unverified, a new mail has been sent."
)

_default_email_service: EmailService = MockEmailService()


def get_email_service() -> EmailService:
    return _default_email_service


def set_email_service(service: EmailService) -> None:
    global _default_email_service
    _default_email_service = service


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
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    client_ip = request.client.host if request.client else "unknown"

    # US-05: Check adaptive login lockout (progressive lockout by IP)
    is_allowed = await check_login_lockout(client_ip, session)
    if not is_allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed login attempts. "
                "Account locked out. Try again later."
            ),
        )

    # US-04: Check rate limit (per IP+email combination).
    # Wave 2a review B-1 — RESIDUAL, known and accepted, not an oversight. B-1 moved
    # ``login_lockout``'s unkeyed purge to the background sweep. THIS call, and the
    # three like it (register, resend-verification, ``security_notification_service``),
    # still run ``check``: an unbounded ``DELETE … WHERE expires_at < now`` therefore
    # remains on every pre-auth auth path. They stay because all four ``check()``
    # callers are pinned byte-for-byte (R-2) — moving them to ``check_without_cleanup``
    # is the same verdict without the purge, but it is a US-34 decision, not a review
    # fix. Why it does not amplify today: the sweep in ``src/main.py``'s lifespan
    # reclaims every expired row within ``RATE_LIMIT_SWEEP_INTERVAL_SECONDS``, so the
    # scan reads at most one window plus one interval of rows rather than a table that
    # grows without bound. Documented in ``rate_limit_service``'s module docstring too.
    rate_limit = RateLimitService(session)
    if not await rate_limit.check(
        f"login:{client_ip}:{body.email}",
        limit=LOGIN_RATE_LIMIT_MAX,
        window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Too many login attempts.")

    try:
        access_token, refresh_token = await auth_service.login(
            session,
            body.email,
            body.password,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        # US-05: Record failed login attempt for adaptive lockout
        await record_login_failure(client_ip, session)

        if str(exc) == "unverified":
            raise HTTPException(
                status_code=403, detail="Account not verified."
            ) from None
        raise HTTPException(status_code=401, detail="Invalid credentials.") from None

    # US-05: Successful login resets the lockout counter
    await reset_login_lockout(client_ip, session, email=body.email)

    _set_refresh_cookie(response, refresh_token)
    return LoginResponse(access_token=access_token)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
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
        f"register:{client_ip}:{body.email}",
        limit=REGISTER_RATE_LIMIT_MAX,
        window_seconds=REGISTER_RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Too many registration attempts.")

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
            # Race Condition: Anderer Request hat zwischen SELECT und INSERT
            # committed → Behandle als Duplicate (silent 201)
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
            logger.warning("Could not send verification email: %s", e)

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
    """US-04/US-08: Resend verification email for unverified accounts.

    Silent handling: Always returns the same generic message regardless of
    whether the email exists, is verified, or is unverified. This prevents
    email enumeration attacks.
    """
    client_ip = request.client.host if request.client else "unknown"

    # US-04: Rate limit: 3/h per IP+email (DB-backed)
    rate_limit = RateLimitService(session)
    if not await rate_limit.check(
        f"resend:{client_ip}:{body.email}",
        limit=RESEND_RATE_LIMIT_MAX,
        window_seconds=RESEND_RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Too many requests.")

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
        if identity is not None and identity.verified_at is None:
            verification_token = create_verification_token(user.id)
            try:
                await email_service.send_verification_email(
                    body.email, verification_token
                )
            except Exception:
                # Silent failure: never leak delivery status to the caller
                pass

        # Audit log (only if user exists)
        await log_audit_action(
            session=session,
            user_id=user.id,
            action="verification_resend_requested",
            ip_address=client_ip,
        )
        await session.commit()

    return {"message": RESEND_VERIFICATION_MESSAGE}


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


# ---------------------------------------------------------------------------
# US-45: Device list
# ---------------------------------------------------------------------------

def _parse_user_agent(ua_string: str | None) -> tuple[str, str | None, str | None]:
    """Parse a User-Agent string into (device_type, browser, os).

    device_type is one of: 'Mobile', 'Tablet', 'PC', 'Unknown'.
    browser and os may be None when the UA is absent or unparseable.
    """
    if not ua_string:
        return "Unknown", None, None

    try:
        from user_agents import parse as ua_parse  # type: ignore[import]

        ua = ua_parse(ua_string)
        browser: str | None = ua.browser.family or None
        os_name: str | None = ua.os.family or None

        if ua.is_tablet:
            device_type = "Tablet"
        elif ua.is_mobile:
            device_type = "Mobile"
        elif ua.is_pc:
            device_type = "PC"
        else:
            device_type = "Unknown"

        return device_type, browser, os_name
    except Exception:
        return "Unknown", None, None


@router.get("/devices", response_model=dict)
async def list_devices(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """US-45: Return active sessions for the authenticated user.

    Active means: not revoked AND not expired.
    is_current=True for the entry whose token_hash matches the current
    refresh cookie (if any).
    """
    now = datetime.now(UTC)

    result = await session.execute(
        select(UserRefreshToken).where(
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
            UserRefreshToken.expires_at > now,
        )
    )
    tokens = result.scalars().all()

    # Determine the hash of the current cookie (if present)
    current_cookie = request.cookies.get(COOKIE_NAME)
    current_hash: str | None = (
        _hash_token(current_cookie) if current_cookie else None
    )

    devices: list[DeviceResponse] = []
    for token in tokens:
        device_type, browser, os_name = _parse_user_agent(token.user_agent)
        is_current = current_hash is not None and token.token_hash == current_hash

        # Serialise datetimes to ISO 8601 strings
        created_at_val = token.created_at
        if created_at_val is not None:
            if hasattr(created_at_val, "tzinfo") and created_at_val.tzinfo is None:
                created_at_val = created_at_val.replace(tzinfo=UTC)
            created_str = created_at_val.isoformat()
        else:
            created_str = now.isoformat()

        expires_at_val = token.expires_at
        if hasattr(expires_at_val, "tzinfo") and expires_at_val.tzinfo is None:
            expires_at_val = expires_at_val.replace(tzinfo=UTC)
        expires_str = expires_at_val.isoformat()

        devices.append(
            DeviceResponse(
                id=token.id,
                device_type=device_type,
                browser=browser,
                os=os_name,
                ip_address=token.ip_address,
                location_city=token.location_city,
                location_country=token.location_country,
                created_at=created_str,
                expires_at=expires_str,
                is_current=is_current,
            )
        )

    return {"devices": [d.model_dump() for d in devices]}


# ---------------------------------------------------------------------------
# US-46: Single device logout (revoke one session by token_id)
# ---------------------------------------------------------------------------


@router.delete("/devices/{token_id}", status_code=status.HTTP_200_OK)
async def revoke_device(
    token_id: str,
    request: Request,
    response: Response,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """US-46: Revoke a single active session by its token primary-key ID.

    Returns 404 when:
    - token_id does not exist
    - token belongs to a different user
    - token is already revoked
    - token is expired (not a usable session)

    When the revoked token matches the caller's current refresh cookie the
    cookie is cleared so the client is immediately logged out.
    """
    now = datetime.now(UTC)

    result = await session.execute(
        select(UserRefreshToken).where(UserRefreshToken.id == token_id)
    )
    record = result.scalar_one_or_none()

    # 404 for non-existent, foreign-user, already-revoked, or expired tokens
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found.")

    if record.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Session not found.")

    expires_at = record.expires_at
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Revoke the token
    record.revoked_at = now
    await session.commit()

    # If this was the caller's current session, clear the cookie
    current_cookie = request.cookies.get(COOKIE_NAME)
    if current_cookie and _hash_token(current_cookie) == record.token_hash:
        _delete_refresh_cookie(response)

    return {"revoked": True}

