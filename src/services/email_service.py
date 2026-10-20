"""Email service Protocol and implementations."""

import asyncio
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Protocol

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

    def _send_sync(self, to_email: str, subject: str, body: str) -> None:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = to_email

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

    async def _send(self, to_email: str, subject: str, body: str) -> None:
        await asyncio.wait_for(
            asyncio.to_thread(self._send_sync, to_email, subject, body),
            timeout=SMTP_TIMEOUT_SECONDS * 2,
        )

    async def send_verification_email(self, to_email: str, token: str) -> None:
        verify_url = f"{self._base_url}/verify-email?token={token}"
        await self._send(
            to_email,
            "Verify your email",
            f"Click here to verify your email: {verify_url}",
        )

    async def send_password_reset_email(self, to_email: str, token: str) -> None:
        reset_url = f"{self._base_url}/reset-password?token={token}"
        await self._send(
            to_email,
            "Reset your password",
            f"Click here to reset your password: {reset_url}",
        )

    async def send_email_change_verification(self, to_email: str, token: str) -> None:
        verify_url = f"{self._base_url}/verify-email-change?token={token}"
        await self._send(
            to_email,
            "Confirm your new email address",
            f"Click here to confirm your new email address: {verify_url}",
        )

    async def send_security_notification_email(
        self, to_email: str, action: str
    ) -> None:
        await self._send(
            to_email,
            "Security notification",
            f"A security-relevant action was performed on your account: {action}",
        )
