"""Email service Protocol and implementations."""

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol

from src.services.email_templates import (
    build_email_change_verification,
    build_password_reset_email,
    build_security_notification,
    build_verification_email,
)

SMTP_TIMEOUT_SECONDS = 10
IMPLICIT_SSL_PORT = 465


class EmailService(Protocol):
    async def send_verification_email(self, to_email: str, token: str) -> None: ...
    async def send_password_reset_email(self, to_email: str, token: str) -> None: ...
    async def send_email_change_verification(
        self, to_email: str, token: str
    ) -> None: ...
    async def send_security_notification_email(
        self, to_email: str, action: str
    ) -> None: ...


class MockEmailService:
    """In-memory email service for testing and development."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification_email(self, to_email: str, token: str) -> None:
        self.sent.append({"to": to_email, "token": token, "type": "verification"})

    async def send_password_reset_email(self, to_email: str, token: str) -> None:
        self.sent.append({"to": to_email, "token": token, "type": "password_reset"})

    async def send_email_change_verification(self, to_email: str, token: str) -> None:
        self.sent.append(
            {"to": to_email, "token": token, "type": "email_change_verification"}
        )

    async def send_security_notification_email(
        self, to_email: str, action: str
    ) -> None:
        self.sent.append(
            {"to": to_email, "action": action, "type": "security_notification"}
        )


class SMTPEmailService:
    """SMTP-backed email service for production.

    The blocking smtplib calls run in a worker thread with an explicit
    timeout so a slow or unreachable SMTP server can neither block the
    asyncio event loop nor hang forever.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        base_url: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_address
        self._base_url = base_url

    def _send_sync(
        self, to_email: str, subject: str, html: str, plain_text: str
    ) -> None:
        """Send a ``multipart/alternative`` email (HTML + plain-text fallback).

        Email clients that can render HTML show the styled template; clients
        that strip HTML fall back to the plain-text part.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = to_email
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with self._connect() as smtp:
            if self._port != IMPLICIT_SSL_PORT:
                # Verified STARTTLS upgrade: without an explicit context the
                # stdlib falls back to CERT_NONE, exposing credentials to MITM.
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._username, self._password)
            smtp.sendmail(self._from, to_email, msg.as_string())

    def _connect(self) -> smtplib.SMTP:
        """Open the SMTP connection matching the configured port.

        Port 465 uses implicit TLS (SMTP_SSL) with no STARTTLS handshake;
        any other port (typically 587) returns a plain connection that the
        caller upgrades via STARTTLS inside the managed scope.
        """
        if self._port == IMPLICIT_SSL_PORT:
            return smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=SMTP_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS)

    async def _send(
        self, to_email: str, subject: str, html: str, plain_text: str
    ) -> None:
        await asyncio.wait_for(
            asyncio.to_thread(
                self._send_sync, to_email, subject, html, plain_text
            ),
            timeout=SMTP_TIMEOUT_SECONDS * 2,
        )

    async def send_verification_email(self, to_email: str, token: str) -> None:
        verify_url = f"{self._base_url}/verify-email?token={token}"
        html, plain_text = build_verification_email(verify_url)
        await self._send(to_email, "Verify your email", html, plain_text)

    async def send_password_reset_email(self, to_email: str, token: str) -> None:
        reset_url = f"{self._base_url}/reset-password?token={token}"
        html, plain_text = build_password_reset_email(reset_url)
        await self._send(to_email, "Reset your password", html, plain_text)

    async def send_email_change_verification(self, to_email: str, token: str) -> None:
        verify_url = f"{self._base_url}/verify-email-change?token={token}"
        html, plain_text = build_email_change_verification(verify_url)
        await self._send(
            to_email, "Confirm your new email address", html, plain_text
        )

    async def send_security_notification_email(
        self, to_email: str, action: str
    ) -> None:
        html, plain_text = build_security_notification(action)
        await self._send(to_email, "Security notification", html, plain_text)
