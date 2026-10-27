"""HTML email templates for transactional and security-notification emails.

This module serves two distinct consumers:

1. ``email_service.py`` — transactional ``build_*`` helpers that return
   ``(html, plain_text)`` tuples for multipart MIME messages.
2. ``security_notification_service.py`` — ``render_*`` helpers that return
   an HTML string for security-event notifications.

Design tokens for the transactional templates match the IT-Trainer frontend:
- Brand purple ``#863bff`` from the logo
- System sans-serif stack (matches ``index.css``)
- 600px max-width centered card on a soft purple-tinted background

Security-notification templates are intentionally self-contained: no external
CSS, no images, no links to external resources — most mail clients strip those
anyway, and security alerts should be readable offline.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Part 1: Security-notification templates (US-09)
# ---------------------------------------------------------------------------

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
                      Location</p>
                    <p style="margin:0 0 16px;font-size:14px;color:#111827;">
                      {location}</p>

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
    location: str
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
        location=ctx.location,
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
        location=ctx.location,
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
        location=ctx.location,
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
        location=ctx.location,
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
        location=ctx.location,
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
            location=ctx.location,
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
    location: str | None = None,
    extra: dict[str, str] | None = None,
) -> NotificationContext:
    """Convenience constructor that normalizes optional fields."""
    return NotificationContext(
        username=_sanitize(username),
        timestamp=_format_timestamp(timestamp),
        ip_address=_sanitize(ip_address),
        user_agent=_sanitize(user_agent),
        location=_sanitize(location),
        action=action,
        extra=dict(extra) if extra else {},
    )


# ---------------------------------------------------------------------------
# Part 2: Transactional email templates (US-04/US-08)
# ---------------------------------------------------------------------------

# Design tokens (sync with frontend ``src/index.css``)
BRAND_COLOR = "#863bff"
BG_COLOR = "#f4f3fb"
CARD_BG = "#ffffff"
TEXT_COLOR = "#171717"
DIM_TEXT = "#525252"
FOOTER_TEXT = "#737373"
_LINK_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif"
)

_FOOTER_HTML = (
    "If you didn't request this, you can safely ignore this email.<br>"
    "&copy; IT-Trainer &mdash; Practice hex, binary, decimal &amp; Unicode."
)


def _preheader(text: str) -> str:
    """Hidden preheader text shown in mail-client inbox previews."""
    style = (
        f"display:none;font-size:1px;color:{BG_COLOR};"
        "line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;"
    )
    return f'<div style="{style}">{text}</div>'


def _button(label: str, url: str) -> str:
    """CTA button using a table cell for Outlook compatibility."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin:0 auto;">'
        "<tr>"
        f'<td style="background-color:{BRAND_COLOR};border-radius:8px;">'
        f'<a href="{url}" target="_blank"'
        f' style="display:inline-block;padding:14px 32px;color:#ffffff;'
        f"text-decoration:none;font-weight:600;font-size:16px;"
        f'font-family:{_LINK_FONT};">'
        f"{label}"
        "</a>"
        "</td>"
        "</tr>"
        "</table>"
    )


