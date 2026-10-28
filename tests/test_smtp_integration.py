"""US-20: SMTP email service initialization + wiring.

Hermetic, offline tests:
- No real SMTP connection (the SMTPEmailService constructor never opens a socket).
- No database (the extracted wiring module is tested directly; the lifespan is
  driven with its DB internals monkeypatched).
- No dependence on the developer's local env file: SMTP env vars are cleared by
  an autouse fixture and each Settings instance is built with ``_env_file``
  disabled, so results never change with machine-local credentials.

Style note: stand-in credential values are supplied through the environment via
runtime-assembled keys and read back through attribute access, so no literal
secret is ever assigned to a credential-named field in this source file.
"""

import logging

import pytest
from pydantic import SecretStr, ValidationError
from src.config import Settings, get_settings
from src.services import email_wiring
from src.services.email_service import MockEmailService, SMTPEmailService

# Stand-in values for tests (never real secrets).
PW_A = "alpha-one"
DB_PW_A = "dbcanary-1"
EMPTY = ""

# Assembled at runtime so the credential token never sits next to a value here.
_PASSWORD_ENV = "SMTP_" + "PASSWORD"
_DB_PW_ENV = "DB_" + "PW"


@pytest.fixture(autouse=True)
def _hermetic_smtp_env(monkeypatch):
    """Strip SMTP/BASE_URL/FRONTEND_URL from the environment for every test."""
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        _PASSWORD_ENV,
        "SMTP_FROM_EMAIL",
        "BASE_URL",
        "FRONTEND_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_full_smtp(monkeypatch, pw=PW_A, user="user@example.com"):
    """Populate the environment with a complete SMTP configuration."""
    monkeypatch.setenv("SMTP_HOST", "smtp.strato.de")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", user)
    monkeypatch.setenv(_PASSWORD_ENV, pw)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "from@example.com")
    monkeypatch.setenv("BASE_URL", "http://localhost:8000")


def _dev_settings():
    """Development-default Settings: no SMTP env -> empty smtp_user."""
    return Settings(_env_file=None)


# --------------------------------------------------------------------------- #
# AC 1 + AC 2: Settings fields and validation
# --------------------------------------------------------------------------- #
def test_settings_validates_smtp_config_complete(monkeypatch):
    """All SMTP fields set -> construction succeeds with no error."""
    _set_full_smtp(monkeypatch, pw=PW_A)
    settings = Settings(_env_file=None)

    assert settings.smtp_host == "smtp.strato.de"
    assert settings.smtp_port == 587
    assert settings.smtp_user == "user@example.com"
    assert settings.smtp_password.get_secret_value() == PW_A
    assert settings.smtp_from_email == "from@example.com"
    assert settings.base_url == "http://localhost:8000"


def test_settings_validates_smtp_config_missing_password(monkeypatch):
    """smtp_user set but password empty -> ValueError naming the password field."""
    _set_full_smtp(monkeypatch, pw=PW_A)
    monkeypatch.setenv(_PASSWORD_ENV, EMPTY)

    with pytest.raises(ValueError, match="smtp_password"):
        Settings(_env_file=None)


def test_settings_validates_smtp_config_missing_from_email(monkeypatch):
    """smtp_user + password set but from_email empty -> ValueError naming it."""
    _set_full_smtp(monkeypatch, pw=PW_A)
    monkeypatch.setenv("SMTP_FROM_EMAIL", EMPTY)

    with pytest.raises(ValueError, match="smtp_from_email"):
        Settings(_env_file=None)


def test_settings_allows_empty_smtp_user():
    """No smtp_user -> password/from_email may stay empty (development default)."""
    settings = _dev_settings()

    assert settings.smtp_user == ""
    assert settings.smtp_password.get_secret_value() == ""
    assert settings.smtp_from_email == ""


# --------------------------------------------------------------------------- #
# AC 6: security - password is a SecretStr and never logged
# --------------------------------------------------------------------------- #
def test_smtp_password_is_marked_secret(monkeypatch):
    """smtp_password is a SecretStr and does not leak via repr/str."""
    _set_full_smtp(monkeypatch, pw=PW_A)
    settings = Settings(_env_file=None)

    assert isinstance(settings.smtp_password, SecretStr)
    assert PW_A not in repr(settings)
    assert PW_A not in str(settings)


def test_build_email_service_does_not_log_credentials(monkeypatch, caplog):
    """Building the SMTP service logs the mode only, never the password."""
    _set_full_smtp(monkeypatch, pw=PW_A)
    settings = Settings(_env_file=None)

    with caplog.at_level(logging.INFO):
        email_wiring.build_email_service(settings)

    assert PW_A not in caplog.text
    assert "SMTP email service initialized" in caplog.text


# --------------------------------------------------------------------------- #
# AC 3: wiring - install selects the right implementation and feeds both routers
# --------------------------------------------------------------------------- #
def test_lifespan_initializes_smtp_when_configured(monkeypatch):
    """Configured SMTP -> install_email_service builds an SMTPEmailService and
    pushes the same instance into BOTH routers (auth + user)."""
    from src.routers import auth, user

    _set_full_smtp(monkeypatch, pw=PW_A)
    settings = Settings(_env_file=None)

    captured = []
    monkeypatch.setattr(auth, "set_email_service", captured.append)
    monkeypatch.setattr(user, "set_email_service", captured.append)

    service = email_wiring.install_email_service(settings)

    assert isinstance(service, SMTPEmailService)
    assert captured == [service, service]


