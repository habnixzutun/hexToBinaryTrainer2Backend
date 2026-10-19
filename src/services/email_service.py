"""Email service Protocol and implementations."""

import smtplib
from email.mime.text import MIMEText
from typing import Protocol


class EmailService(Protocol):
    async def send_verification_email(self, to_email: str, token: str) -> None: ...


class MockEmailService:
    """In-memory email service for testing and development."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification_email(self, to_email: str, token: str) -> None:
        self.sent.append({"to": to_email, "token": token, "type": "verification"})


class SMTPEmailService:
    """SMTP-backed email service for production."""

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

    async def send_verification_email(self, to_email: str, token: str) -> None:
        verify_url = f"{self._base_url}/verify-email?token={token}"
        body = f"Click here to verify your email: {verify_url}"
        msg = MIMEText(body)
        msg["Subject"] = "Verify your email"
        msg["From"] = self._from
        msg["To"] = to_email

        with smtplib.SMTP(self._host, self._port) as smtp:
            smtp.starttls()
            smtp.login(self._username, self._password)
            smtp.sendmail(self._from, to_email, msg.as_string())
