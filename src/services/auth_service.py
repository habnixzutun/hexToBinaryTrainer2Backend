"""Authentication service: login, refresh, logout business logic."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import UserIdentity, UserRefreshToken
from src.services.audit_service import log_audit_action, send_security_notification
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
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """Validate refresh token, rotate, and return new tokens.

    On success: revokes the presented token and issues a fresh
    (access_token, refresh_token) pair. The caller MUST persist the new
    refresh token to the client (e.g. via a httponly cookie) — otherwise
    the rotation is lost and the user cannot refresh again.

    Raises:
        ValueError("unauthorized"): token invalid, expired, or unknown
        ValueError("replay_detected"): token was already revoked
            (possible replay attack) — all user sessions are revoked
            before this is raised.
    """
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise ValueError("unauthorized") from None

    if payload.get("type") != "refresh":
        raise ValueError("unauthorized")

    user_id = payload["sub"]
    token_hash = _hash_token(refresh_token)

    result = await session.execute(
        select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise ValueError("unauthorized")

    # REPLAY DETECTION: revoked_at is set → token was used before or revoked.
    # Per ADR DEC-02 (aggressive strategy): revoke ALL tokens for the user,
    # log the event, notify the user, then reject the request.
    if record.revoked_at is not None:
        await _revoke_all_user_tokens(session, user_id)
        await _log_replay_detection(
            session, user_id, record, ip_address, user_agent
        )
        await session.commit()
        raise ValueError("replay_detected")

    # Check expiration (only reached for non-revoked tokens)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise ValueError("unauthorized")

    # Revoke the current token (rotation — single use)
    record.revoked_at = datetime.now(UTC)

    # Create new refresh token
    new_refresh_token = create_refresh_token(user_id)
    new_token_hash = _hash_token(new_refresh_token)
    new_record = UserRefreshToken(
        id=str(uuid7()),
        user_id=user_id,
        token_hash=new_token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    )
    session.add(new_record)

    # Create new access token
    new_access_token = create_access_token(user_id)

    await session.commit()

    return new_access_token, new_refresh_token


async def _revoke_all_user_tokens(
    session: AsyncSession, user_id: str
) -> None:
    """Revoke all active refresh tokens for a user (replay attack response).

    Implements ADR DEC-02 aggressive strategy: any presented token that is
    already revoked triggers a full session kill. Only not-yet-revoked tokens
    are touched (idempotent wrt already-revoked ones).

    Uses a bulk UPDATE to avoid read-modify-write races under concurrent
    callers, mirroring the pattern from ``logout_all()``.
    """
    await session.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def _log_replay_detection(
    session: AsyncSession,
    user_id: str,
    record: UserRefreshToken,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Log a refresh-token replay event and notify the user.

    The audit entry records which token was replayed (by id, not the secret
    hash) together with the request origin. A security notification is
    dispatched so the account owner learns about the incident even if they
    never inspect the audit log.
    """
    await log_audit_action(
        session=session,
        user_id=user_id,
        action="refresh_token_replay_detected",
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "token_id": record.id,
            "expired_at": record.expires_at.isoformat(),
        },
    )

    await send_security_notification(
        user_id=user_id,
        action="refresh_token_replay_detected",
    )


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


async def logout_all(
    session: AsyncSession,
    user_id: str,
) -> int:
    """Revoke every not-yet-revoked refresh token of a user (kill-all-sessions).

    Tokens that are already expired are revoked too: they are formally still
    open sessions, and revoking them keeps the audit count equal to the number
    of sessions the user actually had. Already revoked tokens are skipped, so
    the call is idempotent (second call returns 0).

    Implemented as a single bulk UPDATE rather than a read-modify-write loop:
    one round trip, no per-object mutation, and no lost-update window between
    SELECT and UPDATE under concurrent callers.

    Returns:
        Number of tokens revoked by this call (0 if the user had none open).
    """
    result = await session.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()

    return int(result.rowcount or 0)
