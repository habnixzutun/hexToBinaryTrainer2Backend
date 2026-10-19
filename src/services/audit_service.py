"""US-10: Audit Service.

Provides audit logging and security notifications for critical actions.

- log_audit_action: Logs security events to user_audit_log table
- send_security_notification: Sends email notifications for critical actions
"""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from src.services.email_service import EmailService
from uuid_utils import uuid7

logger = logging.getLogger(__name__)


async def log_audit_action(
    session: AsyncSession,
    user_id: str,
    action: str,
    ip_address: str,
    user_agent: str,
    metadata: dict | None = None,
) -> None:
    """Log a security action to the audit log.

    Args:
        session: Database session
        user_id: ID of the user performing the action
        action: Type of action (e.g., "password_change", "email_change")
        ip_address: IP address of the request
        user_agent: User agent string
        metadata: Optional additional data (JSON-serializable dict)
    """
    try:
        # Convert metadata to JSON string
        metadata_json = json.dumps(metadata) if metadata else None

        # Insert audit log entry
        from sqlalchemy import text

        await session.execute(
            text(
                """
                INSERT INTO user_audit_log
                (id, user_id, action, ip_address, user_agent, metadata_json, timestamp)
                VALUES (:id, :user_id, :action, :ip_address, :user_agent,
                        :metadata_json, :timestamp)
                """
            ),
            {
                "id": str(uuid7()),
                "user_id": user_id,
                "action": action,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "metadata_json": metadata_json,
                "timestamp": datetime.now(UTC),
            },
        )

        logger.info(f"Audit log created for user {user_id}: {action}")

    except Exception as e:
        # Log error but don't fail the operation
        logger.error(f"Failed to create audit log: {e}")


async def send_security_notification(
    email_service: EmailService,
    email: str,
    action: str,
    timestamp: datetime,
    ip_address: str,
) -> None:
    """Send a security notification email.

    Args:
        email_service: Email service instance
        email: Recipient email address
        action: Type of action (e.g., "password change")
        timestamp: When the action occurred
        ip_address: IP address where action was performed
    """
    try:
        # Format timestamp for email
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

        subject = f"Security Alert: {action.capitalize()}"

        body = f"""
Your {action} was performed on {timestamp_str} from IP {ip_address}.

If you did not perform this action, please contact support immediately.

Best regards,
HexToBinaryTrainer2 Team
""".strip()

        await email_service.send_email(email, subject, body)

        logger.info(f"Security notification sent to {email}: {action}")

    except Exception as e:
        # Log error but don't fail the operation
        logger.error(f"Failed to send security notification: {e}")
