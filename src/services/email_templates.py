"""US-09: HTML email templates for security notifications.

Each template is rendered with a common set of variables
(``username``, ``timestamp``, ``ip_address``, ``user_agent``, ``action``)
plus any action-specific extras. The ``render_*`` helpers return an HTML
string ready to be passed to the email service.

Templates are intentionally small and self-contained: no external CSS,
no images, no links to external resources — most mail clients strip those
anyway, and security alerts should be readable offline.
"""

import html
from dataclasses import dataclass
from datetime import UTC, datetime

# Human-readable labels and subjects per action. Keeping the mapping in
# one place means adding a new action only requires extending this table
# plus a matching ``render_*`` function.
_ACTION_LABEL: dict[str, str] = {
    "password_reset": "Password Reset",
    "refresh_token_replay_detected": "Suspicious Login Attempt (Replay Detected)",
    "email_changed": "Email Address Changed",
    "username_changed": "Username Changed",
    "password_change": "Password Changed",
}

_ACTION_SUBJECT: dict[str, str] = {
    "password_reset": "Security Alert: Password Reset on your account",
    "refresh_token_replay_detected": (
        "Security Alert: Suspicious activity detected on your account"
    ),
    "email_changed": "Security Alert: Email Address Changed on your account",
    "username_changed": "Security Alert: Username Changed on your account",
    "password_change": "Security Alert: Password Changed on your account",
}

_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f8;
font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
         style="background-color:#f4f6f8;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0"
               style="background-color:#ffffff;border-radius:8px;
                      overflow:hidden;box-shadow:0 2px 4px rgba(0,0,0,0.05);">
          <tr>
            <td style="background-color:#d32f2f;padding:20px 32px;">
              <h1 style="margin:0;color:#ffffff;font-size:20px;
                         font-weight:600;letter-spacing:0.3px;">
                &#128274; Security Alert
              </h1>
            </td>
          </tr>
          <tr>
            <td style="padding:32px;">
              <p style="margin:0 0 16px;font-size:16px;color:#1f2937;">
                Hello <strong>{username}</strong>,
              </p>
              <p style="margin:0 0 16px;font-size:15px;color:#374151;line-height:1.5;">
                {body}
              </p>
              <table role="presentation" cellspacing="0" cellpadding="0"
                     style="margin:24px 0;background-color:#f9fafb;
                            border-radius:6px;border:1px solid #e5e7eb;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
                      Action</p>
                    <p style="margin:0 0 16px;font-size:14px;color:#111827;
                              font-weight:600;">{action_label}</p>

                    <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
                      Time (UTC)</p>
                    <p style="margin:0 0 16px;font-size:14px;color:#111827;">
                      {timestamp}</p>

                    <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
                      IP Address</p>
                    <p style="margin:0 0 16px;font-size:14px;color:#111827;">
                      {ip_address}</p>

                    <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
                      Browser / Device</p>
                    <p style="margin:0;font-size:14px;color:#111827;
                              word-break:break-word;">{user_agent}</p>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 16px;font-size:15px;color:#374151;line-height:1.5;">
                <strong>If this wasn't you, contact support immediately</strong>
                and consider changing your password.
              </p>
              <p style="margin:0;font-size:14px;color:#6b7280;">
                — The HexToBinary Trainer Team
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color:#f9fafb;padding:16px 32px;
                       border-top:1px solid #e5e7eb;">
              <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
                This is an automated security notification. Please do not
                reply to this email.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


@dataclass(frozen=True)
class NotificationContext:
    """Immutable bag of template variables for security notifications."""

    username: str
    timestamp: str
    ip_address: str
    user_agent: str
    action: str
    extra: dict[str, str]


def _format_timestamp(value: datetime | str | None) -> str:
    """Return an ISO-8601 UTC string for the email body."""
    if value is None:
        return "unknown"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


def _sanitize(value: str | None, fallback: str = "unknown") -> str:
    """Return a non-empty, HTML-escaped, whitespace-trimmed string.

    All user-controlled values flowing into the email templates pass
    through this function. The ``html.escape`` call with ``quote=True``
    neutralizes angle brackets and quotes so that injected markup
    (e.g. ``<script>`` tags, ``onerror`` handlers) is rendered inert.
    """
    if value is None:
        return fallback
    cleaned = str(value).strip()
    if not cleaned:
        return fallback
    return html.escape(cleaned, quote=True)


def _safe_extra(extra: dict[str, str], key: str, fallback: str = "unknown") -> str:
    """Return an HTML-escaped value from the ``extra`` dict.

    ``extra`` holds action-specific template variables (e.g. old_email,
    new_email, new_username). Every value must be escaped before being
    interpolated into the HTML body.
    """
    raw = extra.get(key)
    return _sanitize(raw, fallback)


