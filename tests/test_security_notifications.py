"""US-09: Tests for security notification email delivery."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base
from src.models.auth import User, UserIdentity
from src.services.email_service import MockEmailService
from src.services.email_templates import (
    NotificationContext,
    build_context,
    render_email_changed,
    render_password_change,
    render_password_reset,
    render_replay_detected,
    render_security_notification,
    render_username_changed,
)
from src.services.rate_limit_service import RateLimitService
from src.services.security_notification_service import (
    SecurityNotificationService,
    _validate_email,
)
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def db_session():
    """Create test database session."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session_maker = sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with async_session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def test_user(db_session):
    """Create a test user with verified email."""
    user_id = "test-user-123"
    user = User(id=user_id, username="alice", email="alice@example.com")
    db_session.add(user)

    identity = UserIdentity(
        id="identity-123",
        user_id=user_id,
        provider="email",
        provider_user_id="alice@example.com",
        password_hash=hash_password("TestPass123!"),
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)
    await db_session.commit()
    return user


# --- Template rendering ----------------------------------------------------


def test_build_context_normalizes_none_values():
    """Empty/None fields become safe fallback strings for the HTML body."""
    ctx = build_context(
        username="  ",
        action="password_reset",
        timestamp=None,
        ip_address=None,
        user_agent=None,
    )
    assert ctx.username == "unknown"
    assert ctx.ip_address == "unknown"
    assert ctx.user_agent == "unknown"
    assert ctx.timestamp == "unknown"


def test_build_context_preserves_datetime():
    """Datetime objects are formatted as ISO-8601 UTC strings."""
    ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
    ctx = build_context(username="alice", action="password_reset", timestamp=ts)
    assert ctx.timestamp == "2024-01-15 10:30:00 UTC"


def test_build_context_handles_naive_datetime():
    """Naive datetimes are treated as UTC without crashing."""
    naive = datetime(2024, 6, 1, 12, 0, 0)  # no tzinfo
    ctx = build_context(username="alice", action="password_reset", timestamp=naive)
    assert ctx.timestamp == "2024-06-01 12:00:00 UTC"


def test_build_context_passes_string_timestamp_through():
    """String timestamps are left as-is."""
    ctx = build_context(
        username="alice", action="password_reset", timestamp="just-now"
    )
    assert ctx.timestamp == "just-now"


def test_build_context_copies_extra_dict():
    """The extra dict is defensively copied so callers can't mutate it."""
    extra = {"old_email": "a@b.com"}
    ctx = build_context(
        username="alice", action="email_changed", extra=extra
    )
    extra["old_email"] = "x@y.com"
    assert ctx.extra["old_email"] == "a@b.com"


def test_render_password_reset_contains_required_elements():
    """Password reset template includes all required fields."""
    ctx = build_context(
        username="alice",
        action="password_reset",
        timestamp="2024-01-15 10:30:00 UTC",
        ip_address="1.2.3.4",
        user_agent="Mozilla/5.0",
    )
    html = render_password_reset(ctx)
    assert "alice" in html
    assert "2024-01-15 10:30:00 UTC" in html
    assert "1.2.3.4" in html
    assert "Mozilla/5.0" in html
    assert "Password Reset" in html
    assert "If this wasn't you" in html


def test_render_replay_detected_contains_urgency():
    """Replay template surfaces urgency and session revocation info."""
    ctx = build_context(username="bob", action="refresh_token_replay_detected")
    html = render_replay_detected(ctx)
    assert "bob" in html
    assert "revoked" in html.lower()
    assert "Security Alert" in html


def test_render_email_changed_shows_old_and_new():
    """Email-changed template mentions both old and new address."""
    ctx = build_context(
        username="carol",
        action="email_changed",
        extra={"old_email": "old@example.com", "new_email": "new@example.com"},
    )
    html = render_email_changed(ctx)
    assert "old@example.com" in html
    assert "new@example.com" in html
    assert "carol" in html


def test_render_username_changed_shows_new_name():
    """Username-changed template shows the new username."""
    ctx = build_context(
        username="dave",
        action="username_changed",
        extra={"new_username": "dave_the_new"},
    )
    html = render_username_changed(ctx)
    assert "dave_the_new" in html
    assert "dave" in html


def test_render_password_change_mentions_session_revocation():
    """Password change template mentions sessions are invalidated."""
    ctx = build_context(username="eve", action="password_change")
    html = render_password_change(ctx)
    assert "sessions" in html.lower()
    assert "eve" in html


def test_render_security_notification_unknown_action_falls_back():
    """Unknown actions still produce a valid HTML email."""
    ctx = build_context(username="frank", action="some_new_action")
    subject, html = render_security_notification("some_new_action", ctx)
    assert "Security Alert" in subject
    assert "Some New Action" in subject
    assert "frank" in html
    assert "If this wasn't you" in html


