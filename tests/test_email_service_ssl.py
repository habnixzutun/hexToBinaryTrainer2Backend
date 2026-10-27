"""Tests for SMTPEmailService port-based connection strategy.

Port 465 requires implicit TLS (smtplib.SMTP_SSL, no STARTTLS handshake).
Any other port (typically 587) uses plain SMTP + STARTTLS upgrade.
Both paths must use a certificate-verifying SSL context.
"""

import smtplib
import ssl

import pytest
from src.services import email_service
from src.services.email_service import SMTPEmailService


class _FakeSMTPBase:
    """Records calls; usable as a context manager like smtplib.SMTP(_SSL)."""

    calls: list[tuple[str, tuple]] = []

    def __init__(self, host, port, **kwargs):
        type(self).calls.append(("init", (host, port, kwargs)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        type(self).calls.append(("exit", ()))
        return False

    def starttls(self, *args, **kwargs):
        type(self).calls.append(("starttls", (kwargs.get("context"),)))

    def login(self, user, password):
        type(self).calls.append(("login", (user, password)))

    def sendmail(self, from_addr, to_addr, msg):
        type(self).calls.append(("sendmail", (from_addr, to_addr)))


class _FakeSMTP(_FakeSMTPBase):
    calls: list[tuple[str, tuple]] = []


class _FakeSMTPSSL(_FakeSMTPBase):
    calls: list[tuple[str, tuple]] = []


@pytest.fixture
def patched_smtplib(monkeypatch):
    _FakeSMTP.calls = []
    _FakeSMTPSSL.calls = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    return _FakeSMTP, _FakeSMTPSSL


def _service(port: int) -> SMTPEmailService:
    return SMTPEmailService(
        host="smtp.example.com",
        port=port,
        username="user@example.com",
        password="secret",
        from_address="user@example.com",
        base_url="http://localhost:5173",
    )


def _call_names(fake_cls) -> list[str]:
    return [name for name, _ in fake_cls.calls]


def _assert_verifying_context(ctx) -> None:
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_port_465_uses_implicit_ssl_without_starttls(patched_smtplib):
    """Port 465 must use SMTP_SSL with a verifying context, no STARTTLS."""
    fake_plain, fake_ssl = patched_smtplib

    _service(465)._send_sync("to@example.com", "Subject", "<p>html</p>", "plain body")

    assert fake_plain.calls == []
    assert _call_names(fake_ssl) == ["init", "login", "sendmail", "exit"]
    init_call = next(c for c in fake_ssl.calls if c[0] == "init")
    host, port, kwargs = init_call[1]
    assert (host, port) == ("smtp.example.com", 465)
    assert kwargs.get("timeout") == email_service.SMTP_TIMEOUT_SECONDS
    _assert_verifying_context(kwargs.get("context"))


@pytest.mark.parametrize("port", [25, 587, 2525])
def test_non_465_ports_use_starttls_with_verifying_context(patched_smtplib, port):
    """Non-465 ports keep the SMTP + STARTTLS path with certificate verification."""
    fake_plain, fake_ssl = patched_smtplib

    _service(port)._send_sync("to@example.com", "Subject", "<p>html</p>", "plain body")

    assert fake_ssl.calls == []
    assert _call_names(fake_plain) == ["init", "starttls", "login", "sendmail", "exit"]
    starttls_call = next(c for c in fake_plain.calls if c[0] == "starttls")
    _assert_verifying_context(starttls_call[1][0])


def test_sendmail_uses_configured_from_and_to(patched_smtplib):
    """Envelope sender/receiver come from the service configuration."""
    _, fake_ssl = patched_smtplib

    _service(465)._send_sync(
        "recipient@example.com",
        "Subject",
        "<p>html</p>",
        "plain body",
    )

    sendmail_call = next(c for c in fake_ssl.calls if c[0] == "sendmail")
    assert sendmail_call[1] == ("user@example.com", "recipient@example.com")


def test_starttls_failure_still_closes_connection(patched_smtplib, monkeypatch):
    """A raising STARTTLS handshake must not leak the open socket."""
    fake_plain, _ = patched_smtplib

    def _failing_starttls(self, *args, **kwargs):
        type(self).calls.append(("starttls", (kwargs.get("context"),)))
        raise smtplib.SMTPNotSupportedError("STARTTLS refused")

    monkeypatch.setattr(_FakeSMTP, "starttls", _failing_starttls)

    with pytest.raises(smtplib.SMTPNotSupportedError):
        _service(587)._send_sync(
            "to@example.com",
            "Subject",
            "<p>html</p>",
            "plain body",
        )

    assert "exit" in _call_names(fake_plain)
