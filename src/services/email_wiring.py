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
from src.services.geolocation_service import (
    GeolocationService,
    IpApiGeolocationService,
    NullGeolocationService,
)

logger = logging.getLogger(__name__)

# Module-level reference so non-router code (e.g. audit_service) can
# resolve the installed service without a dependency-injection round-trip.
_default_service: EmailService = MockEmailService()
_default_geolocation_service: GeolocationService = NullGeolocationService()


def get_email_service() -> EmailService:
    """Return the application-wide EmailService (set at startup)."""
    return _default_service


def get_geolocation_service() -> GeolocationService:
    """Return the application-wide GeolocationService (set at startup)."""
    return _default_geolocation_service


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
            settings.frontend_url,
        )
    logger.info("Using mock email service (development)")
    return MockEmailService()


def install_email_service(settings: Settings) -> EmailService:
    """Build the email service and register it with the auth + user routers."""
    global _default_service, _default_geolocation_service
    service = build_email_service(settings)
    _default_service = service

    # Geolocation: use real IP-API when SMTP is configured (production),
    # otherwise use the null service (offline/dev — avoids external calls).
    if settings.smtp_user:
        _default_geolocation_service = IpApiGeolocationService()
        logger.info("Using ip-api.com geolocation service")
    else:
        _default_geolocation_service = NullGeolocationService()
        logger.info("Using null geolocation service (development)")

    # Imported lazily to avoid pulling the router graph in at module load.
    from src.routers import auth, user

    auth.set_email_service(service)
    user.set_email_service(service)
    return service
