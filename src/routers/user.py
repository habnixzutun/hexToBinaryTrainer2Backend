"""User router: current user info and email/username/password changes (US-16, US-19).

``GET /api/user/me`` is the Sprint-4 read side of US-37 — identity fields plus the
economy numbers, served from :mod:`src.services.profile_service` so it cannot
disagree with ``GET /api/game/profile`` (US-35). The legacy ``points`` field and its
``user_stats`` source are gone from this file; the ``UserStats`` ORM class itself
stays in :mod:`src.models.game` until US-34 drops the table.
"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import get_db
from src.models.auth import User, UserIdentity
from src.services.audit_service import log_audit_action, send_security_notification
from src.services.email_service import EmailService, MockEmailService
from src.services.profile_service import load_snapshot
from src.services.user_service import change_password
from src.utils.email_change_token import (
    create_email_change_token,
    verify_email_change_token,
)
from src.utils.jwt_utils import decode_token

USERNAME_CHANGE_COOLDOWN_DAYS = 7

_default_email_service: EmailService = MockEmailService()


def get_email_service() -> EmailService:
    return _default_email_service


def set_email_service(service: EmailService) -> None:
    global _default_email_service
    _default_email_service = service


def _extract_bearer_user_id(request: Request) -> str:
    """The authenticated identity behind ``Authorization: Bearer <token>``.

    Shared by every authenticated endpoint (``/api/user/*``, the legacy game routes
    and US-33's ``POST /api/game/answer``), which is why every rejection here is a
    ``401`` and never an exception that escapes to a 500: the caller supplies the
    header and the token's contents, so each of those escapes would be an
    attacker-controlled error path. Nothing in here touches the database, so a
    rejected caller costs no connection and no query (US-33 N17/N18).

    Raises:
        HTTPException: 401 for a missing/malformed header, an undecodable, expired,
            wrong-type or subject-less token.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )
    token = auth[len("Bearer ") :]
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        ) from None
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )
    # Wave 2a review B-2: the subject is verified, not assumed. ``payload["sub"]``
    # raised ``KeyError`` on a correctly signed, correctly typed access token with no
    # ``sub`` claim — an unauthenticated caller turning a 401 into a 500, on every
    # endpoint that shares this parser. Measured against PyJWT 2.15.1, which is what
    # ``decode_token`` uses: a token whose ``sub`` is absent, or an empty string,
    # DECODES FINE (so nothing above it rejects it), while a non-string ``sub``
    # (``None``, ``42``, a list) is already rejected by PyJWT's own ``sub`` validator
    # with ``InvalidSubjectError`` — a subclass of ``InvalidTokenError``, caught
    # above as "Invalid token.". The ``isinstance`` check is therefore
    # defence-in-depth for a library that loosens, and the emptiness check is not: an
    # empty identity would otherwise flow into the answer endpoint's rate-limit key
    # (``answer:``) and into ``record_answer``, whose non-empty-identity guard raises
    # ``ValueError`` — the same 500, one layer down.
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )
    return sub


router = APIRouter(tags=["user"])


class EmailChangeRequest(BaseModel):
    new_email: EmailStr


class EmailChangeVerifyRequest(BaseModel):
    token: str


class UsernameChangeRequest(BaseModel):
    new_username: str = Field(min_length=3, max_length=50)


class UserMeResponse(BaseModel):
    """The nine fields US-37 froze: identity, then the Sprint-4 economy numbers."""

    id: str
    username: str
    email: str
    correct_count: int
    incorrect_count: int
    total_xp: int
    level: int
    current_streak: int
    best_streak: int


@router.get(
    "/api/user/me",
    status_code=status.HTTP_200_OK,
    response_model=UserMeResponse,
    summary="Current user profile with economy state (US-37)",
)
async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserMeResponse:
    """Return the caller's identity plus their economy state.

    US-37 moved this read off the legacy ``user_stats`` counters and onto
    ``user_game_profile``, through the same
    :func:`~src.services.profile_service.load_snapshot` that serves
    ``GET /api/game/profile`` (US-35). Sharing the loader is the whole point: the six
    economy fields the two endpoints have in common are then the same five stored
    columns plus one :func:`~src.services.level_service.level_from_xp` call, so
    "``/me`` and ``/profile`` agree field-for-field" is a property of the code rather
    than a coincidence two handlers happen to preserve. ``tests/test_user_me.py``
    pins it against a shared fixture anyway.

    A missing profile row answers the D-1/OQ-3 zero state (level 1 at 0 XP) rather
    than erroring or inserting: the row is created lazily by the first
    ``POST /api/game/answer`` (US-28 AC-3), and a GET that wrote would put a
    round-trip and a lock on the profile screen's hottest path.

    Raises:
        HTTPException: 401 for a missing/invalid/expired/wrong-type token — the only
            meaning of 401 here. 404 when the token is fine but the user it names is
            gone (Rev 3 R-10): the legacy handler answered that with 401, which told
            every client to re-authenticate for an account that no longer exists, and
            diverged from ``/api/game/answer`` (US-33) and ``/api/game/profile``
            (US-35), which both answer 404 "User not found." for this condition.
    """
    user_id = _extract_bearer_user_id(request)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    snapshot = await load_snapshot(session, user_id)

    return UserMeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        total_xp=snapshot.total_xp,
        level=snapshot.level,
        current_streak=snapshot.current_streak,
        best_streak=snapshot.best_streak,
        correct_count=snapshot.correct_count,
        incorrect_count=snapshot.incorrect_count,
    )


@router.patch("/api/user/email", status_code=status.HTTP_200_OK)
async def request_email_change(
    request: Request,
    body: EmailChangeRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    user_id = _extract_bearer_user_id(request)

    existing = await session.execute(
        select(User).where(User.email == str(body.new_email))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered.",
        )

    token = create_email_change_token(user_id, str(body.new_email))

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="email_change_requested",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"new_email": str(body.new_email)},
    )
    await session.commit()

    await email_service.send_email_change_verification(str(body.new_email), token)

    return {"message": "Verification email sent to new address."}


@router.post("/api/auth/verify-email-change", status_code=status.HTTP_200_OK)
async def verify_email_change(
    request: Request,
    body: EmailChangeVerifyRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    # US-07: tokens are Fernet-encrypted; new_email is not visible in the
    # clear until decrypted. verify_email_change_token raises ValueError
    # with a machine-readable reason ("expired", "invalid token",
    # "invalid token type") that we map to user-facing messages.
    try:
        payload = verify_email_change_token(body.token)
    except ValueError as exc:
        reason = str(exc)
        if reason == "expired":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token has expired.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token.",
        ) from None

    user_id: str = payload["sub"]
    new_email: str = payload["new_email"]

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found.",
        )

    old_email = user.email
    user.email = new_email
    session.add(user)

    # Also update the email provider identity so login works with the new email
    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email",
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is not None:
        identity.provider_user_id = new_email
        session.add(identity)

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="email_changed",
        metadata={"old_email": old_email, "new_email": new_email},
    )
    await session.commit()

    await send_security_notification(
        session,
        user_id=user_id,
        action="email_changed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"old_email": old_email, "new_email": new_email},
        recipient_override=old_email,
    )

    return {"message": "Email updated successfully."}


@router.patch("/api/user/username", status_code=status.HTTP_200_OK)
async def change_username(
    request: Request,
    body: UsernameChangeRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    user_id = _extract_bearer_user_id(request)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if user.last_username_change_at is not None:
        last_change = user.last_username_change_at
        if last_change.tzinfo is None:
            last_change = last_change.replace(tzinfo=UTC)
        next_change_at = last_change + timedelta(days=USERNAME_CHANGE_COOLDOWN_DAYS)
        if datetime.now(UTC) < next_change_at:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "message": "Username can only be changed once every 7 days.",
                    "next_change_at": next_change_at.isoformat(),
                },
            )

    existing = await session.execute(
        select(User).where(User.username == body.new_username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken.",
        )

    user.username = body.new_username
    user.last_username_change_at = datetime.now(UTC)
    session.add(user)

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="username_changed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"new_username": body.new_username},
    )
    await session.commit()

    await send_security_notification(
        session,
        user_id=user_id,
        action="username_changed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"new_username": body.new_username},
    )

    return {"message": "Username updated successfully."}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/user/change-password", status_code=status.HTTP_200_OK)
async def change_password_endpoint(
    request: Request,
    body: ChangePasswordRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    user_id = _extract_bearer_user_id(request)

    try:
        await change_password(
            session=session,
            user_id=user_id,
            old_password=body.old_password,
            new_password=body.new_password,
        )
    except ValueError as e:
        error = str(e)
        if error == "wrong_password":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Old password is incorrect.",
            ) from None
        if error == "weak_password":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password does not meet strength requirements.",
            ) from None
        if error == "same_password":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be different from old password.",
            ) from None
        if error == "identity_not_found":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User identity not found.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password change failed.",
        ) from None

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="password_change",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()

    await send_security_notification(
        session,
        user_id=user_id,
        action="password_change",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return {
        "message": "Password changed successfully. All sessions have been invalidated."
    }
