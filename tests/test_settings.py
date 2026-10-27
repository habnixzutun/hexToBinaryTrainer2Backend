"""Tests for Settings factory function (US-05) and JWT_SECRET hardening (US-01).

Verifies that Settings uses factory pattern with lru_cache instead of module-level
instantiation, and that ``JWT_SECRET`` is a required ``SecretStr`` with no
fallback default (US-01, CRITICAL).

Leak regression tests (US-01 AC 7) assert that no sentinel secret ever reaches
an error message, a traceback, or captured stdout/stderr -- including the
startup crash path through :func:`src.config.get_settings`.
"""

import os
import traceback
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError
from src.config import Settings, get_settings

# Sentinels used only inside tests. If any of them shows up in an error
# message, a traceback or captured log output, the leak regression tests fail.
SENTINEL_JWT_SECRET = "jwt-secret-sentinel-0123456789abcdef-never-log"
SENTINEL_DB_PW = "db-pw-sentinel-0123456789abcdef-never-log"

# Minimal env that satisfies every Settings validator.
VALID_JWT_ENV = {"JWT_SECRET": SENTINEL_JWT_SECRET}


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """Keep env-patching tests from leaking cached Settings to other tests.

    Several tests below patch ``os.environ`` and rebuild Settings. Without a
    forced cache clear, the last patched instance would survive into the next
    test file (which is how the pre-US-01 version of this module silently
    polluted ``DB_HOST`` for the whole session).
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGetSettings:
    """Test get_settings factory function."""

    def test_get_settings_returns_settings_instance(self):
        """Verify get_settings returns a Settings instance."""
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_caches_result(self):
        """Verify get_settings returns cached instance on multiple calls."""
        settings1 = get_settings()
        settings2 = get_settings()

        # Should be the same object (cached)
        assert settings1 is settings2

    def test_get_settings_uses_env_variables(self):
        """Verify get_settings reads from environment variables."""
        with patch.dict(
            os.environ,
            {"DB_HOST": "test-host", "DB_PORT": "9999", **VALID_JWT_ENV},
            clear=True,
        ):
            settings = get_settings()

            assert settings.db_host == "test-host"
            assert settings.db_port == 9999

    def test_settings_has_default_values(self):
        """Verify Settings class has sensible defaults."""
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "DB_NAME": "test_db",
                **VALID_JWT_ENV,
            },
            clear=False,
        ):
            settings = Settings()
        assert settings.db_host == "localhost"
        assert settings.db_port == 5432
        assert settings.db_name == "test_db"

    def test_get_settings_async_url_construction(self):
        """Verify get_settings constructs async_database_url correctly."""
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "myhost",
                "DB_PORT": "5433",
                "DB_NAME": "testdb",
                "DB_USER": "testuser",
                "DB_PW": "testpass",
                **VALID_JWT_ENV,
            },
            clear=True,
        ):
            settings = get_settings()

            expected = "postgresql+asyncpg://testuser:testpass@myhost:5433/testdb"
            assert settings.async_database_url == expected

    def test_get_settings_sync_url_construction(self):
        """Verify get_settings constructs sync_database_url correctly."""
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "myhost",
                "DB_PORT": "5433",
                "DB_NAME": "testdb",
                "DB_USER": "testuser",
                "DB_PW": "testpass",
                **VALID_JWT_ENV,
            },
            clear=True,
        ):
            settings = get_settings()

            expected = "postgresql://testuser:testpass@myhost:5433/testdb"
            assert settings.sync_database_url == expected

    def test_settings_not_instantiated_at_module_level(self):
        """Verify no module-level 'settings = Settings()' exists."""
        import src.config as config_module

        # Check that 'settings' attribute doesn't exist or is not a Settings instance
        assert not hasattr(config_module, "settings") or not isinstance(
            getattr(config_module, "settings", None), Settings
        )


class TestJwtSecretSetting:
    """US-01: JWT_SECRET lives in Settings as a required SecretStr."""

    def test_jwt_secret_field_exists_as_secret_str(self):
        """AC 1: jwt_secret is declared on Settings and typed SecretStr."""
        with patch.dict(os.environ, VALID_JWT_ENV, clear=True):
            settings = Settings()

        assert isinstance(settings.jwt_secret, SecretStr)
        # SecretStr must never expose the plaintext through its own repr.
        assert repr(settings.jwt_secret) == "SecretStr('**********')"
        assert str(settings.jwt_secret) == "**********"

    def test_settings_with_jwt_secret_ok(self):
        """AC 5: a configured secret is stored verbatim and readable."""
        with patch.dict(os.environ, VALID_JWT_ENV, clear=True):
            settings = Settings()

        assert settings.jwt_secret.get_secret_value() == SENTINEL_JWT_SECRET

    def test_missing_jwt_secret_raises_value_error(self):
        """AC 2: no JWT_SECRET in the environment -> ValueError on startup."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                get_settings()

        assert "JWT_SECRET is required" in str(excinfo.value)

    def test_missing_jwt_secret_raises_on_direct_construction(self):
        """AC 2: the raw constructor fails too (ValidationError is a ValueError)."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                Settings()

        assert isinstance(excinfo.value, ValidationError)
        assert "JWT_SECRET is required" in str(excinfo.value)

    def test_empty_jwt_secret_raises_value_error(self):
        """AC 2: JWT_SECRET='' is rejected -- an empty key signs nothing usable."""
        with patch.dict(os.environ, {"JWT_SECRET": ""}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                Settings()

        assert "JWT_SECRET is required" in str(excinfo.value)

    def test_whitespace_only_jwt_secret_raises_value_error(self):
        """AC 2: blank padding is not a secret either."""
        with patch.dict(os.environ, {"JWT_SECRET": "   \t "}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                Settings()

        assert "JWT_SECRET is required" in str(excinfo.value)

    def test_jwt_secret_has_no_usable_default(self):
        """AC 2/3: the declared default is a rejected sentinel, not a working key.

        Guards against somebody re-introducing ``SecretStr("dev-secret-...")``
        or relaxing ``_validate_jwt`` back into a soft warning.
        """
        annotation = Settings.model_fields["jwt_secret"]
        default = annotation.default

        assert isinstance(default, SecretStr)
        assert default.get_secret_value() == ""

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                Settings()

    def test_jwt_secret_too_short_raises_value_error(self):
        """US-01 MEDIUM: JWT_SECRET < 32 chars is rejected (brute-force protection)."""
        short_secret = "a" * 31
        with patch.dict(os.environ, {"JWT_SECRET": short_secret}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                Settings()

        assert "JWT_SECRET must be at least 32 characters" in str(excinfo.value)

    def test_jwt_secret_old_default_rejected(self):
        """US-01 MEDIUM: the pre-US-01 default value is blocklisted."""
        old_default = "dev-secret-change-in-production"
        with patch.dict(os.environ, {"JWT_SECRET": old_default}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                Settings()

        assert "JWT_SECRET must not be the old default value" in str(excinfo.value)

    def test_get_settings_error_message_names_field_not_value(self):
        """AC 2/7: startup error is actionable but never quotes the secret."""
        env = {
            "JWT_SECRET": SENTINEL_JWT_SECRET,
            "DB_PW": SENTINEL_DB_PW,
            # SMTP_USER set without password/from-address -> SMTP validator fires.
            "SMTP_USER": "ops@example.com",
            "SMTP_PASSWORD": "",
            "SMTP_FROM_EMAIL": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as excinfo:
                get_settings()

        message = str(excinfo.value)
        assert message.startswith("Invalid configuration: ")
        assert "smtp_password" in message
        assert SENTINEL_JWT_SECRET not in message
        assert SENTINEL_DB_PW not in message


class TestJwtSecretLeakRegression:
    """US-01 AC 7: no log output may ever contain the JWT secret, even on crash."""

    def test_secret_never_in_validation_error_message(self):
        """Raw ValidationError must not quote the plaintext (SecretStr masks it)."""
        env = {
            "JWT_SECRET": SENTINEL_JWT_SECRET,
            "DB_PW": SENTINEL_DB_PW,
            "SMTP_USER": "ops@example.com",
            "SMTP_PASSWORD": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as excinfo:
                Settings()

        rendered = str(excinfo.value)
        dumped = repr(excinfo.value.errors())

        assert SENTINEL_JWT_SECRET not in rendered
        assert SENTINEL_DB_PW not in rendered
        assert SENTINEL_JWT_SECRET not in dumped
        assert SENTINEL_DB_PW not in dumped

    def test_secret_never_in_traceback(self):
        """Formatted traceback of the startup crash must stay secret-free."""
        with patch.dict(os.environ, {"DB_PW": SENTINEL_DB_PW}, clear=True):
            try:
                Settings()
            except ValidationError:
                tb = traceback.format_exc()

        assert SENTINEL_DB_PW not in tb
        assert SENTINEL_JWT_SECRET not in tb

    def test_secret_never_in_startup_crash_log(self, capfd):
        """Simulated startup crash: nothing secret may reach stdout/stderr.

        Reproduces what uvicorn/docker logs when ``get_settings()`` fails at
        import time. Relies on the ``raise ... from None`` chain in
        ``get_settings``: without it, the chained ValidationError context
        (which carries ``input_value``) would be printed as well.
        """
        env = {
            "JWT_SECRET": SENTINEL_JWT_SECRET,
            "DB_PW": SENTINEL_DB_PW,
            "SMTP_USER": "ops@example.com",
            "SMTP_PASSWORD": "",
        }
        with patch.dict(os.environ, env, clear=True):
            try:
                get_settings()
            except ValueError as exc:
                print(str(exc))
                traceback.print_exc()

        captured = capfd.readouterr()
        haystack = captured.out + captured.err

        assert SENTINEL_JWT_SECRET not in haystack
        assert SENTINEL_DB_PW not in haystack
        # Sanity: the crash really happened and did log something actionable.
        assert "Invalid configuration" in haystack

    def test_get_settings_suppresses_original_exception_context(self):
        """The ``from None`` chain is the mechanism -- assert it stays in place."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as excinfo:
                get_settings()

        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__ is True

    def test_settings_repr_and_json_dump_hide_secret(self):
        """SecretStr must stay hidden in repr(), str() and JSON serialisation."""
        with patch.dict(os.environ, VALID_JWT_ENV, clear=True):
            settings = Settings()

        assert SENTINEL_JWT_SECRET not in repr(settings)
        assert SENTINEL_JWT_SECRET not in str(settings)
        assert SENTINEL_JWT_SECRET not in settings.model_dump_json()
        assert SENTINEL_DB_PW not in settings.model_dump_json()
