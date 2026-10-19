"""Tests for US-12: JWT Token Utilities."""

import time
import uuid

import jwt
import pytest

from src.utils.jwt_utils import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
)


@pytest.fixture(autouse=True)
def set_jwt_secret(monkeypatch):
    """Ensure JWT_SECRET is set for all tests."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-unit-tests")


class TestConstants:
    """Test that TTL constants have correct values."""

    def test_access_token_expire_minutes(self):
        assert ACCESS_TOKEN_EXPIRE_MINUTES == 15

    def test_refresh_token_expire_days(self):
        assert REFRESH_TOKEN_EXPIRE_DAYS == 7


class TestCreateAccessToken:
    """Tests for create_access_token."""

    def test_creates_token_string(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_access_token(subject)

        # Assert
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_correct_subject(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_access_token(subject)
        payload = decode_token(token)

        # Assert
        assert payload["sub"] == subject

    def test_token_contains_exp_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_access_token(subject)
        payload = decode_token(token)

        # Assert
        assert "exp" in payload

    def test_token_contains_iat_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_access_token(subject)
        payload = decode_token(token)

        # Assert
        assert "iat" in payload

    def test_token_contains_jti_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_access_token(subject)
        payload = decode_token(token)

        # Assert
        assert "jti" in payload

    def test_access_token_expires_in_15_minutes(self):
        # Arrange
        subject = str(uuid.uuid4())
        before = int(time.time())

        # Act
        token = create_access_token(subject)
        payload = decode_token(token)

        # Assert: exp should be ~15 min (900 s) from now
        expected_exp = before + ACCESS_TOKEN_EXPIRE_MINUTES * 60
        assert abs(payload["exp"] - expected_exp) <= 2


class TestCreateRefreshToken:
    """Tests for create_refresh_token."""

    def test_creates_token_string(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_refresh_token(subject)

        # Assert
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_correct_subject(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_refresh_token(subject)
        payload = decode_token(token)

        # Assert
        assert payload["sub"] == subject

    def test_token_contains_exp_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_refresh_token(subject)
        payload = decode_token(token)

        # Assert
        assert "exp" in payload

    def test_token_contains_iat_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_refresh_token(subject)
        payload = decode_token(token)

        # Assert
        assert "iat" in payload

    def test_token_contains_jti_claim(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token = create_refresh_token(subject)
        payload = decode_token(token)

        # Assert
        assert "jti" in payload

    def test_refresh_token_expires_in_7_days(self):
        # Arrange
        subject = str(uuid.uuid4())
        before = int(time.time())

        # Act
        token = create_refresh_token(subject)
        payload = decode_token(token)

        # Assert: exp should be ~7 days from now
        expected_exp = before + REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        assert abs(payload["exp"] - expected_exp) <= 2


class TestDecodeToken:
    """Tests for decode_token."""

    def test_decodes_valid_access_token(self):
        # Arrange
        subject = str(uuid.uuid4())
        token = create_access_token(subject)

        # Act
        payload = decode_token(token)

        # Assert
        assert isinstance(payload, dict)
        assert payload["sub"] == subject

    def test_decodes_valid_refresh_token(self):
        # Arrange
        subject = str(uuid.uuid4())
        token = create_refresh_token(subject)

        # Act
        payload = decode_token(token)

        # Assert
        assert isinstance(payload, dict)
        assert payload["sub"] == subject

    def test_raises_expired_signature_error_for_expired_token(self, monkeypatch):
        # Arrange: create a token that is already expired
        import datetime

        secret = "test-secret-key-for-unit-tests"
        now = datetime.datetime.now(datetime.UTC)
        expired_payload = {
            "sub": str(uuid.uuid4()),
            "exp": now - datetime.timedelta(seconds=1),
            "iat": now - datetime.timedelta(minutes=16),
            "jti": str(uuid.uuid4()),
        }
        expired_token = jwt.encode(expired_payload, secret, algorithm="HS256")

        # Act & Assert
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(expired_token)

    def test_raises_invalid_token_error_for_bad_signature(self):
        # Arrange
        subject = str(uuid.uuid4())
        token = create_access_token(subject)
        tampered_token = token[:-5] + "XXXXX"

        # Act & Assert
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(tampered_token)

    def test_raises_invalid_token_error_for_garbage_input(self):
        # Arrange
        garbage = "this.is.not.a.jwt"

        # Act & Assert
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(garbage)


class TestJtiUniqueness:
    """Tests for JTI uniqueness across tokens."""

    def test_jti_is_unique_per_access_token(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token1 = create_access_token(subject)
        token2 = create_access_token(subject)
        payload1 = decode_token(token1)
        payload2 = decode_token(token2)

        # Assert
        assert payload1["jti"] != payload2["jti"]

    def test_jti_is_unique_per_refresh_token(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        token1 = create_refresh_token(subject)
        token2 = create_refresh_token(subject)
        payload1 = decode_token(token1)
        payload2 = decode_token(token2)

        # Assert
        assert payload1["jti"] != payload2["jti"]

    def test_jti_differs_between_access_and_refresh_token(self):
        # Arrange
        subject = str(uuid.uuid4())

        # Act
        access_token = create_access_token(subject)
        refresh_token = create_refresh_token(subject)
        access_payload = decode_token(access_token)
        refresh_payload = decode_token(refresh_token)

        # Assert
        assert access_payload["jti"] != refresh_payload["jti"]


class TestMissingJwtSecret:
    """Tests for missing JWT_SECRET environment variable."""

    def test_create_access_token_raises_runtime_error_without_secret(
        self, monkeypatch
    ):
        # Arrange
        monkeypatch.delenv("JWT_SECRET", raising=False)

        # Act & Assert
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            create_access_token(str(uuid.uuid4()))

    def test_create_refresh_token_raises_runtime_error_without_secret(
        self, monkeypatch
    ):
        # Arrange
        monkeypatch.delenv("JWT_SECRET", raising=False)

        # Act & Assert
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            create_refresh_token(str(uuid.uuid4()))

    def test_decode_token_raises_runtime_error_without_secret(self, monkeypatch):
        # Arrange: create token with secret present first
        subject = str(uuid.uuid4())
        token = create_access_token(subject)

        # Then remove secret
        monkeypatch.delenv("JWT_SECRET", raising=False)

        # Act & Assert
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            decode_token(token)
