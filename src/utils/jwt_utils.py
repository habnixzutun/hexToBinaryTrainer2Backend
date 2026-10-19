"""US-12: JWT Token Utilities.

Provides JWT token creation and decoding for access and refresh tokens.
Uses HS256 algorithm with a secret loaded from JWT_SECRET environment variable.
"""

import datetime
import os

import jwt
import uuid_utils

ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

_ALGORITHM = "HS256"


def _get_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    return secret


def _build_payload(subject: str, expires_delta: datetime.timedelta) -> dict:
    now = datetime.datetime.now(datetime.UTC)
    return {
        "sub": subject,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid_utils.uuid7()),
    }


def create_access_token(subject: str) -> str:
    """Create a signed JWT access token for the given subject (user UUID).

    Args:
        subject: User UUID string used as the token subject.

    Returns:
        Signed JWT string.

    Raises:
        RuntimeError: If JWT_SECRET environment variable is not set.
    """
    secret = _get_secret()
    payload = _build_payload(
        subject, datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    """Create a signed JWT refresh token for the given subject (user UUID).

    Args:
        subject: User UUID string used as the token subject.

    Returns:
        Signed JWT string.

    Raises:
        RuntimeError: If JWT_SECRET environment variable is not set.
    """
    secret = _get_secret()
    payload = _build_payload(
        subject, datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token.

    Args:
        token: JWT string to decode.

    Returns:
        Decoded payload as a dictionary.

    Raises:
        RuntimeError: If JWT_SECRET environment variable is not set.
        jwt.ExpiredSignatureError: If the token has expired.
        jwt.InvalidTokenError: If the token is invalid or tampered.
    """
    secret = _get_secret()
    return jwt.decode(token, secret, algorithms=[_ALGORITHM])
