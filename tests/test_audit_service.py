"""Tests for US-10: Audit Service.

Tests verify audit logging and security notifications for critical actions.
"""

from datetime import UTC, datetime

import pytest
from src.services.audit_service import (
    log_audit_action,
    send_security_notification,
)
from src.services.email_service import MockEmailService
from uuid_utils import uuid7


class TestLogAuditAction:
    """Test log_audit_action function."""

    @pytest.fixture
    async def user_id(self, db_session):
        """Create a test user in the database and return their ID."""
        from sqlalchemy import text

        user_id = str(uuid7())
        await db_session.execute(
            text(
                "INSERT INTO users (id, username, email) "
                "VALUES (:id, :username, :email)"
            ),
            {
                "id": user_id,
                "username": f"testuser_{user_id}",
                "email": f"test_{user_id}@example.com",
            },
        )
        await db_session.commit()
        return user_id

    @pytest.fixture
    async def db_session(self, async_engine):
        """Create a database session with fresh schema."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        async_session = sessionmaker(
            async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with async_session() as session:
            yield session

    async def test_log_audit_action_inserts_row(self, db_session, user_id):
        """Test that log_audit_action inserts a row into user_audit_log."""
        # Arrange
        action = "password_change"
        ip_address = "192.168.1.1"
        user_agent = "Mozilla/5.0"
        metadata = {"old_password_hash": "abc123"}

        # Act
        await log_audit_action(
            db_session, user_id, action, ip_address, user_agent, metadata
        )
        await db_session.commit()

        # Assert
        from sqlalchemy import text

        result = await db_session.execute(
            text("SELECT COUNT(*) FROM user_audit_log WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        count = result.scalar()
        assert count == 1

    async def test_log_audit_action_stores_correct_data(self, db_session, user_id):
        """Test that log_audit_action stores correct data."""
        # Arrange
        action = "email_change"
        ip_address = "10.0.0.1"
        user_agent = "Chrome/120.0"
        metadata = {"old_email": "old@example.com", "new_email": "new@example.com"}

        # Act
        await log_audit_action(
            db_session, user_id, action, ip_address, user_agent, metadata
        )
        await db_session.commit()

        # Assert
        from sqlalchemy import text

        result = await db_session.execute(
            text(
                "SELECT action, ip_address, user_agent, metadata_json "
                "FROM user_audit_log WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        row = result.fetchone()
        assert row[0] == action
        assert row[1] == ip_address
        assert row[2] == user_agent
        assert '"old_email": "old@example.com"' in row[3]

    async def test_log_audit_action_without_metadata(self, db_session, user_id):
        """Test that log_audit_action works without metadata."""
        # Arrange
        action = "username_change"
        ip_address = "172.16.0.1"
        user_agent = "Safari/17.0"

        # Act
        await log_audit_action(db_session, user_id, action, ip_address, user_agent)
        await db_session.commit()

        # Assert
        from sqlalchemy import text

        result = await db_session.execute(
            text("SELECT COUNT(*) FROM user_audit_log WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        count = result.scalar()
        assert count == 1


class TestSendSecurityNotification:
    """Test send_security_notification function."""

    @pytest.fixture
    def mock_email_service(self):
        """Create MockEmailService instance."""
        return MockEmailService()

    async def test_send_security_notification_sends_email(
        self, mock_email_service
    ):
        """Test that send_security_notification sends an email."""
        # Arrange
        email = "user@example.com"
        action = "password change"
        timestamp = datetime.now(UTC)
        ip_address = "192.168.1.100"

        # Act
        await send_security_notification(
            mock_email_service, email, action, timestamp, ip_address
        )

        # Assert
        assert len(mock_email_service.sent_emails) == 1
        sent_email = mock_email_service.sent_emails[0]
        assert sent_email["to"] == email
        assert action.capitalize() in sent_email["subject"]

    async def test_send_security_notification_includes_details(
        self, mock_email_service
    ):
        """Test that security notification includes timestamp and IP."""
        # Arrange
        email = "user@example.com"
        action = "email change"
        timestamp = datetime(2026, 10, 19, 14, 30, 0, tzinfo=UTC)
        ip_address = "10.0.0.50"

        # Act
        await send_security_notification(
            mock_email_service, email, action, timestamp, ip_address
        )

        # Assert
        sent_email = mock_email_service.sent_emails[0]
        assert ip_address in sent_email["body"]
        assert "2026" in sent_email["body"]

    async def test_send_security_notification_for_all_actions(
        self, mock_email_service
    ):
        """Test that security notifications work for all action types."""
        # Arrange
        email = "user@example.com"
        actions = [
            "password change",
            "password reset",
            "email change",
            "username change",
        ]
        timestamp = datetime.now(UTC)
        ip_address = "192.168.1.1"

        # Act
        for action in actions:
            await send_security_notification(
                mock_email_service, email, action, timestamp, ip_address
            )

        # Assert
        assert len(mock_email_service.sent_emails) == 4
        for i, action in enumerate(actions):
            assert action.capitalize() in mock_email_service.sent_emails[i]["subject"]
