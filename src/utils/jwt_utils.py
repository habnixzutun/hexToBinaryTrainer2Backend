"""JWT token creation and decoding utilities."""

from datetime import UTC, datetime, timedelta

import jwt
from uuid_utils import uuid7

from src.config import get_settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_MINUTES = 15
REFRESH_TOKEN_TTL_DAYS = 7
VERIFICATION_TOKEN_TTL_HOURS = 24
PASSWORD_RESET_TOKEN_TTL_HOURS = 1
EMAIL_CHANGE_TOKEN_TTL_HOURS = 24


def get_jwt_secret() -> str:
    """Return the current JWT secret from Settings.

    Note: Settings is cached via @lru_cache. Secret rotation requires
    a process restart or explicit get_settings.cache_clear().
    """
    return get_settings().jwt_signing_key


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": str(uuid7()),
        "exp": datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_verification_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "email_verification",
        "exp": datetime.now(UTC) + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_password_reset_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "password_reset",
        "exp": datetime.now(UTC) + timedelta(hours=PASSWORD_RESET_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_email_change_token(user_id: str, new_email: str) -> str:
    payload = {
        "sub": user_id,
        "type": "email_change",
        "new_email": new_email,
        "exp": datetime.now(UTC) + timedelta(hours=EMAIL_CHANGE_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token.

    Raises:
        jwt.ExpiredSignatureError: if token is expired
        jwt.InvalidTokenError: if token is invalid
    """
    return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
