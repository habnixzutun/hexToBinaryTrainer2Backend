"""Tests for US-07: Fernet-encrypted email change tokens.

Acceptance criteria:

1. The returned token does NOT contain the new email address in plaintext
   (Fernet ciphertext is opaque; the JWT claims are hidden inside).
2. Round-trip: ``create_email_change_token`` → ``verify_email_change_token``
   returns the original claims (user_id, new_email, type).
3. Expired token is rejected (ValueError "expired").
4. Invalid / tampered / garbage tokens are rejected.
5. ``email_change_key`` shorter than 32 chars is rejected at Settings
   validation time.
"""

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from src.config import Settings, get_settings
from src.utils.email_change_token import (
    _fernet_key,
    create_email_change_token,
    verify_email_change_token,
)
from src.utils.jwt_utils import (
    EMAIL_CHANGE_TOKEN_TTL_HOURS,
    JWT_ALGORITHM,
    get_jwt_secret,
)

USER_ID = "user-us07-001"
NEW_EMAIL = "secret-new-email@example.com"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Make sure each test sees a fresh Settings view.

    Some tests patch env vars or change state; clearing the lru_cache
    avoids cross-test leakage. Restored afterwards.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build_encrypted_jwt(payload: dict) -> str:
    """Helper: build a Fernet-encrypted JWT using the configured key.

    Lets tests craft specific payloads (expired, wrong type, ...) that
    the production code would never produce, to exercise error paths.
    """
    fernet_key = _fernet_key()
    jwt_bytes = pyjwt.encode(
        payload, get_jwt_secret(), algorithm=JWT_ALGORITHM
    ).encode("utf-8")
    return Fernet(fernet_key).encrypt(jwt_bytes).decode("utf-8")


# ---------------------------------------------------------------------------
# AC 1: new_email is NOT visible in plaintext in the token
# ---------------------------------------------------------------------------


class TestEmailChangeTokenEncryption:
    def test_token_does_not_contain_new_email_in_plaintext(self) -> None:
        token = create_email_change_token(USER_ID, NEW_EMAIL)
        # The email address must not appear as a substring. Fernet
        # ciphertext is url-safe base64 and contains no raw payload bytes.
        assert NEW_EMAIL not in token

    def test_token_does_not_contain_user_id_in_plaintext(self) -> None:
        token = create_email_change_token(USER_ID, NEW_EMAIL)
        assert USER_ID not in token

    def test_token_is_not_a_plain_jwt(self) -> None:
        # Plain JWTs have exactly two dots (header.payload.signature).
        # Fernet tokens use only url-safe base64 chars (no dots).
        token = create_email_change_token(USER_ID, NEW_EMAIL)
        assert "." not in token

    def test_token_differs_across_calls_for_same_inputs(self) -> None:
        # Fernet uses a random IV per encryption. This prevents an
        # attacker who has seen one token from recognizing a duplicate
        # request for the same user/email in log files.
        t1 = create_email_change_token(USER_ID, NEW_EMAIL)
        t2 = create_email_change_token(USER_ID, NEW_EMAIL)
        assert t1 != t2


# ---------------------------------------------------------------------------
# AC 2: round-trip works
# ---------------------------------------------------------------------------


class TestEmailChangeTokenRoundTrip:
    def test_round_trip_returns_original_claims(self) -> None:
        token = create_email_change_token(USER_ID, NEW_EMAIL)
        payload = verify_email_change_token(token)

        assert payload["sub"] == USER_ID
        assert payload["new_email"] == NEW_EMAIL
        assert payload["type"] == "email_change"
        assert "exp" in payload
        assert "iat" in payload

    def test_round_trip_with_various_emails(self) -> None:
        emails = [
            "a@b.co",
            "very.long+tag@example.com",
            "user@sub.domain.example.org",
        ]
        for email in emails:
            payload = verify_email_change_token(
                create_email_change_token(USER_ID, email)
            )
            assert payload["new_email"] == email


# ---------------------------------------------------------------------------
# AC 3: expired token is rejected
# ---------------------------------------------------------------------------


