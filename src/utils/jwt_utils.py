"""JWT token creation and decoding utilities."""

import os
from datetime import UTC, datetime, timedelta

import jwt
from uuid_utils import uuid7

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_MINUTES = 15
REFRESH_TOKEN_TTL_DAYS = 7
VERIFICATION_TOKEN_TTL_HOURS = 24
PASSWORD_RESET_TOKEN_TTL_HOURS = 1
EMAIL_CHANGE_TOKEN_TTL_HOURS = 24


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": str(uuid7()),
        "exp": datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_verification_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "email_verification",
        "exp": datetime.now(UTC) + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_password_reset_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "password_reset",
        "exp": datetime.now(UTC) + timedelta(hours=PASSWORD_RESET_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# create_email_change_token moved to src/utils/email_change_token.py (US-07).
# Email-change tokens are now Fernet-encrypted so the new email address is
# not visible in the clear inside the JWT.


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token.

    Raises:
        jwt.ExpiredSignatureError: if token is expired
        jwt.InvalidTokenError: if token is invalid
    """
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
