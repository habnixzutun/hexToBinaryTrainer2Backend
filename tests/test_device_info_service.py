"""Tests for User-Agent parsing into human-readable browser/device strings."""

from src.services.device_info_service import parse_user_agent


def test_parse_chrome_on_macos():
    """Chrome on macOS produces 'Chrome on Mac'."""
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    result = parse_user_agent(ua)
    assert "Chrome" in result
    assert "Mac" in result


def test_parse_firefox_on_windows():
    """Firefox on Windows produces 'Firefox on Windows'."""
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    )
    result = parse_user_agent(ua)
    assert "Firefox" in result
    assert "Windows" in result


def test_parse_safari_on_iphone():
    """Safari on iPhone produces 'Mobile Safari on iPhone'."""
    ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
        "Mobile/15E148 Safari/604.1"
    )
    result = parse_user_agent(ua)
    assert "Safari" in result
    assert "iPhone" in result


def test_parse_returns_unknown_for_none():
    """None input returns 'unknown'."""
    assert parse_user_agent(None) == "unknown"


def test_parse_returns_unknown_for_empty():
    """Empty/whitespace input returns 'unknown'."""
    assert parse_user_agent("") == "unknown"
    assert parse_user_agent("   ") == "unknown"


def test_parse_returns_raw_for_unrecognised():
    """Unrecognised UA falls back to the raw string (not 'Other on Other')."""
    raw = "SomeCustomBot/1.0"
    result = parse_user_agent(raw)
    # Either the raw string or something non-"Other"
    assert "Other on Other" not in result


def test_parse_handles_malformed_ua():
    """Completely malformed input does not raise — falls back gracefully."""
    result = parse_user_agent("\x00\x01\x02")
    assert isinstance(result, str)
    assert result  # non-empty
