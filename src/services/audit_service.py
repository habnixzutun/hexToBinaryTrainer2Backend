"""Audit logging service for security-relevant actions."""

import json

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from src.models.auth import UserAuditLog


async def log_audit_action(
    session: AsyncSession,
    user_id: str,
    action: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict | None = None,
) -> None:
    entry = UserAuditLog(
        id=str(uuid7()),
        user_id=user_id,
        action=action,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    session.add(entry)


async def send_security_notification(
    session: AsyncSession,
    *,
    user_id: str,
    action: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict | None = None,
    recipient_override: str | None = None,
) -> None:
    """Dispatch a security notification email for the given audit action.

    Delegates to ``SecurityNotificationService`` which handles user lookup,
    template rendering, rate limiting, and email delivery. Failures are
    swallowed (logged) — a broken mail pipeline must never break the
    security-relevant flow that triggered the notification.
    """
    # Lazy import to avoid circular dependency at module load time
    from src.services.email_wiring import get_email_service
    from src.services.security_notification_service import SecurityNotificationService

    email_service = get_email_service()
    notification_service = SecurityNotificationService(
        email_service=email_service,
        session=session,
    )
    await notification_service.send(
        user_id=user_id,
        action=action,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=metadata,
        recipient_override=recipient_override,
    )
