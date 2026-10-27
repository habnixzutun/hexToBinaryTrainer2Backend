"""Password reset business logic: request and reset flow."""

import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import PasswordResetToken, User, UserIdentity, UserRefreshToken
from src.services.audit_service import log_audit_action, send_security_notification
from src.services.email_service import EmailService
from src.utils.jwt_utils import (
    PASSWORD_RESET_TOKEN_TTL_HOURS,
    create_password_reset_token,
    decode_token,
)
from src.utils.password import hash_password


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def request_password_reset(
    session: AsyncSession,
    email: str,
    email_service: EmailService,
) -> None:
    """Generate reset token and send email if user exists.

    Always returns without error regardless of whether the email is registered.
    """
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        return

    raw_token = create_password_reset_token(user.id)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(hours=PASSWORD_RESET_TOKEN_TTL_HOURS)

    record = PasswordResetToken(
        id=str(uuid7()),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        used_at=None,
        type="password_reset",
    )
    session.add(record)
    await session.commit()

    await email_service.send_password_reset_email(email, raw_token)


async def reset_password(
    session: AsyncSession,
    raw_token: str,
    new_password: str,
) -> None:
    """Validate token and update the user's password.

    Raises:
        ValueError("expired"): token is expired
        ValueError("invalid"): token is invalid or wrong type
        ValueError("already_used"): token was already used
    """
    try:
        payload = decode_token(raw_token)
    except jwt.ExpiredSignatureError:
        raise ValueError("expired") from None
    except jwt.InvalidTokenError:
        raise ValueError("invalid") from None

    if payload.get("type") != "password_reset":
        raise ValueError("invalid")

    user_id: str = payload["sub"]
    token_hash = _hash_token(raw_token)

    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise ValueError("invalid")
    if record.used_at is not None:
        raise ValueError("already_used")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise ValueError("expired")

    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email",
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is None:
        raise ValueError("invalid")

    identity.password_hash = hash_password(new_password)
    record.used_at = datetime.now(UTC)

    refresh_result = await session.execute(
        select(UserRefreshToken).where(UserRefreshToken.user_id == user_id)
    )
    refresh_tokens = refresh_result.scalars().all()
    now = datetime.now(UTC)
    for rt in refresh_tokens:
        if rt.revoked_at is None:
            rt.revoked_at = now

    await log_audit_action(
        session=session,
        user_id=user_id,
        action="password_reset",
    )

    await session.commit()

    await send_security_notification(
        session,
        user_id=user_id,
        action="password_reset",
    )
