"""US-10: Email Service.

Provides email sending functionality with a Protocol interface for testing
and production implementations.

- EmailService: Protocol interface for email sending
- MockEmailService: In-memory implementation for testing
- SMTPEmailService: Production implementation using SMTP (Strato)
- get_email_service: Factory function with FastAPI dependency injection
"""

import logging
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

import aiosmtplib
from src.config import get_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmailService(Protocol):
    """Protocol interface for email sending.

    Defines the contract for email service implementations.
    Allows easy mocking for tests and swapping implementations.
    """

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Send an email.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (plain text)

        Returns:
            True if email was sent successfully, False otherwise
        """
        ...


class MockEmailService:
    """Mock email service for testing.

    Stores sent emails in memory for test verification.
    No actual emails are sent.
    """

    def __init__(self):
        """Initialize MockEmailService with empty sent_emails list."""
        self.sent_emails: list[dict] = []

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Store email in memory.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body

        Returns:
            True (always succeeds)
        """
        self.sent_emails.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
            }
        )
        return True

    def clear(self) -> None:
        """Clear all stored emails."""
        self.sent_emails.clear()


class SMTPEmailService:
    """Production email service using SMTP.

    Sends emails via SMTP server (Strato).
    Configuration from environment variables.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_email: str,
    ):
        """Initialize SMTPEmailService with SMTP configuration.

        Args:
            host: SMTP server hostname
            port: SMTP server port (typically 587 for TLS)
            user: SMTP authentication username
            password: SMTP authentication password
            from_email: Sender email address
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_email = from_email

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Send email via SMTP.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (plain text)

        Returns:
            True if email was sent successfully, False otherwise
        """
        try:
            # Create email message
            message = EmailMessage()
            message["From"] = self.from_email
            message["To"] = to
            message["Subject"] = subject
            message.set_content(body)

            # Send via SMTP with TLS
            await aiosmtplib.send(
                message,
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                use_tls=True,
            )

            logger.info(f"Email sent successfully to {to}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to}: {e}")
            return False


def get_email_service() -> EmailService:
    """Factory function to get EmailService instance.

    Returns MockEmailService in test environment,
    SMTPEmailService in production.

    Returns:
        EmailService instance
    """
    settings = get_settings()

    # Check if we're in test environment
    if settings.db_name == "test_db":
        return MockEmailService()

    # Production: use SMTP
    return SMTPEmailService(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_password,
        from_email=settings.smtp_from_email,
    )