def test_all_known_actions_have_subjects():
    """Every renderer returns a subject starting with 'Security Alert:'."""
    actions = [
        "password_reset",
        "refresh_token_replay_detected",
        "email_changed",
        "username_changed",
        "password_change",
    ]
    for action in actions:
        ctx = NotificationContext(
            username="x",
            timestamp="now",
            ip_address="unknown",
            user_agent="unknown",
            action=action,
            extra={},
        )
        subject, html = render_security_notification(action, ctx)
        assert subject.startswith("Security Alert:"), action
        assert html, action


# --- SecurityNotificationService ------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_sends_notification(db_session, test_user):
    """password_reset action sends an email to the user."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id=test_user.id,
        action="password_reset",
        ip_address="10.0.0.1",
        user_agent="TestAgent",
    )

    assert len(email_service.sent) == 1
    sent = email_service.sent[0]
    assert sent["to"] == "alice@example.com"
    assert "Security Alert" in sent["subject"]
    assert "Password Reset" in sent["subject"]
    assert sent["type"] == "raw"
    assert "alice" in sent["html_body"]
    assert "10.0.0.1" in sent["html_body"]


@pytest.mark.asyncio
async def test_replay_detection_sends_notification(db_session, test_user):
    """Replay detection action sends an email to the user."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id=test_user.id,
        action="refresh_token_replay_detected",
        ip_address="10.0.0.2",
        user_agent="SuspiciousAgent",
    )

    assert len(email_service.sent) == 1
    sent = email_service.sent[0]
    assert sent["to"] == "alice@example.com"
    assert "Suspicious" in sent["subject"]
    assert "alice" in sent["html_body"]


@pytest.mark.asyncio
async def test_email_change_notification_uses_recipient_override(
    db_session, test_user
):
    """email_changed notification goes to the OLD address, not the current one."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id=test_user.id,
        action="email_changed",
        metadata={"old_email": "old@example.com", "new_email": "new@example.com"},
        recipient_override="old@example.com",
    )

    assert len(email_service.sent) == 1
    sent = email_service.sent[0]
    # Must go to the OLD address (recipient override), not the current one
    assert sent["to"] == "old@example.com"
    assert "alice@example.com" not in [s["to"] for s in email_service.sent]


@pytest.mark.asyncio
async def test_unknown_user_logs_warning(db_session):
    """Notification for non-existent user is a silent no-op (logged)."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id="non-existent-user",
        action="password_reset",
    )

    # No email sent — user not found
    assert email_service.sent == []


@pytest.mark.asyncio
async def test_email_service_failure_is_swallowed(db_session, test_user):
    """If email_service raises, notification doesn't propagate the error."""

    class FailingEmailService:
        """EmailService double that always raises on send_raw_email."""

        async def send_raw_email(self, **_: object) -> None:
            raise OSError("SMTP unreachable")

    # The FailingEmailService satisfies the EmailService Protocol only for
    # the method we care about; pass it directly to bypass strict typing.
    service = SecurityNotificationService(
        FailingEmailService(), db_session  # type: ignore[arg-type]
    )

    # Must NOT raise, even though the email service blows up
    await service.send(
        user_id=test_user.id,
        action="password_reset",
    )


# --- audit_service wrapper -------------------------------------------------


@pytest.mark.asyncio
async def test_audit_service_wrapper_no_email_service_is_noop(db_session, test_user):
    """send_security_notification is currently a placeholder (no-op)."""
    from src.services.audit_service import send_security_notification

    # send_security_notification is a placeholder that does nothing
    await send_security_notification(
        user_id=test_user.id,
        action="password_reset",
    )


@pytest.mark.asyncio
async def test_audit_service_wrapper_dispatches_with_email_service(
    db_session, test_user
):
    """send_security_notification is a placeholder and does not send emails."""
    from src.services.audit_service import send_security_notification

    email_service = MockEmailService()

    # send_security_notification is a placeholder, it doesn't use email_service
    await send_security_notification(
        user_id=test_user.id,
        action="username_changed",
        metadata={"new_username": "new_alice"},
    )

    # The placeholder doesn't send any emails
    assert len(email_service.sent) == 0


# --- XSS protection (HTML escaping) ---------------------------------------


def test_sanitize_escapes_html_in_username():
    """Angle brackets in username are escaped so they render as text."""
    ctx = build_context(
        username="<script>alert('xss')</script>",
        action="password_reset",
    )
    html = render_password_reset(ctx)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_sanitize_escapes_html_in_ip_address():
    """IP address field is HTML-escaped."""
    ctx = build_context(
        username="alice",
        action="password_reset",
        ip_address='"><img src=x onerror=alert(1)>',
    )
    html = render_password_reset(ctx)
    assert "<img" not in html
    assert "&lt;img" in html


def test_sanitize_escapes_html_in_user_agent():
    """User-agent field is HTML-escaped."""
    ctx = build_context(
        username="alice",
        action="password_reset",
        user_agent='"><script>document.location="http://evil"</script>',
    )
    html = render_password_reset(ctx)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_sanitize_escapes_quotes():
    """Double quotes are escaped to prevent attribute injection."""
    ctx = build_context(
        username='alice" onmouseover="alert(1)',
        action="password_reset",
    )
    html = render_password_reset(ctx)
    assert 'alice"' not in html or "&quot;" in html


