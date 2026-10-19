import pytest

from src.utils.password import hash_password, validate_password_strength, verify_password

VALID_PASSWORD = "SecurePass1"


class TestValidatePasswordStrength:
    def test_valid_password_does_not_raise(self) -> None:
        validate_password_strength(VALID_PASSWORD)

    def test_empty_password_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Password"):
            validate_password_strength("")

    def test_too_short_password_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Password"):
            validate_password_strength("Short1")

    def test_too_long_password_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Password"):
            validate_password_strength("A1" + "a" * 127)

    def test_password_without_uppercase_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Password"):
            validate_password_strength("nouppercase1")

    def test_password_without_digit_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Password"):
            validate_password_strength("NoDigitHere")

    def test_exactly_8_characters_is_valid(self) -> None:
        validate_password_strength("Secure1x")

    def test_exactly_128_characters_is_valid(self) -> None:
        validate_password_strength("A1" + "a" * 126)


class TestHashPassword:
    def test_returns_string(self) -> None:
        result = hash_password(VALID_PASSWORD)
        assert isinstance(result, str)

    def test_hash_is_not_plaintext(self) -> None:
        result = hash_password(VALID_PASSWORD)
        assert result != VALID_PASSWORD

    def test_two_hashes_of_same_password_differ(self) -> None:
        hash1 = hash_password(VALID_PASSWORD)
        hash2 = hash_password(VALID_PASSWORD)
        assert hash1 != hash2


class TestVerifyPassword:
    def test_correct_password_returns_true(self) -> None:
        hashed = hash_password(VALID_PASSWORD)
        assert verify_password(VALID_PASSWORD, hashed) is True

    def test_wrong_password_returns_false(self) -> None:
        hashed = hash_password(VALID_PASSWORD)
        assert verify_password("WrongPass1", hashed) is False

    def test_empty_plain_against_valid_hash_returns_false(self) -> None:
        hashed = hash_password(VALID_PASSWORD)
        assert verify_password("", hashed) is False
