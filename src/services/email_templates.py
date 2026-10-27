"""HTML email templates for transactional emails.

Each builder returns an ``(html, plain_text)`` tuple so the SMTP service
can send a ``multipart/alternative`` message: HTML-capable clients render
the styled version, plain-text fallbacks get the URL directly.

Design matches the IT-Trainer frontend:
- Brand purple ``#863bff`` from the logo
- System sans-serif stack (matches ``index.css``)
- 600px max-width centered card on a soft purple-tinted background
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Design tokens (sync with frontend ``src/index.css``)
# ---------------------------------------------------------------------------

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
    html = _page(
        title="Verify your email",
        preheader="Welcome to IT-Trainer! Confirm your email to get started.",
        inner=inner,
    )
    return html, plain


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
    html = _page(
        title="Reset your password",
        preheader="Reset your IT-Trainer password. Link expires in 1 hour.",
        inner=inner,
    )
    return html, plain


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
    html = _page(
        title="Confirm your new email",
        preheader="Confirm your new email address for IT-Trainer.",
        inner=inner,
    )
    return html, plain


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
    html = _page(
        title="Security notification",
        preheader=title,
        inner=inner,
    )
    return html, plain
