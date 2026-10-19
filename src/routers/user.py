"""User router: email change, email change verification, username change (US-16) and password change (US-19)."""

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
from src.services.user_service import change_password
from src.utils.jwt_utils import create_email_change_token, decode_token

USERNAME_CHANGE_COOLDOWN_DAYS = 7

_default_email_service: EmailService = MockEmailService()


def get_email_service() -> EmailService:
    return _default_email_service


def set_email_service(service: EmailService) -> None:
    global _default_email_service
    _default_email_service = service


def _extract_bearer_user_id(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )
    token = auth[len("Bearer "):]
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )
    return payload["sub"]


router = APIRouter(tags=["user"])


class EmailChangeRequest(BaseModel):
    new_email: EmailStr


class EmailChangeVerifyRequest(BaseModel):
    token: str


class UsernameChangeRequest(BaseModel):
    new_username: str = Field(min_length=3, max_length=50)


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
    body: EmailChangeVerifyRequest,
    session: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    try:
        payload = decode_token(body.token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token has expired.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token.",
        )

    if payload.get("type") != "email_change":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token type.",
        )

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

    await email_service.send_security_notification_email(old_email, "email_changed")
    await send_security_notification(user_id, "email_changed")

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

    await send_security_notification(user_id, "username_changed")

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
            )
        if error == "weak_password":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password does not meet strength requirements.",
            )
        if error == "same_password":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be different from old password.",
            )
        if error == "identity_not_found":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User identity not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password change failed.",
        )

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="password_change",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()

    await send_security_notification(user_id, "password_change")

    return {"message": "Password changed successfully. All sessions have been invalidated."}
