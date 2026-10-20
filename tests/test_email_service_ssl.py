"""Tests for SMTPEmailService port-based connection strategy.

Port 465 requires implicit TLS (smtplib.SMTP_SSL, no STARTTLS handshake).
Any other port (typically 587) uses plain SMTP + STARTTLS upgrade.
"""

import smtplib

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
        type(self).calls.append(("starttls", ()))

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


def test_port_465_uses_implicit_ssl_without_starttls(patched_smtplib):
    """Port 465 must use SMTP_SSL and skip the STARTTLS handshake."""
    fake_plain, fake_ssl = patched_smtplib

    _service(465)._send_sync("to@example.com", "Subject", "Body")

    assert fake_plain.calls == []
    assert _call_names(fake_ssl) == ["init", "login", "sendmail", "exit"]
    init_call = next(c for c in fake_ssl.calls if c[0] == "init")
    host, port, kwargs = init_call[1]
    assert (host, port) == ("smtp.example.com", 465)
    assert kwargs.get("timeout") == email_service.SMTP_TIMEOUT_SECONDS


def test_port_587_uses_plain_smtp_with_starttls(patched_smtplib):
    """Non-465 ports keep the SMTP + STARTTLS upgrade path."""
    fake_plain, fake_ssl = patched_smtplib

    _service(587)._send_sync("to@example.com", "Subject", "Body")

    assert fake_ssl.calls == []
    assert _call_names(fake_plain) == ["init", "starttls", "login", "sendmail", "exit"]


def test_sendmail_uses_configured_from_and_to(patched_smtplib):
    """Envelope sender/receiver come from the service configuration."""
    _, fake_ssl = patched_smtplib

    _service(465)._send_sync("recipient@example.com", "Subject", "Body")

    sendmail_call = next(c for c in fake_ssl.calls if c[0] == "sendmail")
    assert sendmail_call[1] == ("user@example.com", "recipient@example.com")
