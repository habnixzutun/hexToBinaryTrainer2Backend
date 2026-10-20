"""Integration tests for complete authentication flows (US-17).

These tests verify that the individual auth features work together correctly
end-to-end: register → verify → login → refresh → logout, password reset,
email change, password change, and multi-device scenarios.
"""


import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.routers.auth import set_email_service as auth_set_email_service
from src.routers.user import get_email_service as user_get_email_service
from src.services.email_service import MockEmailService

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset rate limiters between tests."""
    from src.routers.auth import _registration_attempts
    from src.routers.auth import limiter as auth_limiter

    _registration_attempts.clear()
    auth_limiter.reset()
    yield
    _registration_attempts.clear()
    auth_limiter.reset()


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def mock_email_service():
    return MockEmailService()


@pytest.fixture
async def client(engine, db_session, mock_email_service):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    auth_set_email_service(mock_email_service)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[user_get_email_service] = lambda: mock_email_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _extract_verification_email(mock_email_service: MockEmailService) -> str:
    """Find the most recent verification email and extract the token."""
    for email in reversed(mock_email_service.sent):
        if email.get("type") == "verification":
            return email["token"]
    raise AssertionError("No verification email found")


def _extract_password_reset_email(mock_email_service: MockEmailService) -> str:
    """Find the most recent password reset email and extract the token."""
    for email in reversed(mock_email_service.sent):
        if email.get("type") == "password_reset":
            return email["token"]
    raise AssertionError("No password reset email found")


def _extract_email_change_email(mock_email_service: MockEmailService) -> str:
    """Find the most recent email change verification and extract the token."""
    for email in reversed(mock_email_service.sent):
        if email.get("type") == "email_change_verification":
            return email["token"]
    raise AssertionError("No email change verification email found")


# ---------------------------------------------------------------------------
# Helper: register + verify a user
# ---------------------------------------------------------------------------


async def register_and_verify(
    client: AsyncClient,
    mock_email_service: MockEmailService,
    email: str,
    username: str,
    password: str,
) -> None:
    """Register a user and verify their email via the token in the mock email."""
    reg_response = await client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": password},
    )
    assert reg_response.status_code == 201, reg_response.text

    token = _extract_verification_email(mock_email_service)
    verify_response = await client.post(
        "/api/auth/verify-email",
        json={"token": token},
    )
    assert verify_response.status_code == 200


# ---------------------------------------------------------------------------
# Test 1: Complete Registration Flow
# ---------------------------------------------------------------------------


async def test_complete_registration_flow(client, mock_email_service):
    """Register → Verify Email → Login → Refresh → Logout."""
    email = "flow-reg@example.com"
    password = "Password1!"

    # Step 1: Register
    reg_response = await client.post(
        "/api/auth/register",
        json={"email": email, "username": "flowreg", "password": password},
    )
    assert reg_response.status_code == 201
    assert len(mock_email_service.sent) >= 1

    # Step 2: Verify Email
    token = _extract_verification_email(mock_email_service)
    verify_response = await client.post(
        "/api/auth/verify-email",
        json={"token": token},
    )
    assert verify_response.status_code == 200

    # Step 3: Login
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    refresh_cookie = login_response.cookies.get("refresh_token")
    assert access_token is not None
    assert refresh_cookie is not None

    # Step 4: Refresh (uses cookie automatically)
    refresh_response = await client.post("/api/auth/refresh")
    assert refresh_response.status_code == 200
    refreshed_token = refresh_response.json()["access_token"]
    # Refreshed token is a valid access token
    assert refreshed_token is not None and len(refreshed_token) > 0

    # Step 5: Logout
    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 200


# ---------------------------------------------------------------------------
# Test 2: Complete Password Reset Flow
# ---------------------------------------------------------------------------


async def test_complete_password_reset_flow(client, mock_email_service, engine):
    """Register → Verify → Request Reset → Reset → Login with new password."""
    email = "flow-reset@example.com"
    old_password = "OldPass1!"
    new_password = "NewPass2@"

    await register_and_verify(
        client, mock_email_service, email, "flowreset", old_password
    )

    # Request password reset
    reset_request = await client.post(
        "/api/auth/request-password-reset",
        json={"email": email},
    )
    assert reset_request.status_code == 200

    reset_token = _extract_password_reset_email(mock_email_service)

    # Reset password
    reset_response = await client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": new_password},
    )
    assert reset_response.status_code == 200

    # Login with new password works
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": new_password},
    )
    assert login_response.status_code == 200

    # Old password no longer works
    old_login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": old_password},
    )
    assert old_login.status_code == 401


# ---------------------------------------------------------------------------
# Test 3: Complete Email Change Flow
# ---------------------------------------------------------------------------


async def test_complete_email_change_flow(client, mock_email_service):
    """Register → Verify → Login → Change Email → Verify New Email → Login with new
    email."""
    old_email = "old-email@example.com"
    new_email = "new-email@example.com"
    password = "Password1!"

    await register_and_verify(
        client, mock_email_service, old_email, "emailchanger", password
    )

    # Login to get access token
    login_response = await client.post(
        "/api/auth/login",
        json={"email": old_email, "password": password},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]

    # Request email change
    change_response = await client.patch(
        "/api/user/email",
        json={"new_email": new_email},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change_response.status_code == 200

    email_change_token = _extract_email_change_email(mock_email_service)

    # Verify email change
    verify_response = await client.post(
        "/api/auth/verify-email-change",
        json={"token": email_change_token},
    )
    assert verify_response.status_code == 200

    # Login with new email works
    new_login = await client.post(
        "/api/auth/login",
        json={"email": new_email, "password": password},
    )
    assert new_login.status_code == 200

    # Old email no longer works (user not found)
    old_login = await client.post(
        "/api/auth/login",
        json={"email": old_email, "password": password},
    )
    assert old_login.status_code == 401


# ---------------------------------------------------------------------------
# Test 4: Complete Password Change Flow
# ---------------------------------------------------------------------------


async def test_complete_password_change_flow(client, mock_email_service):
    """Register → Verify → Login → Change Password → Logout → Login with new
    password."""
    email = "pw-change-flow@example.com"
    old_password = "OldPass1!"
    new_password = "NewPass2@"

    await register_and_verify(
        client, mock_email_service, email, "pwchangeflow", old_password
    )

    # Login
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": old_password},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]

    # Change password
    change_response = await client.post(
        "/api/user/change-password",
        json={"old_password": old_password, "new_password": new_password},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change_response.status_code == 200

    # Logout
    await client.post("/api/auth/logout")

    # Login with new password works
    new_login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": new_password},
    )
    assert new_login.status_code == 200

    # Old password no longer works
    old_login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": old_password},
    )
    assert old_login.status_code == 401


# ---------------------------------------------------------------------------
# Test 5: Password Change Invalidates All Sessions
# ---------------------------------------------------------------------------


async def test_password_change_invalidates_all_sessions(client, mock_email_service):
    """After password change, all refresh tokens are revoked."""
    email = "session-invalidate@example.com"
    old_password = "OldPass1!"
    new_password = "NewPass2@"

    await register_and_verify(
        client, mock_email_service, email, "sessionkill", old_password
    )

    # Login on device A
    login_a = await client.post(
        "/api/auth/login",
        json={"email": email, "password": old_password},
    )
    assert login_a.status_code == 200
    access_token_a = login_a.json()["access_token"]

    # Login on device B (fresh client)
    from httpx import ASGITransport, AsyncClient

    transport_b = ASGITransport(app=app)
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        app.dependency_overrides[user_get_email_service] = lambda: mock_email_service
        login_b = await client_b.post(
            "/api/auth/login",
            json={"email": email, "password": old_password},
        )
        assert login_b.status_code == 200
        refresh_b = login_b.cookies.get("refresh_token")

    # Change password from device A
    change_response = await client.post(
        "/api/user/change-password",
        json={"old_password": old_password, "new_password": new_password},
        headers={"Authorization": f"Bearer {access_token_a}"},
    )
    assert change_response.status_code == 200

    # Refresh from device B should now fail
    transport_b2 = ASGITransport(app=app)
    async with AsyncClient(transport=transport_b2, base_url="http://test") as client_b2:
        client_b2.cookies.set("refresh_token", refresh_b)
        refresh_response = await client_b2.post("/api/auth/refresh")
        assert refresh_response.status_code == 401


# ---------------------------------------------------------------------------
# Test 6: Multi-Device Logout Independence
# ---------------------------------------------------------------------------


async def test_logout_device_a_does_not_affect_device_b(client, mock_email_service):
    """Logout on device A should not invalidate device B's refresh token."""
    email = "multi-device@example.com"
    password = "Password1!"

    await register_and_verify(
        client, mock_email_service, email, "multidevice", password
    )

    # Login on device A
    login_a = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_a.status_code == 200

    # Login on device B
    from httpx import ASGITransport, AsyncClient

    transport_b = ASGITransport(app=app)
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        app.dependency_overrides[user_get_email_service] = lambda: mock_email_service
        login_b = await client_b.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login_b.status_code == 200
        refresh_b = login_b.cookies.get("refresh_token")

    # Logout from device A
    await client.post("/api/auth/logout")

    # Refresh on device B still works
    transport_b2 = ASGITransport(app=app)
    async with AsyncClient(transport=transport_b2, base_url="http://test") as client_b2:
        client_b2.cookies.set("refresh_token", refresh_b)
        refresh_response = await client_b2.post("/api/auth/refresh")
        assert refresh_response.status_code == 200


