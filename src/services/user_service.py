"""User service: password change and other user-level operations."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import UserIdentity, UserRefreshToken
from src.utils.password import hash_password, validate_password_strength, verify_password


async def change_password(
    session: AsyncSession,
    user_id: str,
    old_password: str,
    new_password: str,
) -> None:
    """Change password for an email-provider identity.

    Raises:
        ValueError("wrong_password"): old password does not match
        ValueError("weak_password"): new password fails strength check
        ValueError("same_password"): new password is identical to old
        ValueError("identity_not_found"): no email identity for this user
    """
    result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email",
        )
    )
    identity = result.scalar_one_or_none()

    if identity is None or identity.password_hash is None:
        raise ValueError("identity_not_found")

    if not verify_password(old_password, identity.password_hash):
        raise ValueError("wrong_password")

    strength_errors = validate_password_strength(new_password)
    if strength_errors:
        raise ValueError("weak_password")

    if verify_password(new_password, identity.password_hash):
        raise ValueError("same_password")

    identity.password_hash = hash_password(new_password)
    session.add(identity)

    await _revoke_all_refresh_tokens(session, user_id)

    await session.commit()


async def _revoke_all_refresh_tokens(session: AsyncSession, user_id: str) -> None:
    result = await session.execute(
        select(UserRefreshToken).where(
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
        )
    )
    tokens = result.scalars().all()
    now = datetime.now(UTC)
    for token in tokens:
        token.revoked_at = now
        session.add(token)
