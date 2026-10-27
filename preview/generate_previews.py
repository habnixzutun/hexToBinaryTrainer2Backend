"""Generate preview HTML files for all email templates.

Run from the repo root:
    python preview/generate_previews.py

Opens the preview directory afterwards.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.services.email_templates import (  # noqa: E402
    build_email_change_verification,
    build_password_reset_email,
    build_security_notification,
    build_verification_email,
)

PREVIEW_DIR = REPO_ROOT / "preview"
SAMPLE_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9."
    "ZXhhbXBsZS11c2VyLWlkLXdpdGgtYS1yZWFsbG9vb29vb2ctdG9rZW4."
    "signature-placeholder"
)


def _write(name: str, html: str) -> None:
    path = PREVIEW_DIR / name
    path.write_text(html, encoding="utf-8")
    print(f"  ✓ {path.relative_to(REPO_ROOT)}")


def main() -> None:
    base = "https://it-trainer.app"
    verify = f"{base}/verify-email?token={SAMPLE_TOKEN}"
    reset = f"{base}/reset-password?token={SAMPLE_TOKEN}"
    change = f"{base}/verify-email-change?token={SAMPLE_TOKEN}"

    print("Generating email previews...")

    html_verify, _ = build_verification_email(verify)
    _write("registration_confirmation.html", html_verify)

    html_reset, _ = build_password_reset_email(reset)
    _write("password_reset.html", html_reset)

    html_change, _ = build_email_change_verification(change)
    _write("email_change.html", html_change)

    html_security, _ = build_security_notification("password_change")
    _write("security_notification.html", html_security)

    print(f"\nAll previews written to {PREVIEW_DIR}/")
    print("Open in browser to inspect:")
    for f in sorted(PREVIEW_DIR.glob("*.html")):
        print(f"  {f.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