# ---------------------------------------------------------------------------
# Test 7: Password Reset Invalidates All Sessions
# ---------------------------------------------------------------------------


async def test_password_reset_invalidates_all_sessions(
    client, mock_email_service, engine
):
    """After password reset, all refresh tokens are revoked."""
    email = "reset-invalidate@example.com"
    old_password = "OldPass1!"
    new_password = "NewPass2@"

    await register_and_verify(
        client, mock_email_service, email, "resetkill", old_password
    )

    # Login to get a refresh token
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": old_password},
    )
    assert login_response.status_code == 200
    refresh_token = login_response.cookies.get("refresh_token")

    # Request + perform password reset
    await client.post("/api/auth/request-password-reset", json={"email": email})
    reset_token = _extract_password_reset_email(mock_email_service)
    await client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": new_password},
    )

    # The original refresh token should now be revoked
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client_b:
        client_b.cookies.set("refresh_token", refresh_token)
        refresh_response = await client_b.post("/api/auth/refresh")
        assert refresh_response.status_code == 401


# ---------------------------------------------------------------------------
# Test 8: Token Replay Attack — expired reset token
# ---------------------------------------------------------------------------


async def test_expired_reset_token_cannot_be_reused(client, mock_email_service):
    """A password reset token can only be used once."""
    email = "replay@example.com"
    password = "Password1!"
    new_password = "NewPass2@"

    await register_and_verify(client, mock_email_service, email, "replayuser", password)

    # Request reset
    await client.post("/api/auth/request-password-reset", json={"email": email})
    reset_token = _extract_password_reset_email(mock_email_service)

    # Use token successfully
    first_reset = await client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": new_password},
    )
    assert first_reset.status_code == 200

    # Try to use same token again — should fail
    second_reset = await client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "Another1!"},
    )
    assert second_reset.status_code == 400