class TestEmailChangeTokenExpired:
    def test_expired_token_raises_valueerror(self) -> None:
        payload = {
            "sub": USER_ID,
            "type": "email_change",
            "new_email": NEW_EMAIL,
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iat": datetime.now(UTC)
            - timedelta(hours=EMAIL_CHANGE_TOKEN_TTL_HOURS + 1),
        }
        token = _build_encrypted_jwt(payload)

        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token(token)
        assert str(exc_info.value) == "expired"


# ---------------------------------------------------------------------------
# AC 4: invalid / tampered / garbage tokens are rejected
# ---------------------------------------------------------------------------


class TestEmailChangeTokenInvalid:
    def test_garbage_token_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token("not-a-fernet-token")
        assert str(exc_info.value) == "invalid token"

    def test_empty_token_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token("")
        assert str(exc_info.value) == "invalid token"

    def test_tampered_ciphertext_rejected(self) -> None:
        token = create_email_change_token(USER_ID, NEW_EMAIL)
        # Flip one byte in the middle of the ciphertext. Fernet's HMAC
        # must catch this.
        middle = len(token) // 2
        replacement = "A" if token[middle] != "A" else "B"
        tampered = token[:middle] + replacement + token[middle + 1 :]
        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token(tampered)
        assert str(exc_info.value) == "invalid token"

    def test_plain_jwt_rejected(self) -> None:
        """A valid, unencrypted JWT must NOT be accepted -- the endpoint
        must only accept Fernet-wrapped tokens after US-07."""
        payload = {
            "sub": USER_ID,
            "type": "email_change",
            "new_email": NEW_EMAIL,
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iat": datetime.now(UTC),
        }
        plain_jwt = pyjwt.encode(
            payload, get_jwt_secret(), algorithm=JWT_ALGORITHM
        )
        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token(plain_jwt)
        assert str(exc_info.value) == "invalid token"

    def test_wrong_token_type_rejected(self) -> None:
        payload = {
            "sub": USER_ID,
            "type": "access",  # wrong type
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iat": datetime.now(UTC),
        }
        token = _build_encrypted_jwt(payload)
        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token(token)
        assert str(exc_info.value) == "invalid token type"

    def test_wrong_key_rejected(self) -> None:
        """A token encrypted with a different email_change_key must not
        decrypt successfully.
        """
        # Build a Fernet key from an arbitrary different secret.
        other_raw = b"D" * 40
        other_fernet_key = base64.urlsafe_b64encode(
            hashlib.sha256(other_raw).digest()
        )
        payload = {
            "sub": USER_ID,
            "type": "email_change",
            "new_email": NEW_EMAIL,
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iat": datetime.now(UTC),
        }
        jwt_bytes = pyjwt.encode(
            payload, get_jwt_secret(), algorithm=JWT_ALGORITHM
        ).encode("utf-8")
        token = Fernet(other_fernet_key).encrypt(jwt_bytes).decode("utf-8")

        with pytest.raises(ValueError) as exc_info:
            verify_email_change_token(token)
        assert str(exc_info.value) == "invalid token"


# ---------------------------------------------------------------------------
# AC 5: email_change_key < 32 chars is rejected at Settings validation
# ---------------------------------------------------------------------------


class TestEmailChangeKeyValidation:
    def test_short_key_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"EMAIL_CHANGE_KEY": "too-short-to-be-safe"},
            clear=False,
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings()
            errors = exc_info.value.errors()
            locations = [
                ".".join(str(part) for part in err["loc"]) for err in errors
            ]
            assert any(
                "email_change_key" in loc for loc in locations
            ), f"expected email_change_key in error locations, got {locations}"

    def test_exactly_32_char_key_accepted(self) -> None:
        key = "a" * 32
        with patch.dict(os.environ, {"EMAIL_CHANGE_KEY": key}, clear=False):
            settings = Settings()
            assert settings.email_change_key.get_secret_value() == key

    def test_default_key_is_at_least_32_chars(self) -> None:
        settings = Settings()
        assert len(settings.email_change_key.get_secret_value()) >= 32

    def test_key_is_a_secret_str(self) -> None:
        settings = Settings()
        # __repr__ of SecretStr must NOT leak the value.
        repr_value = repr(settings.email_change_key)
        assert settings.email_change_key.get_secret_value() not in repr_value