def _page(
    *, title: str, preheader: str, inner: str, footer: str = _FOOTER_HTML
) -> str:
    """Wrap ``inner`` content in the shared page shell."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{title}</title>\n"
        "</head>\n"
        f'<body style="margin:0;padding:0;background-color:{BG_COLOR};'
        f"font-family:{_LINK_FONT};color:{TEXT_COLOR};\">\n"
        f"{_preheader(preheader)}\n"
        '<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="background-color:{BG_COLOR};">\n'
        '<tr><td align="center" style="padding:32px 16px;">\n'
        '<table role="presentation" width="600" cellpadding="0" '
        'cellspacing="0" style="max-width:600px;width:100%;">\n'
        # Logo header
        "<tr>\n"
        '<td style="padding:32px 40px 24px;text-align:center;">\n'
        f'<div style="display:inline-block;background-color:{BRAND_COLOR};'
        "color:#ffffff;font-size:24px;line-height:1;padding:14px 20px;"
        "border-radius:12px;font-weight:700;letter-spacing:0.5px;"
        f'font-family:{_LINK_FONT};">'
        "&#9889; IT-Trainer"  # ⚡
        "</div>\n"
        "</td>\n"
        "</tr>\n"
        # Card
        "<tr>\n"
        f'<td style="background-color:{CARD_BG};border-radius:16px;'
        'box-shadow:0 1px 3px rgba(0,0,0,0.06);">\n'
        f"{inner}\n"
        "</td>\n"
        "</tr>\n"
        # Footer
        "<tr>\n"
        f'<td style="padding:32px 40px;text-align:center;color:{FOOTER_TEXT};'
        'font-size:12px;line-height:1.6;">\n'
        f"{footer}\n"
        "</td>\n"
        "</tr>\n"
        "</table>\n"
        "</td></tr>\n"
        "</table>\n"
        "</body>\n"
        "</html>"
    )


def _link_block(url: str) -> str:
    """Fallback 'copy this link' block shown under the CTA button."""
    return (
        f'<p style="margin:24px 0 0;color:{DIM_TEXT};font-size:14px;'
        'line-height:1.6;">\n'
        "Or copy and paste this link into your browser:\n"
        "</p>\n"
        f'<p style="margin:8px 0 0;font-size:13px;word-break:break-all;">\n'
        f'<a href="{url}" style="color:{BRAND_COLOR};">{url}</a>\n'
        "</p>"
    )


# ---------------------------------------------------------------------------
# Builders — each returns ``(html, plain_text)``
# ---------------------------------------------------------------------------


def build_verification_email(verify_url: str) -> tuple[str, str]:
    """Registration confirmation: verify the account's email address."""
    inner = (
        '<div style="padding:40px 40px 32px;">\n'
        f'<h1 style="margin:0 0 16px;font-size:24px;font-weight:700;'
        f'color:{TEXT_COLOR};">\n'
        "Welcome to IT-Trainer!\n"
        "</h1>\n"
        f'<p style="margin:0 0 24px;color:{DIM_TEXT};font-size:16px;'
        'line-height:1.6;">\n'
        "Thanks for signing up. Please confirm your email address to start\n"
        "practicing hex, binary, decimal and Unicode conversions.\n"
        "</p>\n"
        '<div style="text-align:center;padding:8px 0;">\n'
        f'{_button("Verify my email", verify_url)}\n'
        "</div>\n"
        f"{_link_block(verify_url)}\n"
        f'<p style="margin:24px 0 0;color:{DIM_TEXT};font-size:14px;'
        'line-height:1.6;">\n'
        f'This link expires in <strong style="color:{TEXT_COLOR};">'
        "24 hours</strong>.\n"
        "</p>\n"
        "</div>"
    )
    plain = (
        "Welcome to IT-Trainer!\n\n"
        "Please verify your email address by opening this link:\n"
        f"{verify_url}\n\n"
        "The link expires in 24 hours.\n\n"
        "If you didn't sign up, you can safely ignore this email."
    )
    html_out = _page(
        title="Verify your email",
        preheader="Welcome to IT-Trainer! Confirm your email to get started.",
        inner=inner,
    )
    return html_out, plain


def build_password_reset_email(reset_url: str) -> tuple[str, str]:
    """Password-reset email with a time-limited link."""
    inner = (
        '<div style="padding:40px 40px 32px;">\n'
        f'<h1 style="margin:0 0 16px;font-size:24px;font-weight:700;'
        f'color:{TEXT_COLOR};">\n'
        "Reset your password\n"
        "</h1>\n"
        f'<p style="margin:0 0 24px;color:{DIM_TEXT};font-size:16px;'
        'line-height:1.6;">\n'
        "We received a request to reset the password for your IT-Trainer\n"
        "account. Click the button below to choose a new password.\n"
        "</p>\n"
        '<div style="text-align:center;padding:8px 0;">\n'
        f'{_button("Reset password", reset_url)}\n'
        "</div>\n"
        f"{_link_block(reset_url)}\n"
        f'<p style="margin:24px 0 0;color:{DIM_TEXT};font-size:14px;'
        'line-height:1.6;">\n'
        f'This link expires in <strong style="color:{TEXT_COLOR};">'
        "1 hour</strong>.\n"
        "If you didn't request a reset, you can safely ignore this email "
        "&mdash;\n"
        "your password will remain unchanged.\n"
        "</p>\n"
        "</div>"
    )
    plain = (
        "Reset your password\n\n"
        "We received a request to reset your IT-Trainer password. "
        "Open this link to choose a new one:\n"
        f"{reset_url}\n\n"
        "The link expires in 1 hour.\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    html_out = _page(
        title="Reset your password",
        preheader="Reset your IT-Trainer password. Link expires in 1 hour.",
        inner=inner,
    )
    return html_out, plain