# ---------------------------------------------------------------------------
# Test 9: Audit log entries are created for critical actions
# ---------------------------------------------------------------------------


async def test_audit_log_entries_created(client, mock_email_service, engine):
    """Password change and reset create audit log entries."""
    from src.models.auth import UserAuditLog

    email = "audit-flow@example.com"
    password = "Password1!"
    new_password = "NewPass2@"

    await register_and_verify(client, mock_email_service, email, "audituser", password)

    # Login and change password
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    access_token = login_response.json()["access_token"]

    await client.post(
        "/api/user/change-password",
        json={"old_password": password, "new_password": new_password},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # Verify audit log entry for password_change
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserAuditLog).where(UserAuditLog.action == "password_change")
        )
        audit_entries = result.scalars().all()
        assert len(audit_entries) >= 1


# ---------------------------------------------------------------------------
# Test 10: Username change cooldown enforcement
# ---------------------------------------------------------------------------


async def test_username_change_cooldown_integration(client, mock_email_service):
    """Username change enforces 7-day cooldown across the full flow."""
    email = "username-cooldown@example.com"
    password = "Password1!"

    await register_and_verify(
        client, mock_email_service, email, "cooldownuser", password
    )

    # Login
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    access_token = login_response.json()["access_token"]

    # Change username successfully
    change1 = await client.patch(
        "/api/user/username",
        json={"new_username": "newname1"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change1.status_code == 200

    # Try to change again within 7 days — should fail with 429
    change2 = await client.patch(
        "/api/user/username",
        json={"new_username": "newname2"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change2.status_code == 429
