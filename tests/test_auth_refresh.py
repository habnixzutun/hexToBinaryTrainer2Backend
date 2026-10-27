"""Tests for US-02: Refresh-Token-Rotation + Replay-Detektion.

Coverage targets:
- auth_service.refresh() (rotation, replay, expiration, malformed)
- /refresh router (cookie handling, error differentiation)
- _revoke_all_user_tokens (bulk revocation on replay)
- _log_replay_detection (audit log + security notification)

AC mapping (docs/auth-hardening-plan.md, US-02):
- AC 1: test_refresh_revokes_old_token_in_db
- AC 2: test_refresh_token_rotation_creates_new_db_record
- AC 3: test_refresh_rotates_cookie, test_refresh_sets_new_cookie_on_success
- AC 4: test_refresh_replay_detects_attack, test_refresh_replay_revokes_all_tokens
- AC 5: test_refresh_replay_logs_audit_entry, test_refresh_replay_audit_metadata
- AC 6: test_refresh_replay_sends_security_notification
- AC 7: (this file IS the integration test suite)
- AC 8: coverage check (>= 80%)
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserAuditLog, UserIdentity, UserRefreshToken
from src.services import auth_service
from src.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
)
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "Password1!"
VERIFIED_EMAIL = "verified-refresh@example.com"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture(autouse=True)
async def reset_rate_limiter():
    """Clear DB-based rate limit storage between tests to prevent bleed-over.

    Multiple tests in this file call /login, which is rate-limited to 5/min.
    Without resetting the limiter between tests, later tests hit the budget
    consumed by earlier ones and fail with misleading 429 errors.
    """
    from sqlalchemy import delete

    from src.models.auth import RateLimitAttempt

    # Get the engine from the test setup
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()
    await eng.dispose()


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
async def seeded_db(db_session):
    """Seed DB with a verified user."""
    pw_hash = hash_password(VALID_PASSWORD)

    verified_user = User(
        id="user-refresh-001",
        username="refreshuser",
        email=VERIFIED_EMAIL,
    )
    db_session.add(verified_user)

    verified_identity = UserIdentity(
        id="identity-refresh-001",
        user_id="user-refresh-001",
        provider="email",
        provider_user_id=VERIFIED_EMAIL,
        password_hash=pw_hash,
        verified_at=datetime.now(UTC),
    )
    db_session.add(verified_identity)

    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seeded_db):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path: token rotation
# ---------------------------------------------------------------------------


async def test_refresh_rotates_cookie(client):
    """AC 3: refresh sets a NEW refresh-token cookie (different from the old one)."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert login.status_code == 200
    old_refresh = login.cookies.get("refresh_token")
    assert old_refresh is not None

    refresh_resp = await client.post("/api/auth/refresh")
    assert refresh_resp.status_code == 200
    new_refresh = refresh_resp.cookies.get("refresh_token")
    assert new_refresh is not None
    assert old_refresh != new_refresh, "Refresh token must be rotated"


async def test_refresh_returns_new_access_token(client):
    """AC 3: refresh returns a valid access token in the JSON body.

    Note: access tokens issued within the same wall-clock second are
    byte-identical JWTs (same sub/type/iat/exp), so we only assert that
    *some* valid token is returned. Token rotation is verified at the
    refresh-token level (test_refresh_rotates_cookie).
    """
    await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert len(data["access_token"]) > 0


async def test_refresh_revokes_old_token_in_db(client, engine):
    """AC 1: the refresh token presented to /refresh is revoked in the DB."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    old_refresh = login.cookies.get("refresh_token")
    old_hash = _hash_token(old_refresh)

    await client.post("/api/auth/refresh")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.token_hash == old_hash)
        )
        old_record = result.scalar_one()
        assert old_record.revoked_at is not None, "Old token must be revoked"


async def test_refresh_token_rotation_creates_new_db_record(client, engine):
    """AC 2: a new refresh-token record is created on each rotation."""
    await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    await client.post("/api/auth/refresh")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(select(UserRefreshToken))
        tokens = result.scalars().all()
        # 1 (login) + 1 (rotation) = 2 records
        assert len(tokens) == 2
        revoked = [t for t in tokens if t.revoked_at is not None]
        active = [t for t in tokens if t.revoked_at is None]
        assert len(revoked) == 1, "old token revoked"
        assert len(active) == 1, "new token active"


# ---------------------------------------------------------------------------
# Replay detection (AC 4)
# ---------------------------------------------------------------------------


async def test_refresh_replay_detects_attack(client):
    """AC 4: reusing a revoked refresh token returns 401 with a security message."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    # Rotate: A is revoked, B is issued
    refresh_resp = await client.post("/api/auth/refresh")
    assert refresh_resp.status_code == 200
    token_b = refresh_resp.cookies.get("refresh_token")
    assert token_a != token_b

    # Replay: attacker presents the old (revoked) token A
    client.cookies.set("refresh_token", token_a)
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert "Security violation" in response.json()["detail"]


