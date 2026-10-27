"""Parse raw User-Agent strings into human-readable browser/device descriptions.

Security-notification emails show the client's browser and device so the
recipient can spot an unfamiliar login. A raw User-Agent string like
``Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/...`` is
unreadable for most users — this module turns it into ``Chrome on macOS``.

Failures (malformed UA, missing library) fall back to the raw string or
``"unknown"`` — a broken parser must never break the notification flow.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def parse_user_agent(raw_user_agent: str | None) -> str:
    """Return a short ``Browser on Device`` description for ``raw_user_agent``.

    Returns ``"unknown"`` when the input is empty/None or when the UA
    library cannot determine at least a browser family.
    """
    if not raw_user_agent:
        return "unknown"

    cleaned = raw_user_agent.strip()
    if not cleaned:
        return "unknown"

    try:
        from user_agents import parse
    except ImportError:  # pragma: no cover — library is a declared dependency
        logger.warning("user-agents library not installed; falling back to raw UA")
        return cleaned

    try:
        ua = parse(cleaned)
        browser = ua.browser.family
        device = ua.device.family
        os_family = ua.os.family

        # "Other" is the library's placeholder for unrecognised values.
        # Surface whatever we *do* know instead of "Other on Other".
        if browser == "Other" and device == "Other":
            return cleaned

        if browser == "Other":
            return device

        # Desktop browsers report device="Other" but have a useful OS.
        # Use OS as the platform label so "Firefox on Windows" is shown
        # instead of just "Firefox".
        if device == "Other" or device == "Spider":
            if os_family and os_family != "Other":
                return f"{browser} on {os_family}"
            return browser

        return f"{browser} on {device}"
    except Exception:  # noqa: BLE001 — parser must never break the flow
        logger.warning("Failed to parse user-agent: %r", cleaned[:80])
        return cleaned
