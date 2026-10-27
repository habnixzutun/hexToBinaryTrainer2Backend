"""US-09: Security notification delivery.

The ``SecurityNotificationService`` is the single entry point for turning
an audit action into a user-facing email. Callers hand it a user id and
an action string; it looks up the user, picks the right template,
renders the HTML and pushes the email through the ``EmailService``.

Failures are swallowed (and logged) — a broken mail pipeline must never
break the security-relevant flow that triggered the notification in the
first place. The audit log is the source of truth; the email is a
courtesy copy.
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import User
from src.services.device_info_service import parse_user_agent
from src.services.email_service import EmailService
from src.services.email_templates import (
    NotificationContext,
    build_context,
    render_security_notification,
)
from src.services.geolocation_service import GeolocationService, NullGeolocationService
from src.services.rate_limit_service import RateLimitService

logger = logging.getLogger(__name__)

# RFC-5322-lite email validation: good enough to catch typos and block
# header-injection payloads, without being so strict that legitimate
# addresses get rejected. The real deliverability check happens at the
# SMTP level.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Rate-limit budget: 10 notifications per (user, action) per hour.
# Tight enough to make notification-spam unattractive, loose enough
# that a real password-mashing attacker still triggers at most one
# alert per action type per hour.
_NOTIFICATION_LIMIT = 10
_NOTIFICATION_WINDOW_SECONDS = 3600


def _validate_email(email: str | None) -> str | None:
    """Return ``email`` if it looks like a valid address, else ``None``.

    Used on every recipient address — both the one loaded from the DB
    and the ``recipient_override`` supplied by the caller — so that
    malformed or header-injection addresses never reach the MTA.
    """
    if not email:
        return None
    cleaned = email.strip()
    if not cleaned:
        return None
    if not _EMAIL_RE.match(cleaned):
        return None
    return cleaned


class SecurityNotificationService:
    """Dispatch security alert emails for audit actions."""

    def __init__(
        self,
        email_service: EmailService,
        session: AsyncSession,
        rate_limit_service: RateLimitService | None = None,
        geolocation_service: GeolocationService | None = None,
    ) -> None:
        self._email_service = email_service
        self._session = session
        self._rate_limit_service = rate_limit_service or RateLimitService(session)
        self._geolocation_service = geolocation_service or NullGeolocationService()

    async def _lookup_user_email(self, user_id: str) -> tuple[str, str] | None:
        """Return ``(email, username)`` for ``user_id``, or ``None``."""
        result = await self._session.execute(
            select(User.email, User.username).where(User.id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row.email, row.username

    async def send(
        self,
        *,
        user_id: str,
        action: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict | None = None,
        recipient_override: str | None = None,
    ) -> None:
        """Look up the user and send the matching security email.

        Parameters
        ----------
        user_id:
            Owner of the account. Used for the DB lookup unless
            ``recipient_override`` is set.
        action:
            Audit action name (e.g. ``password_reset``). Drives the
            template selection.
        ip_address / user_agent:
            Request context, shown in the email body so the user can
            spot an unfamiliar device.
        metadata:
            Free-form audit metadata. Templates pick out the keys they
            need (e.g. ``old_email``/``new_email`` for ``email_changed``).
        recipient_override:
            Send to a different address than the one on file — used for
            the ``email_changed`` notification, which must reach the
            *old* address (the user may no longer control the new one).
        """
        extra = dict(metadata) if metadata else {}

        if recipient_override:
            email = _validate_email(recipient_override)
            if email is None:
                logger.warning(
                    "Cannot send security notification: "
                    "invalid recipient_override=%r for user %s",
                    recipient_override,
                    user_id,
                )
                return
            username = extra.get("username", "user")
        else:
            user = await self._lookup_user_email(user_id)
            if user is None:
                logger.warning(
                    "Cannot send security notification: user %s not found",
                    user_id,
                )
                return
            raw_email, username = user
            email = _validate_email(raw_email)
            if email is None:
                logger.warning(
                    "Cannot send security notification: "
                    "invalid email=%r for user %s",
                    raw_email,
                    user_id,
                )
                return

        # DoS protection: cap notifications per user+action per hour.
        rate_key = f"security_notification:{user_id}:{action}"
        is_allowed = await self._rate_limit_service.check(
            key=rate_key,
            limit=_NOTIFICATION_LIMIT,
            window_seconds=_NOTIFICATION_WINDOW_SECONDS,
        )
        if not is_allowed:
            logger.warning(
                "Rate limit exceeded for security notification: "
                "user=%s action=%s (limit=%d/h)",
                user_id,
                action,
                _NOTIFICATION_LIMIT,
            )
            return

        ctx: NotificationContext = build_context(
            username=username,
            action=action,
            timestamp=datetime.now(UTC),
            ip_address=ip_address,
            user_agent=parse_user_agent(user_agent),
            location=await self._geolocation_service.lookup(ip_address),
            extra=extra,
        )

        subject, html_body = render_security_notification(action, ctx)

        try:
            await self._email_service.send_raw_email(
                to_email=email,
                subject=subject,
                html_body=html_body,
                context={
                    "user_id": user_id,
                    "action": action,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                },
            )
        except Exception:  # noqa: BLE001 — notification must not break the flow
            logger.exception(
                "Failed to send security notification for action=%s user=%s",
                action,
                user_id,
            )