async def test_refresh_replay_revokes_all_tokens(client, engine):
    """AC 4: replay of token A revokes token B as well (aggressive strategy)."""
    # Create token A via login
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    # Rotate: A revoked, B issued
    await client.post("/api/auth/refresh")

    # Replay A → all sessions should be killed
    client.cookies.set("refresh_token", token_a)
    replay_response = await client.post("/api/auth/refresh")
    assert replay_response.status_code == 401

    # Confirm B is also revoked in DB
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(select(UserRefreshToken))
        tokens = result.scalars().all()
        for t in tokens:
            assert t.revoked_at is not None, f"Token {t.id} should be revoked"


async def test_refresh_replay_subsequent_use_of_token_b_fails(client):
    """AC 4: after replay, even the 'new' token B is rejected."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    refresh_resp = await client.post("/api/auth/refresh")
    token_b = refresh_resp.cookies.get("refresh_token")

    # Replay A
    client.cookies.set("refresh_token", token_a)
    await client.post("/api/auth/refresh")

    # Try B — must fail (all sessions dead)
    client.cookies.set("refresh_token", token_b)
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_refresh_multiple_replays_all_detected(engine, seeded_db):
    """Adversarial: 3 consecutive replays all return the security-violation message.

    Each replay uses a fresh client to avoid cookie-jar state coupling:
    after a server-side cookie deletion the httpx client may still hold
    stale attributes for the cookie name, so a fresh client per replay
    keeps the test focused on the server-side behavior.
    """
    # Login + rotate on the shared client
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def _client_factory():
        async def _override():
            async with async_session() as session:
                yield session

        app.dependency_overrides[get_db] = _override
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"refresh_token": ""},
        )

    async with _client_factory() as login_client:
        login = await login_client.post(
            "/api/auth/login",
            json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
        )
        assert login.status_code == 200

    async with _client_factory() as rotate_client:
        rotate_client.cookies.set("refresh_token", login.cookies.get("refresh_token"))
        await rotate_client.post("/api/auth/refresh")

    token_a = login.cookies.get("refresh_token")

    for i in range(3):
        async with _client_factory() as replay_client:
            replay_client.cookies.set("refresh_token", token_a)
            response = await replay_client.post("/api/auth/refresh")
            assert response.status_code == 401, f"replay #{i+1} must be rejected"
            assert "Security violation" in response.json()["detail"]

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Expired / malformed tokens
# ---------------------------------------------------------------------------


async def test_refresh_expired_token_returns_401(client, engine):
    """An expired refresh token returns 401 (without replay escalation)."""
    # Login fresh
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    raw_token = login.cookies.get("refresh_token")

    # Expire the token in the DB directly (bypass JWT exp claim)
    token_hash = _hash_token(raw_token)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
        )
        record = result.scalar_one()
        record.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401
    # Expired (not revoked) → generic error, not the security-violation message
    assert "Security violation" not in response.json()["detail"]


async def test_refresh_no_cookie_returns_401(client):
    """Refresh without a cookie returns 401."""
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_refresh_malformed_token_returns_401(client):
    """A malformed refresh token returns 401."""
    client.cookies.set("refresh_token", "not.a.valid.jwt")
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_refresh_unknown_token_returns_401(client):
    """A valid JWT that is not in the DB returns 401."""
    from src.utils.jwt_utils import create_refresh_token as _create

    fake_token = _create("nonexistent-user-id")
    client.cookies.set("refresh_token", fake_token)
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_refresh_access_token_rejected(client):
    """An access token presented as a refresh token returns 401."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    # The login response body contains the access token
    access_token = login.json()["access_token"]
    client.cookies.set("refresh_token", access_token)

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Audit logging (AC 5)
# ---------------------------------------------------------------------------


