"""Authentication service: login, refresh, logout business logic."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import UserIdentity, UserRefreshToken
from src.utils.jwt_utils import (
    REFRESH_TOKEN_TTL_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.utils.password import verify_password


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def login(
    session: AsyncSession,
    email: str,
    password: str,
) -> tuple[str, str]:
    """Validate credentials and return (access_token, refresh_token).

    Raises:
        ValueError("unverified"): account exists but email not verified
        ValueError("unauthorized"): wrong password or unknown user
    """
    result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.provider == "email",
            UserIdentity.provider_user_id == email,
        )
    )
    identity = result.scalar_one_or_none()

    if identity is None or identity.password_hash is None:
        raise ValueError("unauthorized")

    if not verify_password(password, identity.password_hash):
        raise ValueError("unauthorized")

    if identity.verified_at is None:
        raise ValueError("unverified")

    access_token = create_access_token(identity.user_id)
    refresh_token = create_refresh_token(identity.user_id)

    token_record = UserRefreshToken(
        id=str(uuid7()),
        user_id=identity.user_id,
        token_hash=_hash_token(refresh_token),
        expires_at=datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    )
    session.add(token_record)
    await session.commit()

    return access_token, refresh_token


async def refresh(
    session: AsyncSession,
    refresh_token: str,
) -> str:
    """Validate refresh token and return a new access_token.

    Raises:
        ValueError("unauthorized"): token invalid, expired, or revoked
    """
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise ValueError("unauthorized") from None

    if payload.get("type") != "refresh":
        raise ValueError("unauthorized")

    token_hash = _hash_token(refresh_token)
    result = await session.execute(
        select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise ValueError("unauthorized")
    if record.revoked_at is not None:
        raise ValueError("unauthorized")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise ValueError("unauthorized")

    return create_access_token(record.user_id)


async def logout(
    session: AsyncSession,
    refresh_token: str,
) -> None:
    """Revoke the refresh token if it exists. Idempotent."""
    token_hash = _hash_token(refresh_token)
    result = await session.execute(
        select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await session.commit()
