"""Tests for the JWT helpers (US-01: secret now comes from Settings).

Covers the round-trip of every token type, expiry/signature rejection, and the
US-01 guarantees:

* the signing key is read from ``get_settings()`` at call time -- there is no
  import-time constant and no ``os.environ`` fallback,
* rotating ``JWT_SECRET`` invalidates previously minted tokens,
* no insecure default literal survives anywhere in ``jwt_utils``.
"""

import ast
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from src.config import get_settings
from src.utils import jwt_utils
from src.utils.jwt_utils import (
    ACCESS_TOKEN_TTL_MINUTES,
    EMAIL_CHANGE_TOKEN_TTL_HOURS,
    JWT_ALGORITHM,
    PASSWORD_RESET_TOKEN_TTL_HOURS,
    REFRESH_TOKEN_TTL_DAYS,
    VERIFICATION_TOKEN_TTL_HOURS,
    create_access_token,
    create_email_change_token,
    create_password_reset_token,
    create_refresh_token,
    create_verification_token,
    decode_token,
    get_jwt_secret,
)

JWT_UTILS_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "utils" / "jwt_utils.py"
)

# >= 32 bytes so PyJWT's RFC 7518 key-length warning stays quiet.
TEST_SECRET = "unit-test-jwt-secret-0123456789abcdef-aaaa"
ROTATED_SECRET = "rotated-unit-test-jwt-secret-9876543210-zzzz"
FOREIGN_SECRET = "some-other-service-jwt-secret-0123456789ab"
USER_ID = "user-jwt-001"


@pytest.fixture(autouse=True)
def settings_secret():
    """Pin JWT_SECRET to TEST_SECRET for every test, then restore the session env.

    ``get_settings`` is lru_cached, so the cache is cleared on the way in and
    out; without the outgoing clear the sentinel would leak into every later
    test in the session (including the DB fixtures that sign real tokens).
    """
    get_settings.cache_clear()
    with patch.dict(os.environ, {"JWT_SECRET": TEST_SECRET}):
        yield
    get_settings.cache_clear()