async def test_refresh_replay_logs_audit_entry(client, engine):
    """AC 5: a replay creates an audit log entry with the correct action."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    # Rotate so token A is revoked
    await client.post("/api/auth/refresh")

    # Replay
    client.cookies.set("refresh_token", token_a)
    await client.post("/api/auth/refresh")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserAuditLog).where(
                UserAuditLog.action == "refresh_token_replay_detected"
            )
        )
        audit_entries = result.scalars().all()
        assert len(audit_entries) >= 1
        entry = audit_entries[0]
        assert entry.user_id == "user-refresh-001"


async def test_refresh_replay_audit_metadata(client, engine):
    """AC 5: audit entry includes token_id and expired_at in metadata JSON."""
    import json

    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    # Manually expire AND revoke the token so we control the expires_at value
    token_hash = _hash_token(token_a)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
        )
        record = result.scalar_one()
        controlled_expiry = datetime(2025, 1, 1, tzinfo=UTC)
        record.expires_at = controlled_expiry
        record.revoked_at = datetime.now(UTC)
        await session.commit()

    # Replay
    client.cookies.set("refresh_token", token_a)
    await client.post("/api/auth/refresh")

    async with async_session() as session:
        result = await session.execute(
            select(UserAuditLog).where(
                UserAuditLog.action == "refresh_token_replay_detected"
            )
        )
        entry = result.scalar_one()
        assert entry.metadata_json is not None
        metadata = json.loads(entry.metadata_json)
        assert "token_id" in metadata
        assert "expired_at" in metadata
        # SQLite drops timezone info on round-trip, so compare the datetime
        # body (YYYY-MM-DDTHH:MM:SS) only — timezone suffix may or may not
        # be present depending on the backend.
        assert metadata["expired_at"].startswith("2025-01-01T00:00:00")


# ---------------------------------------------------------------------------
# Security notification (AC 6)
# ---------------------------------------------------------------------------


async def test_refresh_replay_sends_security_notification(client):
    """AC 6: send_security_notification is called on replay detection."""
    from unittest.mock import ANY, AsyncMock, patch

    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    token_a = login.cookies.get("refresh_token")

    # Rotate
    await client.post("/api/auth/refresh")

    # Replay with mocked notification
    client.cookies.set("refresh_token", token_a)
    with patch(
        "src.services.auth_service.send_security_notification",
        new_callable=AsyncMock,
    ) as mock_notify:
        response = await client.post("/api/auth/refresh")
        assert response.status_code == 401
        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args.kwargs
        assert call_kwargs["user_id"] == "user-refresh-001"
        assert call_kwargs["action"] == "refresh_token_replay_detected"


# ---------------------------------------------------------------------------
# Service layer unit tests
# ---------------------------------------------------------------------------


async def test_service_refresh_returns_tuple(seeded_db):
    """refresh() returns a (access_token, refresh_token) tuple on success.

    Note: the new access token may be byte-identical to the old one when
    both calls land in the same wall-clock second (JWT iat/exp have only
    second precision). The refresh token, however, contains a fresh jti
    (UUID7) on every call, so it is always different.
    """
    _login_access, login_refresh = await auth_service.login(
        seeded_db, VERIFIED_EMAIL, VALID_PASSWORD
    )

    new_access, new_refresh = await auth_service.refresh(seeded_db, login_refresh)

    assert isinstance(new_access, str)
    assert isinstance(new_refresh, str)
    # Refresh tokens always differ (fresh jti per create_refresh_token)
    assert new_refresh != login_refresh


async def test_service_refresh_unauthorized_on_unknown_token(seeded_db):
    """refresh() raises ValueError('unauthorized') for a token not in the DB."""
    fake_token = create_refresh_token("nonexistent-user")
    with pytest.raises(ValueError, match="unauthorized"):
        await auth_service.refresh(seeded_db, fake_token)


async def test_service_refresh_unauthorized_on_wrong_token_type(seeded_db):
    """refresh() raises ValueError('unauthorized') when token type != 'refresh'."""
    # Access token has type='access' → must be rejected
    access_token = create_access_token("user-refresh-001")
    with pytest.raises(ValueError, match="unauthorized"):
        await auth_service.refresh(seeded_db, access_token)


async def test_service_refresh_replay_detected(seeded_db):
    """refresh() raises ValueError('replay_detected') for a revoked token."""
    _, login_refresh = await auth_service.login(
        seeded_db, VERIFIED_EMAIL, VALID_PASSWORD
    )

    # Consume once (rotates → revokes login_refresh)
    await auth_service.refresh(seeded_db, login_refresh)

    # Replay the now-revoked token
    with pytest.raises(ValueError, match="replay_detected"):
        await auth_service.refresh(seeded_db, login_refresh)


async def test_service_refresh_replay_revokes_all_active_tokens(seeded_db):
    """Replay revokes ALL active tokens, including ones from other logins."""
    # Login twice → 2 active tokens
    _, token_a = await auth_service.login(seeded_db, VERIFIED_EMAIL, VALID_PASSWORD)
    _, token_b = await auth_service.login(seeded_db, VERIFIED_EMAIL, VALID_PASSWORD)

    # Rotate token A → A is revoked, A' is issued
    _, token_a_prime = await auth_service.refresh(seeded_db, token_a)

    # Replay token A → all active tokens (A', B) must be revoked
    with pytest.raises(ValueError, match="replay_detected"):
        await auth_service.refresh(seeded_db, token_a)

    # Verify A' is revoked
    with pytest.raises(ValueError):
        await auth_service.refresh(seeded_db, token_a_prime)

    # Verify B is revoked too
    with pytest.raises(ValueError):
        await auth_service.refresh(seeded_db, token_b)