def build_email_change_verification(verify_url: str) -> tuple[str, str]:
    """Confirm a pending change to a new email address."""
    inner = (
        '<div style="padding:40px 40px 32px;">\n'
        f'<h1 style="margin:0 0 16px;font-size:24px;font-weight:700;'
        f'color:{TEXT_COLOR};">\n'
        "Confirm your new email\n"
        "</h1>\n"
        f'<p style="margin:0 0 24px;color:{DIM_TEXT};font-size:16px;'
        'line-height:1.6;">\n'
        "You requested to change the email address associated with your\n"
        "IT-Trainer account. Click the button below to confirm the new "
        "address.\n"
        "</p>\n"
        '<div style="text-align:center;padding:8px 0;">\n'
        f'{_button("Confirm new email", verify_url)}\n'
        "</div>\n"
        f"{_link_block(verify_url)}\n"
        f'<p style="margin:24px 0 0;color:{DIM_TEXT};font-size:14px;'
        'line-height:1.6;">\n'
        f'This link expires in <strong style="color:{TEXT_COLOR};">'
        "24 hours</strong>.\n"
        "If you didn't request this change, please secure your account "
        "immediately.\n"
        "</p>\n"
        "</div>"
    )
    plain = (
        "Confirm your new email address\n\n"
        "You requested to change the email on your IT-Trainer account. "
        "Open this link to confirm:\n"
        f"{verify_url}\n\n"
        "The link expires in 24 hours.\n\n"
        "If you didn't request this, please secure your account immediately."
    )
    html_out = _page(
        title="Confirm your new email",
        preheader="Confirm your new email address for IT-Trainer.",
        inner=inner,
    )
    return html_out, plain


# Human-readable copy for known security-action codes.
_SECURITY_ACTIONS: dict[str, tuple[str, str]] = {
    "password_reset": (
        "Your password was reset",
        "The password for your IT-Trainer account was reset. If you made "
        "this change, no further action is needed.",
    ),
    "password_change": (
        "Your password was changed",
        "The password for your IT-Trainer account was changed. If you made "
        "this change, no further action is needed.",
    ),
    "email_changed": (
        "Your email address was changed",
        "The email address on your IT-Trainer account was changed. You "
        "will receive future notifications at the new address.",
    ),
    "username_changed": (
        "Your username was changed",
        "The username on your IT-Trainer account was changed.",
    ),
    "refresh_token_replay": (
        "Suspicious sign-in detected",
        "A previously issued session token was reused, which may indicate "
        "that someone else has access to your account. Please change your "
        "password immediately.",
    ),
}

_DEFAULT_SECURITY: tuple[str, str] = (
    "Security-relevant account action",
    "A security-relevant action was performed on your IT-Trainer account.",
)


def build_security_notification(action: str) -> tuple[str, str]:
    """Informational notification about a security event on the account."""
    title, body_text = _SECURITY_ACTIONS.get(action, _DEFAULT_SECURITY)
    inner = (
        '<div style="padding:40px 40px 32px;">\n'
        f'<h1 style="margin:0 0 16px;font-size:24px;font-weight:700;'
        f'color:{TEXT_COLOR};">\n'
        f"{title}\n"
        "</h1>\n"
        f'<p style="margin:0 0 16px;color:{DIM_TEXT};font-size:16px;'
        'line-height:1.6;">\n'
        f"{body_text}\n"
        "</p>\n"
        f'<p style="margin:0;color:{DIM_TEXT};font-size:14px;'
        'line-height:1.6;">\n'
        "If this wasn't you, please change your password immediately and\n"
        "contact support.\n"
        "</p>\n"
        "</div>"
    )
    plain = (
        f"{title}\n\n"
        f"{body_text}\n\n"
        "If this wasn't you, please change your password immediately."
    )
    html_out = _page(
        title="Security notification",
        preheader=title,
        inner=inner,
    )
    return html_out, plain
