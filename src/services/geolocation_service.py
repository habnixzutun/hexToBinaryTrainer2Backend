"""IP-to-location lookup for security-notification emails.

Shows the geographic location of the client that triggered a security
event (password change, email change, etc.) so the account owner can
spot unfamiliar activity.

The default implementation uses the free ``ip-api.com`` API (no key
required, 45 req/min rate limit). All failures — network errors, rate
limits, private IPs — return ``"unknown"`` so a broken lookup never
blocks the notification flow.

A ``GeolocationService`` Protocol is defined so tests and deployments
can swap in a mock or a different provider (e.g. a local GeoLite2 DB).
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# Timeout per lookup — generous enough for slow networks but short
# enough to not stall the notification pipeline.
_LOOKUP_TIMEOUT_SECONDS = 3.0

# Returned for private/loopback/reserved IPs and on any failure.
_UNKNOWN = "unknown"


class GeolocationService(Protocol):
    """Resolve an IP address to a human-readable location string."""

    async def lookup(self, ip_address: str | None) -> str: ...


class IpApiGeolocationService:
    """Look up location via the free ``ip-api.com/json`` endpoint.

    Returns a ``City, Country`` string on success, ``"unknown"`` on any
    failure (network error, rate limit, non-success API status, private IP).
    """

    def __init__(
        self,
        base_url: str = "http://ip-api.com/json",
        timeout: float = _LOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout

    async def lookup(self, ip_address: str | None) -> str:
        """Return ``City, Country`` for ``ip_address``, or ``"unknown"``."""
        if not ip_address:
            return _UNKNOWN

        cleaned = ip_address.strip()
        if not cleaned:
            return _UNKNOWN

        # Private/loopback addresses always return "fail" from ip-api.com.
        # Skip the round-trip and short-circuit to "unknown".
        if _is_private_ip(cleaned):
            return _UNKNOWN

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    self._base_url,
                    params={"q": cleaned, "fields": "status,city,country"},
                )
                response.raise_for_status()
                data = response.json()

            if data.get("status") != "success":
                return _UNKNOWN

            city = data.get("city") or ""
            country = data.get("country") or ""

            if city and country:
                return f"{city}, {country}"
            if country:
                return country
            return _UNKNOWN
        except httpx.HTTPError:
            logger.warning(
                "Geolocation lookup failed for ip=%s", cleaned[:20], exc_info=True
            )
            return _UNKNOWN
        except (ValueError, KeyError):
            logger.warning(
                "Geolocation lookup returned invalid JSON for ip=%s",
                cleaned[:20],
                exc_info=True,
            )
            return _UNKNOWN


class NullGeolocationService:
    """Always returns ``"unknown"`` — used in tests and offline development."""

    async def lookup(self, ip_address: str | None) -> str:
        return _UNKNOWN


def _is_private_ip(ip: str) -> bool:
    """Return True for loopback, link-local and RFC-1918 private addresses."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_reserved