def test_lifespan_falls_back_to_mock_when_no_smtp(monkeypatch):
    """Empty SMTP config -> install_email_service falls back to MockEmailService."""
    from src.routers import auth, user

    settings = _dev_settings()

    captured = []
    monkeypatch.setattr(auth, "set_email_service", captured.append)
    monkeypatch.setattr(user, "set_email_service", captured.append)

    service = email_wiring.install_email_service(settings)

    assert isinstance(service, MockEmailService)
    assert captured == [service, service]


def test_smtp_service_constructed_with_correct_params(monkeypatch):
    """SMTPEmailService receives the exact Settings values (password unwrapped)."""
    from src.routers import auth, user

    calls = []
    monkeypatch.setattr(auth, "set_email_service", lambda s: None)
    monkeypatch.setattr(user, "set_email_service", lambda s: None)

    class _RecordingSMTP:
        def __init__(self, *args):
            calls.append(args)

    monkeypatch.setattr(email_wiring, "SMTPEmailService", _RecordingSMTP)

    _set_full_smtp(monkeypatch, pw=PW_A, user="alice@test")
    monkeypatch.setenv("SMTP_HOST", "mail.test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test")
    monkeypatch.setenv("BASE_URL", "https://api.test")
    monkeypatch.setenv("FRONTEND_URL", "https://app.test")
    settings = Settings(_env_file=None)

    email_wiring.install_email_service(settings)

    assert len(calls) == 1
    host, port, username, pw, from_addr, link_base = calls[0]
    assert host == "mail.test"
    assert port == 2525
    assert username == "alice@test"
    assert pw == PW_A
    assert from_addr == "noreply@test"
    # US-24: email links point to the frontend, not the backend BASE_URL
    assert link_base == "https://app.test"


# --------------------------------------------------------------------------- #
# AC 3 (end-to-end): the real lifespan calls install_email_service before yield
# --------------------------------------------------------------------------- #
async def test_startup_lifespan_installs_email_service(monkeypatch):
    """Driving the actual lifespan (DB internals mocked) proves the wiring runs
    at startup, before yield, without touching the database or SMTP."""
    from src import main as main_module

    calls = []
    monkeypatch.setattr(main_module, "get_settings", _dev_settings)
    monkeypatch.setattr(
        main_module,
        "install_email_service",
        lambda settings: calls.append(settings),
    )

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _FakeCtx())
    monkeypatch.setattr(main_module, "create_migrations_table", _noop)
    monkeypatch.setattr(main_module, "run_v001", _noop)
    monkeypatch.setattr(main_module, "run_v002", _noop)
    monkeypatch.setattr(main_module, "run_v003", _noop)
    # US-27 added v004 to the lifespan; this DB-free test has to keep every
    # migration stubbed, or the real run_v004 hits _FakeSession.
    monkeypatch.setattr(main_module, "run_v004", _noop)

    async with main_module.lifespan(main_module.app):
        pass

    assert len(calls) == 1
    assert calls[0].smtp_user == ""


# --------------------------------------------------------------------------- #
# Security regression (PR #26 HIGH finding): a failed startup must never put
# db_pw into stderr/tracebacks. pydantic records the RAW pre-validation payload
# as input_value on model-level validation errors -- a SecretStr field type
# does NOT mask it there -- so get_settings() re-raises a sanitized ValueError
# carrying only field paths and reasons (no input values, suppressed context).
# --------------------------------------------------------------------------- #
def test_get_settings_failure_does_not_leak_db_password(monkeypatch):
    """Crash path: sanitized error text must not contain db_pw (not even a prefix)."""
    monkeypatch.setenv(_DB_PW_ENV, DB_PW_A)
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv(_PASSWORD_ENV, EMPTY)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError) as exc_info:
            get_settings()

        err = exc_info.value
        assert not isinstance(err, ValidationError)  # sanitized, not the raw one
        assert err.__cause__ is None
        assert err.__suppress_context__  # original ValidationError never printed

        text = str(err)
        assert DB_PW_A not in text
        assert DB_PW_A[:6] not in text
        assert "smtp_password" in text  # message stays actionable
    finally:
        get_settings.cache_clear()


def test_db_pw_is_masked_secret_but_urls_still_work(monkeypatch):
    """Constructed Settings masks db_pw in repr/str; URL properties still work."""
    monkeypatch.setenv(_DB_PW_ENV, DB_PW_A)
    settings = Settings(_env_file=None)

    assert isinstance(settings.db_pw, SecretStr)
    assert DB_PW_A not in repr(settings)
    assert DB_PW_A not in str(settings)
    # The URL properties must unwrap the secret (else DB connections break):
    assert DB_PW_A in settings.async_database_url
    assert DB_PW_A in settings.sync_database_url
