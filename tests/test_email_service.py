"""Tests for US-10: Email Service.

Tests verify the EmailService interface, MockEmailService for testing,
and SMTPEmailService for production use.
"""

import pytest
from src.services.email_service import (
    EmailService,
    MockEmailService,
    SMTPEmailService,
    get_email_service,
)


class TestEmailServiceProtocol:
    """Test EmailService Protocol definition."""

    def test_email_service_is_protocol(self):
        """Test that EmailService is a Protocol."""
        assert hasattr(EmailService, "__protocol_attrs__")

    def test_email_service_has_send_email_method(self):
        """Test that EmailService defines send_email method."""
        assert hasattr(EmailService, "send_email")


class TestMockEmailService:
    """Test MockEmailService implementation."""

    @pytest.fixture
    def mock_service(self):
        """Create MockEmailService instance."""
        return MockEmailService()

    async def test_mock_email_service_stores_emails(self, mock_service):
        """Test that MockEmailService stores sent emails."""
        # Arrange
        to = "test@example.com"
        subject = "Test Subject"
        body = "Test Body"

        # Act
        await mock_service.send_email(to, subject, body)

        # Assert
        assert len(mock_service.sent_emails) == 1
        email = mock_service.sent_emails[0]
        assert email["to"] == to
        assert email["subject"] == subject
        assert email["body"] == body

    async def test_email_service_returns_true_on_success(self, mock_service):
        """Test that send_email returns True on success."""
        # Arrange
        to = "test@example.com"
        subject = "Test Subject"
        body = "Test Body"

        # Act
        result = await mock_service.send_email(to, subject, body)

        # Assert
        assert result is True

    async def test_mock_email_service_stores_multiple_emails(self, mock_service):
        """Test that MockEmailService stores multiple emails."""
        # Arrange & Act
        await mock_service.send_email("user1@example.com", "Subject 1", "Body 1")
        await mock_service.send_email("user2@example.com", "Subject 2", "Body 2")
        await mock_service.send_email("user3@example.com", "Subject 3", "Body 3")

        # Assert
        assert len(mock_service.sent_emails) == 3
        assert mock_service.sent_emails[0]["to"] == "user1@example.com"
        assert mock_service.sent_emails[1]["to"] == "user2@example.com"
        assert mock_service.sent_emails[2]["to"] == "user3@example.com"

    async def test_mock_email_service_can_be_cleared(self, mock_service):
        """Test that MockEmailService can be cleared."""
        # Arrange
        await mock_service.send_email("test@example.com", "Subject", "Body")
        assert len(mock_service.sent_emails) == 1

        # Act
        mock_service.clear()

        # Assert
        assert len(mock_service.sent_emails) == 0


class TestSMTPEmailService:
    """Test SMTPEmailService implementation."""

    @pytest.fixture
    def smtp_config(self):
        """SMTP configuration for tests."""
        return {
            "host": "smtp.example.com",
            "port": 587,
            "user": "test@example.com",
            "password": "test_password",
            "from_email": "noreply@example.com",
        }

    def test_smtp_email_service_initializes_with_config(self, smtp_config):
        """Test that SMTPEmailService initializes with configuration."""
        # Arrange & Act
        service = SMTPEmailService(**smtp_config)

        # Assert
        assert service.host == smtp_config["host"]
        assert service.port == smtp_config["port"]
        assert service.user == smtp_config["user"]
        assert service.password == smtp_config["password"]
        assert service.from_email == smtp_config["from_email"]

    async def test_smtp_email_service_returns_bool(self, smtp_config):
        """Test that SMTPEmailService.send_email returns bool."""
        # Arrange
        service = SMTPEmailService(**smtp_config)

        # Act
        result = await service.send_email(
            "recipient@example.com", "Test Subject", "Test Body"
        )

        # Assert
        assert isinstance(result, bool)


class TestGetEmailService:
    """Test get_email_service factory function."""

    def test_get_email_service_returns_email_service(self):
        """Test that get_email_service returns an EmailService instance."""
        # Arrange & Act
        service = get_email_service()

        # Assert
        assert isinstance(service, EmailService)

    def test_get_email_service_is_factory(self):
        """Test that get_email_service is a factory function."""
        # Arrange & Act
        service1 = get_email_service()
        service2 = get_email_service()

        # Assert
        assert service1 is not None
        assert service2 is not None

    def test_get_email_service_uses_environment_variables(self, monkeypatch):
        """Test that get_email_service reads from environment variables."""
        # Arrange
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USER", "test@test.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.com")

        # Act
        service = get_email_service()

        # Assert
        assert service is not None
