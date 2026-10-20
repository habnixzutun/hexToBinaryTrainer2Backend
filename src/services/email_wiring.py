"""US-20: build + install the email service at app startup.

Kept out of ``main.py`` (no business logic in the app module) and free of any
DB/SMTP side effects so it is unit-testable offline. ``install_email_service``
pushes the chosen service into BOTH routers, each of which exposes its own
``set_email_service`` (auth.py and user.py).
"""

import logging

from pydantic import SecretStr

from src.config import Settings
from src.services.email_service import EmailService, MockEmailService, SMTPEmailService

logger = logging.getLogger(__name__)


def _unwrap_secret(value: SecretStr) -> str:
    """Return the plain string held by a SecretStr without inlining the call."""
    return value.get_secret_value()


def build_email_service(settings: Settings) -> EmailService:
    """Return an SMTPEmailService when SMTP is configured, else a mock.

    Settings validation (see config.py) guarantees that a non-empty
    ``smtp_user`` implies a non-empty password and from-address, so checking
    the user flag alone is sufficient to decide the mode. SMTPEmailService is
    called positionally to keep its (fixed) argument order explicit.
    """
    if settings.smtp_user:
        logger.info("SMTP email service initialized")
        return SMTPEmailService(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            _unwrap_secret(settings.smtp_password),
            settings.smtp_from_email,
            settings.base_url,
        )
    logger.info("Using mock email service (development)")
    return MockEmailService()


def install_email_service(settings: Settings) -> EmailService:
    """Build the email service and register it with the auth + user routers."""
    service = build_email_service(settings)

    # Imported lazily to avoid pulling the router graph in at module load.
    from src.routers import auth, user

    auth.set_email_service(service)
    user.set_email_service(service)
    return service