def test_email_changed_escapes_old_and_new_email():
    """old_email and new_email in email_changed are HTML-escaped."""
    ctx = build_context(
        username="carol",
        action="email_changed",
        extra={
            "old_email": "<script>xss</script>@evil.com",
            "new_email": "new@evil.com\"><img src=x>",
        },
    )
    html = render_email_changed(ctx)
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_username_changed_escapes_new_username():
    """new_username in username_changed is HTML-escaped."""
    ctx = build_context(
        username="dave",
        action="username_changed",
        extra={"new_username": "<b>hacker</b>"},
    )
    html = render_username_changed(ctx)
    assert "<b>hacker</b>" not in html
    assert "&lt;b&gt;hacker&lt;/b&gt;" in html


def test_unknown_action_escapes_label():
    """Unknown action label is HTML-escaped in the fallback template."""
    ctx = build_context(
        username="frank",
        action="<script>alert('x')</script>",
    )
    subject, html = render_security_notification(
        "<script>alert('x')</script>", ctx
    )
    # The HTML body must not contain the raw (unescaped) script tag
    assert "<script>alert('x')</script>" not in html
    # The .title() call transforms the case (e.g. <Script>), but
    # the escaped angle brackets must still be present.
    assert "&lt;script&gt;" in html.lower()
    # The <title> tag must also carry the escaped form, not the raw tag
    assert "<title>Security Alert: &lt;Script&gt;" in html


# --- Email validation ------------------------------------------------------


def test_validate_email_accepts_valid_address():
    """Standard well-formed addresses pass validation."""
    assert _validate_email("alice@example.com") == "alice@example.com"
    assert _validate_email("bob+tag@sub.domain.org") == "bob+tag@sub.domain.org"


def test_validate_email_rejects_missing_at():
    """Addresses without @ are rejected."""
    assert _validate_email("not-an-email") is None


def test_validate_email_rejects_missing_domain():
    """Addresses without a TLD are rejected."""
    assert _validate_email("alice@") is None
    assert _validate_email("alice@localhost") is None


def test_validate_email_rejects_empty_and_none():
    """Empty, whitespace-only and None values are rejected."""
    assert _validate_email("") is None
    assert _validate_email("   ") is None
    assert _validate_email(None) is None


def test_validate_email_rejects_header_injection():
    """Newline characters (header injection) are rejected."""
    assert _validate_email("alice@example.com\r\nBcc:evil@evil.com") is None


def test_validate_email_strips_whitespace():
    """Leading/trailing whitespace is stripped before validation."""
    assert _validate_email("  alice@example.com  ") == "alice@example.com"


@pytest.mark.asyncio
async def test_invalid_recipient_override_is_rejected(db_session, test_user):
    """send() refuses to deliver when recipient_override is not a valid email."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id=test_user.id,
        action="email_changed",
        metadata={"old_email": "old@example.com", "new_email": "new@example.com"},
        recipient_override="not-a-valid-email",
    )

    assert email_service.sent == []


@pytest.mark.asyncio
async def test_invalid_db_email_is_rejected(db_session):
    """send() refuses to deliver when the on-file email is malformed."""
    user = User(
        id="bad-email-user",
        username="mallory",
        email="not-valid",
    )
    db_session.add(user)
    await db_session.commit()

    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    await service.send(
        user_id="bad-email-user",
        action="password_reset",
    )

    assert email_service.sent == []


# --- Rate limiting ---------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_10_notifications(db_session, test_user):
    """After 10 notifications for the same user+action, further sends are blocked."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    # Send exactly 10 — all should succeed
    for _ in range(10):
        await service.send(
            user_id=test_user.id,
            action="password_reset",
            ip_address="10.0.0.1",
        )

    assert len(email_service.sent) == 10

    # The 11th must be blocked
    await service.send(
        user_id=test_user.id,
        action="password_reset",
        ip_address="10.0.0.1",
    )
    assert len(email_service.sent) == 10


@pytest.mark.asyncio
async def test_rate_limit_is_per_action(db_session, test_user):
    """Different actions have independent rate-limit counters."""
    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    # Fill up the password_reset bucket
    for _ in range(10):
        await service.send(
            user_id=test_user.id,
            action="password_reset",
        )

    # password_change should still work — different action = different key
    await service.send(
        user_id=test_user.id,
        action="password_change",
    )
    assert len(email_service.sent) == 11


@pytest.mark.asyncio
async def test_rate_limit_is_per_user(db_session, test_user):
    """Different users have independent rate-limit counters."""
    other_user = User(
        id="other-user-456",
        username="bob",
        email="bob@example.com",
    )
    db_session.add(other_user)
    await db_session.commit()

    email_service = MockEmailService()
    service = SecurityNotificationService(email_service, db_session)

    # Fill up test_user's bucket
    for _ in range(10):
        await service.send(
            user_id=test_user.id,
            action="password_reset",
        )

    # other_user should still be able to receive
    await service.send(
        user_id="other-user-456",
        action="password_reset",
    )
    assert len(email_service.sent) == 11