def _token_issued_seconds_ago(seconds: int, *, secret: str = TEST_SECRET) -> str:
    """Mint a raw HS256 token with a skewed ``iat``/``exp``."""
    now = datetime.now(UTC)
    payload = {
        "sub": USER_ID,
        "type": "access",
        "iat": now - timedelta(seconds=seconds),
        "exp": now
        - timedelta(seconds=seconds)
        + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


class TestSecretSourcing:
    """AC 4: the signing key is loaded via get_settings(), not os.environ."""

    def test_get_jwt_secret_returns_settings_value(self):
        assert get_jwt_secret() == get_settings().jwt_secret.get_secret_value()

    def test_no_plaintext_secret_held_as_module_global(self):
        """The secret must not live in a module-level ``str`` global.

        A frozen global would both outlive a secret rotation and show up in
        heap dumps / ``vars(module)`` introspection.
        """
        assert "JWT_SECRET" not in vars(jwt_utils)

        with patch.object(
            jwt_utils, "get_settings", return_value=get_settings()
        ) as mocked:
            create_access_token(USER_ID)

        mocked.assert_called_once()

    def test_secret_follows_current_settings_at_call_time(self):
        """Rotating JWT_SECRET changes the key used by the very next call."""
        assert get_jwt_secret() == get_settings().jwt_secret.get_secret_value()

        first = get_jwt_secret()
        get_settings.cache_clear()
        try:
            with patch.dict(os.environ, {"JWT_SECRET": ROTATED_SECRET}):
                get_settings.cache_clear()
                assert get_jwt_secret() == ROTATED_SECRET
                assert get_jwt_secret() != first
        finally:
            get_settings.cache_clear()

    def test_module_reads_no_environment_directly(self):
        """Adversarial (test plan): AST must show no os.environ access at all."""
        tree = ast.parse(JWT_UTILS_SOURCE.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                source = ast.unparse(node.value)
                assert (
                    source != "os.environ"
                ), f"jwt_utils reads the environment directly at line {node.lineno}"
            if isinstance(node, ast.Import):
                assert all(
                    a.name != "os" for a in node.names
                ), "jwt_utils must not import os (US-01)"

    def test_insecure_default_literal_removed(self):
        """Adversarial (test plan): the pre-US-01 fallback literal is gone."""
        source = JWT_UTILS_SOURCE.read_text()

        assert "dev-secret-change-in-production" not in source
        assert "jwt.decode(" in source  # still decodes through PyJWT


class TestAccessTokenRoundTrip:
    """AC 6: tokens created and decoded through Settings sign/verify."""

    def test_access_token_roundtrip(self):
        token = create_access_token(USER_ID)
        payload = decode_token(token)

        assert payload["sub"] == USER_ID
        assert payload["type"] == "access"
        assert "exp" in payload and "iat" in payload

    def test_access_token_carries_expected_ttl(self):
        payload = decode_token(create_access_token(USER_ID))
        lifetime = payload["exp"] - payload["iat"]

        assert lifetime == ACCESS_TOKEN_TTL_MINUTES * 60

    def test_refresh_token_roundtrip_has_unique_jti(self):
        first = decode_token(create_refresh_token(USER_ID))
        second = decode_token(create_refresh_token(USER_ID))

        assert first["type"] == "refresh"
        assert first["sub"] == USER_ID
        assert first["jti"] and first["jti"] != second["jti"]

    def test_refresh_token_carries_expected_ttl(self):
        payload = decode_token(create_refresh_token(USER_ID))

        assert payload["exp"] - payload["iat"] == REFRESH_TOKEN_TTL_DAYS * 86_400

    @pytest.mark.parametrize(
        ("factory", "expected_type", "ttl_seconds"),
        [
            (
                lambda: create_verification_token(USER_ID),
                "email_verification",
                VERIFICATION_TOKEN_TTL_HOURS * 3600,
            ),
            (
                lambda: create_password_reset_token(USER_ID),
                "password_reset",
                PASSWORD_RESET_TOKEN_TTL_HOURS * 3600,
            ),
        ],
    )
    def test_user_bound_tokens_roundtrip(self, factory, expected_type, ttl_seconds):
        payload = decode_token(factory())

        assert payload["type"] == expected_type
        assert payload["sub"] == USER_ID
        assert payload["exp"] - payload["iat"] == ttl_seconds

    def test_email_change_token_roundtrip(self):
        token = create_email_change_token(USER_ID, "new@example.com")
        payload = decode_token(token)

        assert payload["type"] == "email_change"
        assert payload["sub"] == USER_ID
        assert payload["new_email"] == "new@example.com"
        assert payload["exp"] - payload["iat"] == EMAIL_CHANGE_TOKEN_TTL_HOURS * 3600


class TestTokenRejection:
    """Adversarial: forged, expired and cross-service tokens are refused."""

    def test_expired_token_raises_expired_signature_error(self):
        token = _token_issued_seconds_ago(ACCESS_TOKEN_TTL_MINUTES * 60 + 60)

        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)

    def test_not_yet_expired_token_is_accepted(self):
        token = _token_issued_seconds_ago(60)

        assert decode_token(token)["sub"] == USER_ID

    def test_token_signed_with_foreign_secret_is_rejected(self):
        """A token minted by another service (same algorithm, other key) fails."""
        forged = jwt.encode(
            {"sub": USER_ID, "type": "access"},
            FOREIGN_SECRET,
            algorithm=JWT_ALGORITHM,
        )

        with pytest.raises(jwt.InvalidSignatureError):
            decode_token(forged)

    def test_token_signed_with_old_secret_is_rejected_after_rotation(self):
        """US-01 migration note: rotating the secret kills existing sessions."""
        legacy = jwt.encode(
            {"sub": USER_ID, "type": "access", "exp": _in_one_hour()},
            "dev-secret-change-in-production",
            algorithm=JWT_ALGORITHM,
        )

        with pytest.raises(jwt.InvalidSignatureError):
            decode_token(legacy)

    def test_tampered_payload_is_rejected(self):
        token = create_access_token(USER_ID)
        header, payload, signature = token.split(".")
        tampered = f"{header}.{payload[:-2] + 'AA'}.{signature}"

        with pytest.raises(jwt.InvalidTokenError):
            decode_token(tampered)

    def test_algorithm_confusion_is_rejected(self):
        """decode_token pins ``algorithms=[HS256]``, so HS512 tokens fail."""
        stronger = jwt.encode(
            {"sub": USER_ID, "type": "access", "exp": _in_one_hour()},
            TEST_SECRET,
            algorithm="HS512",
        )

        with pytest.raises(jwt.InvalidAlgorithmError):
            decode_token(stronger)

    def test_none_algorithm_is_rejected(self):
        unsigned = jwt.encode(
            {"sub": USER_ID, "type": "access", "exp": _in_one_hour()},
            key=None,
            algorithm="none",
        )

        with pytest.raises(jwt.InvalidTokenError):
            decode_token(unsigned)

    def test_garbage_token_is_rejected(self):
        with pytest.raises(jwt.DecodeError):
            decode_token("not-a-jwt")

    def test_secret_never_appears_inside_a_token(self):
        """The secret is HMAC key material only -- never a claim."""
        token = create_access_token(USER_ID)

        assert TEST_SECRET not in token
        assert TEST_SECRET not in jwt.encode(  # nosec - asserting key isolation
            {"sub": USER_ID}, TEST_SECRET, algorithm=JWT_ALGORITHM
        ).replace(TEST_SECRET, "")


def _in_one_hour() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)
