"""Account deletion business logic: request and confirm flow (US-47/US-48)."""

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import AccountDeletionToken, User, UserIdentity
from src.services.email_service import EmailService
from src.utils.hash_token import hash_token as _hash_token
from src.utils.jwt_utils import JWT_ALGORITHM, get_jwt_secret
from src.utils.password import verify_password

ACCOUNT_DELETION_TOKEN_TYPE = "account_deletion"
ACCOUNT_DELETION_TOKEN_TTL_HOURS = 1


def _create_account_deletion_token(user_id: str) -> str:
    """Generate a signed JWT for account deletion confirmation."""
    payload = {
        "sub": user_id,
        "type": ACCOUNT_DELETION_TOKEN_TYPE,
        "exp": datetime.now(UTC) + timedelta(hours=ACCOUNT_DELETION_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
        "jti": str(uuid7()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


async def request_account_deletion(
    session: AsyncSession,
    user_id: str,
    email: str,
    username: str,
    email_service: EmailService,
) -> None:
    """Generate a deletion token and send a confirmation email.

    Invalidates any existing pending deletion token for the user before
    issuing a new one, so no stale rows accumulate in account_deletion_tokens.

    Args:
        session: Database session.
        user_id: The authenticated user's ID.
        email: The user's current email address.
        username: The user's current username.
        email_service: Email service for sending the confirmation email.
    """
    raw_token = _create_account_deletion_token(user_id)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(hours=ACCOUNT_DELETION_TOKEN_TTL_HOURS)

    # Remove any existing active deletion tokens for this user (deduplication).
    await session.execute(
        delete(AccountDeletionToken).where(
            AccountDeletionToken.user_id == user_id,
            AccountDeletionToken.used_at.is_(None),
        )
    )

    deletion_record = AccountDeletionToken(
        id=str(uuid7()),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        used_at=None,
    )
    session.add(deletion_record)

    await session.commit()

    await email_service.send_account_deletion_request_email(email, raw_token)


async def confirm_account_deletion(
    session: AsyncSession,
    raw_token: str,
    password: str,
    email_service: EmailService,
) -> None:
    """Validate the deletion token and permanently delete the user account.

    Steps:
    1. Decode the JWT (raises ValueError on expired/invalid).
    2. Verify the token type is "account_deletion".
    3. Look up the AccountDeletionToken row by hash.
    4. Check the token is not already used and not expired.
    5. Verify the user's password.
    6. Mark token as used (double-submission safety).
    7. Delete the user (CASCADE removes all child rows).
    8. Send confirmation email.

    Args:
        session: Database session.
        raw_token: The raw JWT from the confirmation link.
        password: The user's current password to confirm identity.
        email_service: Email service for sending the deletion confirmation.

    Raises:
        ValueError("expired"): Token is expired.
        ValueError("invalid"): Token is invalid, wrong type, or not found.
        ValueError("already_used"): Token was already consumed.
        ValueError("wrong_password"): Password does not match.
    """
    try:
        payload = jwt.decode(
            raw_token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("expired") from None
    except jwt.InvalidTokenError:
        raise ValueError("invalid") from None

    if payload.get("type") != ACCOUNT_DELETION_TOKEN_TYPE:
        raise ValueError("invalid")

    user_id: str = payload["sub"]
    token_hash = _hash_token(raw_token)

    result = await session.execute(
        select(AccountDeletionToken).where(
            AccountDeletionToken.token_hash == token_hash,
            AccountDeletionToken.user_id == user_id,
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

    # Verify password against user's email identity.
    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email",
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is None:
        raise ValueError("invalid")

    if not verify_password(password, identity.password_hash):
        raise ValueError("wrong_password")

    # Fetch user info before deletion (needed for confirmation email).
    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise ValueError("invalid")

    user_email = user.email
    user_username = user.username

    # Mark token as used BEFORE deletion (double-submission safety).
    record.used_at = datetime.now(UTC)
    await session.flush()

    # Delete user — CASCADE removes identities, refresh tokens, audit logs,
    # game profile, password_reset_tokens, account_deletion_tokens, etc.
    await session.delete(user)
    await session.commit()

    # Send confirmation email to the (now-deleted) user's address.
    await email_service.send_account_deleted_confirmation_email(
        user_email, user_username
    )
