"""US-07: Fernet-encrypted email change tokens.

The previous implementation embedded the new email address as a plaintext
JWT claim. Anyone intercepting the verification link (or reading it out of
a mail provider's logs) could see the target email address in the clear.

This module wraps the email-change JWT in a Fernet (AES-128-CBC +
HMAC-SHA256) envelope so the new email is only visible after decryption
with the configured ``email_change_key``. The Fernet key is derived from
the secret via SHA-256, producing the 32-byte url-safe-base64 key Fernet
requires.

Token format: ``Fernet.encrypt(JWT.encode(payload))``
"""

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet, InvalidToken

from src.config import get_settings
from src.utils.jwt_utils import (
    EMAIL_CHANGE_TOKEN_TTL_HOURS,
    JWT_ALGORITHM,
    JWT_SECRET,
)


def _fernet_key() -> bytes:
    """Derive a Fernet-compatible 32-byte url-safe-base64 key.

    SHA-256 compresses the configured ``email_change_key`` to a fixed
    32-byte digest; Fernet wants that digest base64url-encoded.
    """
    raw = get_settings().email_change_key.get_secret_value().encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def create_email_change_token(user_id: str, new_email: str) -> str:
    """Build a Fernet-encrypted email change token.

    The underlying JWT carries ``sub``, ``type``, ``new_email``, ``exp``,
    ``iat``. The JWT string is encrypted with Fernet so ``new_email`` is
    not visible in the returned token.
    """
    payload = {
        "sub": user_id,
        "type": "email_change",
        "new_email": new_email,
        "exp": datetime.now(UTC)
        + timedelta(hours=EMAIL_CHANGE_TOKEN_TTL_HOURS),
        "iat": datetime.now(UTC),
    }
    jwt_bytes = jwt.encode(
        payload, JWT_SECRET, algorithm=JWT_ALGORITHM
    ).encode("utf-8")
    fernet = Fernet(_fernet_key())
    return fernet.encrypt(jwt_bytes).decode("utf-8")


def verify_email_change_token(token: str) -> dict:
    """Decrypt and validate an email change token.

    Returns:
        The decoded JWT payload (``sub``, ``type``, ``new_email``,
        ``exp``, ``iat``) on success.

    Raises:
        ValueError: With one of the following messages:
            ``"expired"`` -- the JWT ``exp`` claim is in the past.
            ``"invalid token type"`` -- the JWT ``type`` is not
                ``"email_change"``.
            ``"invalid token"`` -- anything else (malformed, tampered,
                wrong key, not a Fernet token, not a JWT).
    """
    try:
        fernet = Fernet(_fernet_key())
        # ttl=None: we enforce expiry via the JWT ``exp`` claim, not via
        # Fernet's own TTL (which would force a hard recrypt window).
        jwt_bytes = fernet.decrypt(token, ttl=None)
    except (InvalidToken, Exception):
        # Catch-all: any failure to decrypt (bad format, wrong key,
        # tampered ciphertext, ...) surfaces as "invalid token".
        raise ValueError("invalid token") from None

    try:
        payload = jwt.decode(
            jwt_bytes, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid token") from exc

    if payload.get("type") != "email_change":
        raise ValueError("invalid token type")

    return payload
