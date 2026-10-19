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
    user_id: str,
    action: str,
    metadata: dict | None = None,
) -> None:
    """Placeholder for future security notification delivery (email, push, etc.)."""
    pass
