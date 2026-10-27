"""Tests for IP-to-location geolocation services."""

import pytest
import httpx
from src.services.geolocation_service import (
    IpApiGeolocationService,
    NullGeolocationService,
    _is_private_ip,
)


# --- NullGeolocationService ---------------------------------------------------


@pytest.mark.asyncio
async def test_null_service_returns_unknown_for_any_ip():
    """NullGeolocationService always returns 'unknown'."""
    service = NullGeolocationService()
    assert await service.lookup("8.8.8.8") == "unknown"
    assert await service.lookup(None) == "unknown"
    assert await service.lookup("") == "unknown"


# --- IpApiGeolocationService --------------------------------------------------


@pytest.mark.asyncio
async def test_ipapi_returns_city_and_country(monkeypatch):
    """Successful lookup returns 'City, Country'."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"status": "success", "city": "Berlin", "country": "Germany"}

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "Berlin, Germany"


@pytest.mark.asyncio
async def test_ipapi_returns_country_only_when_no_city(monkeypatch):
    """When only country is available, return just the country."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"status": "success", "city": "", "country": "Germany"}

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "Germany"


@pytest.mark.asyncio
async def test_ipapi_returns_unknown_on_api_fail_status(monkeypatch):
    """API returning status=fail (e.g. reserved IP) yields 'unknown'."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"status": "fail"}

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "unknown"


@pytest.mark.asyncio
async def test_ipapi_returns_unknown_on_http_error(monkeypatch):
    """HTTP errors yield 'unknown' without raising."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        class _Resp:
            status_code = 500

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "server error", request=None, response=self  # noqa
                )

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "unknown"


@pytest.mark.asyncio
async def test_ipapi_returns_unknown_on_network_error(monkeypatch):
    """Network errors (timeouts, DNS) yield 'unknown' without raising."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        raise httpx.ConnectError("DNS failure")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "unknown"


@pytest.mark.asyncio
async def test_ipapi_skips_private_ip():
    """Private IPs (127.0.0.1, 192.168.x, etc.) skip the API call."""
    service = IpApiGeolocationService()
    # No monkeypatch needed — these should short-circuit before any HTTP call
    assert await service.lookup("127.0.0.1") == "unknown"
    assert await service.lookup("192.168.1.1") == "unknown"
    assert await service.lookup("10.0.0.1") == "unknown"


@pytest.mark.asyncio
async def test_ipapi_returns_unknown_for_none_or_empty():
    """None or empty IP returns 'unknown' without making an API call."""
    service = IpApiGeolocationService()
    assert await service.lookup(None) == "unknown"
    assert await service.lookup("") == "unknown"
    assert await service.lookup("   ") == "unknown"


@pytest.mark.asyncio
async def test_ipapi_returns_unknown_on_invalid_json(monkeypatch):
    """Malformed JSON response yields 'unknown' without raising."""
    service = IpApiGeolocationService()

    async def mock_get(self_client, url, params=None):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                raise ValueError("not json")

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await service.lookup("8.8.8.8")
    assert result == "unknown"


# --- _is_private_ip helper ---------------------------------------------------


def test_is_private_ip_loopback():
    """Loopback addresses are private."""
    assert _is_private_ip("127.0.0.1") is True
    assert _is_private_ip("::1") is True


def test_is_private_ip_rfc1918():
    """RFC 1918 addresses (10.x, 172.16.x, 192.168.x) are private."""
    assert _is_private_ip("10.0.0.1") is True
    assert _is_private_ip("172.16.0.1") is True
    assert _is_private_ip("192.168.1.1") is True


def test_is_private_ip_public():
    """Public IPs are not private."""
    assert _is_private_ip("8.8.8.8") is False
    assert _is_private_ip("1.1.1.1") is False


def test_is_private_ip_invalid():
    """Invalid IP strings return False (not private)."""
    assert _is_private_ip("not-an-ip") is False
    assert _is_private_ip("") is False