def render_password_reset(ctx: NotificationContext) -> str:
    """Render the password-reset notification body."""
    body = (
        "We received a request to reset the password on your account. "
        "If you made this request, follow the instructions in the "
        "separate password-reset email. If you did not request a reset, "
        "someone may be trying to compromise your account."
    )
    return _BASE_TEMPLATE.format(
        subject=_ACTION_SUBJECT["password_reset"],
        username=ctx.username,
        body=body,
        action_label=_ACTION_LABEL["password_reset"],
        timestamp=ctx.timestamp,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )


def render_replay_detected(ctx: NotificationContext) -> str:
    """Render the refresh-token-replay notification body."""
    body = (
        "We detected a reuse of an already-invalidated session token on "
        "your account. As a precaution we have revoked <strong>all</strong> "
        "of your active sessions — you will need to sign in again on every "
        "device. If you did not trigger this, treat your credentials as "
        "compromised and change your password immediately."
    )
    return _BASE_TEMPLATE.format(
        subject=_ACTION_SUBJECT["refresh_token_replay_detected"],
        username=ctx.username,
        body=body,
        action_label=_ACTION_LABEL["refresh_token_replay_detected"],
        timestamp=ctx.timestamp,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )


def render_email_changed(ctx: NotificationContext) -> str:
    """Render the email-changed notification body.

    The alert goes to the *old* address so the user learns about a
    potentially malicious change even if they no longer control the new
    one. ``extra["old_email"]`` / ``extra["new_email"]`` are surfaced in
    the body for that reason.
    """
    old_email = _safe_extra(ctx.extra, "old_email")
    new_email = _safe_extra(ctx.extra, "new_email")
    body = (
        f"The email address on your account was changed from "
        f"<strong>{old_email}</strong> to "
        f"<strong>{new_email}</strong>. You will now receive all future "
        f"communication at the new address. If you did not make this "
        f"change, contact support immediately to restore access."
    )
    return _BASE_TEMPLATE.format(
        subject=_ACTION_SUBJECT["email_changed"],
        username=ctx.username,
        body=body,
        action_label=_ACTION_LABEL["email_changed"],
        timestamp=ctx.timestamp,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )


def render_username_changed(ctx: NotificationContext) -> str:
    """Render the username-changed notification body."""
    new_username = _safe_extra(ctx.extra, "new_username")
    body = (
        f"The username on your account was changed to "
        f"<strong>{new_username}</strong>. If you did not make this "
        f"change, contact support immediately."
    )
    return _BASE_TEMPLATE.format(
        subject=_ACTION_SUBJECT["username_changed"],
        username=ctx.username,
        body=body,
        action_label=_ACTION_LABEL["username_changed"],
        timestamp=ctx.timestamp,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )


def render_password_change(ctx: NotificationContext) -> str:
    """Render the password-change notification body."""
    body = (
        "The password on your account was changed successfully. "
        "All active sessions have been invalidated — you will need to "
        "sign in again on every device with the new password. "
        "If you did not make this change, contact support immediately."
    )
    return _BASE_TEMPLATE.format(
        subject=_ACTION_SUBJECT["password_change"],
        username=ctx.username,
        body=body,
        action_label=_ACTION_LABEL["password_change"],
        timestamp=ctx.timestamp,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )


def render_security_notification(
    action: str,
    ctx: NotificationContext,
) -> tuple[str, str]:
    """Render the HTML body for ``action`` and return ``(subject, html)``.

    Unknown actions fall back to a generic template — this keeps the
    notification pipeline resilient if a new audit action is added before
    a dedicated template exists.
    """
    renderer = {
        "password_reset": render_password_reset,
        "refresh_token_replay_detected": render_replay_detected,
        "email_changed": render_email_changed,
        "username_changed": render_username_changed,
        "password_change": render_password_change,
    }.get(action)

    if renderer is None:
        raw_label = _ACTION_LABEL.get(action, action.replace("_", " ").title())
        label = html.escape(raw_label, quote=True)
        subject = f"Security Alert: {raw_label} on your account"
        # The subject also appears inside <title> in the HTML body —
        # escape it there to prevent XSS via a crafted action name.
        safe_subject = html.escape(subject, quote=True)
        body = (
            f"A security-relevant action (<strong>{label}</strong>) was "
            f"performed on your account. If this was not you, contact "
            f"support immediately."
        )
        html_body = _BASE_TEMPLATE.format(
            subject=safe_subject,
            username=ctx.username,
            body=body,
            action_label=label,
            timestamp=ctx.timestamp,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return subject, html_body

    return _ACTION_SUBJECT[action], renderer(ctx)


def build_context(
    *,
    username: str,
    action: str,
    timestamp: datetime | str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    extra: dict[str, str] | None = None,
) -> NotificationContext:
    """Convenience constructor that normalizes optional fields."""
    return NotificationContext(
        username=_sanitize(username),
        timestamp=_format_timestamp(timestamp),
        ip_address=_sanitize(ip_address),
        user_agent=_sanitize(user_agent),
        action=action,
        extra=dict(extra) if extra else {},
    )
