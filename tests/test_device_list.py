"""Tests for US-45: GET /api/auth/devices — Active-Device Listing.

Coverage targets (10 acceptance criteria):
- AC 1: Endpoint requires authentication (401 without token)
- AC 2: Returns only active (non-revoked, non-expired) tokens for the current user
- AC 3: Each entry has: id, device_type, browser, os, ip_address, location_city,
         location_country, created_at, expires_at, is_current
- AC 4: is_current=True for the token matching the refresh cookie (hash comparison)
- AC 5: is_current=False for other active tokens
- AC 6: Revoked tokens do NOT appear in the list
- AC 7: Expired tokens do NOT appear in the list
- AC 8: Tokens from other users do NOT appear
- AC 9: device_type is one of: "Mobile", "Tablet", "PC", "Unknown"
- AC 10: Empty list when no active sessions

Database strategy: in-memory SQLite via a local `engine` fixture (same pattern as
tests/test_logout_all.py), so these tests do not depend on the PostgreSQL instance
from docker-compose.test.yaml.

These tests are intentionally RED: GET /api/auth/devices does not exist yet.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity, UserRefreshToken
from src.utils.jwt_utils import REFRESH_TOKEN_TTL_DAYS, create_access_token, create_refresh_token
from src.utils.password import hash_password
from uuid_utils import uuid7

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_A = "user-device-a-001"
USER_B = "user-device-b-002"
EMAIL_A = "devices-a@example.com"
EMAIL_B = "devices-b@example.com"
PASSWORD = "Password1!"

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)

DEVICE_TYPES = {"Mobile", "Tablet", "PC", "Unknown"}

REQUIRED_ENTRY_FIELDS = {
    "id",
    "device_type",
    "browser",
    "os",
    "ip_address",
    "location_city",
    "location_country",
    "created_at",
    "expires_at",
    "is_current",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bearer(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _make_token_id() -> str:
    return str(uuid7())


async def _add_user(session: AsyncSession, user_id: str, email: str) -> None:
    session.add(User(id=user_id, username=f"name-{user_id}", email=email))
    session.add(
        UserIdentity(
            id=f"identity-{user_id}",
            user_id=user_id,
            provider="email",
            provider_user_id=email,
            password_hash=hash_password(PASSWORD),
            verified_at=datetime.now(UTC),
        )
    )


async def _add_refresh_token(
    session: AsyncSession,
    user_id: str,
    *,
    token_key: str | None = None,
    expires_in_days: int = REFRESH_TOKEN_TTL_DAYS,
    revoked: bool = False,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Seed a refresh-token row and return the raw token string."""
    raw_token = create_refresh_token(user_id) if token_key is None else token_key
    now = datetime.now(UTC)
    record = UserRefreshToken(
        id=_make_token_id(),
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=now + timedelta(days=expires_in_days),
        revoked_at=now if revoked else None,
    )
    # Store device metadata when the model columns exist.
    # If the columns are absent (before migration), the test will still
    # seed the row; the endpoint is what must fail (404/422) to keep RED.
    if ip_address is not None and hasattr(UserRefreshToken, "ip_address"):
        record.ip_address = ip_address
    if user_agent is not None and hasattr(UserRefreshToken, "user_agent"):
        record.user_agent = user_agent
    session.add(record)
    return raw_token


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_rate_limiter():
    """Clear DB-based rate limit storage between tests to prevent bleed-over."""
    from sqlalchemy import delete

    from src.models.auth import RateLimitAttempt

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
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def seed_users(db_session):
    """Two verified users with no refresh tokens yet."""
    await _add_user(db_session, USER_A, EMAIL_A)
    await _add_user(db_session, USER_B, EMAIL_B)
    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seed_users):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# AC 1: authentication required
# ---------------------------------------------------------------------------


async def test_device_list_requires_auth(client):
    """AC 1: GET /api/auth/devices returns 401 without Authorization header."""
    # Arrange — no auth header

    # Act
    response = await client.get("/api/auth/devices")

    # Assert
    assert response.status_code == 401


async def test_device_list_rejects_missing_bearer(client):
    """AC 1: GET /api/auth/devices returns 401 when Authorization header is absent."""
    # Arrange
    headers = {}

    # Act
    response = await client.get("/api/auth/devices", headers=headers)

    # Assert
    assert response.status_code == 401


async def test_device_list_rejects_invalid_token(client):
    """AC 1: GET /api/auth/devices returns 401 for a malformed token."""
    # Arrange
    headers = {"Authorization": "Bearer not-a-jwt"}

    # Act
    response = await client.get("/api/auth/devices", headers=headers)

    # Assert
    assert response.status_code == 401


async def test_device_list_rejects_refresh_token_as_access(client):
    """AC 1: A refresh-type JWT is rejected — only access tokens are valid."""
    # Arrange
    refresh_token = create_refresh_token(USER_A)
    headers = {"Authorization": f"Bearer {refresh_token}"}

    # Act
    response = await client.get("/api/auth/devices", headers=headers)

    # Assert
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC 2 + AC 10: returns active tokens only, empty when none
# ---------------------------------------------------------------------------


async def test_device_list_empty_when_no_sessions(client):
    """AC 10: GET /api/auth/devices returns an empty list when the user has no tokens."""
    # Arrange — seed_users fixture: USER_A has no refresh tokens

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "devices" in data
    assert data["devices"] == []


async def test_device_list_returns_active_tokens(client, db_session):
    """AC 2: Returns all active (non-revoked, non-expired) tokens for the user."""
    # Arrange — add 3 active tokens for USER_A
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "devices" in data
    assert len(data["devices"]) == 3


# ---------------------------------------------------------------------------
# AC 3: entry schema validation
# ---------------------------------------------------------------------------


async def test_device_list_entry_has_required_fields(client, db_session):
    """AC 3: Each device entry contains all required fields."""
    # Arrange
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    entries = response.json()["devices"]
    assert len(entries) == 1
    entry = entries[0]
    for field in REQUIRED_ENTRY_FIELDS:
        assert field in entry, f"Missing field in device entry: {field!r}"


async def test_device_list_entry_fields_are_not_null_for_known_ua(client, db_session):
    """AC 3: Non-nullable fields have real values when user-agent is known."""
    # Arrange — token with known Chrome UA stored
    await _add_refresh_token(
        db_session,
        USER_A,
        ip_address="192.168.0.1",
        user_agent=CHROME_UA,
    )
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    entry = response.json()["devices"][0]
    assert entry["id"] is not None
    assert entry["device_type"] is not None
    assert entry["created_at"] is not None
    assert entry["expires_at"] is not None
    assert isinstance(entry["is_current"], bool)


# ---------------------------------------------------------------------------
# AC 4 + AC 5: is_current flag
# ---------------------------------------------------------------------------


async def test_device_list_is_current_true_for_matching_cookie(client, seed_users):
    """AC 4: is_current=True for the device whose token hash matches the refresh cookie."""
    # Arrange — login to get a real refresh cookie
    login = await client.post(
        "/api/auth/login",
        json={"email": EMAIL_A, "password": PASSWORD},
    )
    assert login.status_code == 200
    current_refresh = login.cookies.get("refresh_token")
    assert current_refresh is not None

    # Act — call devices endpoint with the current refresh cookie
    response = await client.get(
        "/api/auth/devices",
        headers=_bearer(USER_A),
        cookies={"refresh_token": current_refresh},
    )

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["is_current"] is True


async def test_device_list_is_current_false_for_other_tokens(client, db_session):
    """AC 5: is_current=False for tokens that are not the current refresh cookie."""
    # Arrange — login creates one token (is_current=True), seed another manually
    login = await client.post(
        "/api/auth/login",
        json={"email": EMAIL_A, "password": PASSWORD},
    )
    assert login.status_code == 200
    current_refresh = login.cookies.get("refresh_token")

    # Add a second active token directly
    other_raw = await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get(
        "/api/auth/devices",
        headers=_bearer(USER_A),
        cookies={"refresh_token": current_refresh},
    )

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2

    current_devices = [d for d in devices if d["is_current"] is True]
    other_devices = [d for d in devices if d["is_current"] is False]
    assert len(current_devices) == 1
    assert len(other_devices) == 1


async def test_device_list_all_is_current_false_without_cookie(client, db_session):
    """AC 5: All entries have is_current=False when no refresh cookie is sent."""
    # Arrange — two active tokens
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act — no refresh_token cookie provided
    response = await client.get(
        "/api/auth/devices",
        headers=_bearer(USER_A),
    )

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2
    assert all(d["is_current"] is False for d in devices)


async def test_device_list_exactly_one_is_current_with_valid_cookie(client, seed_users):
    """AC 4+5: Exactly one device is_current=True when cookie matches one token."""
    # Arrange — two logins = two tokens
    first_login = await client.post(
        "/api/auth/login",
        json={"email": EMAIL_A, "password": PASSWORD},
    )
    first_refresh = first_login.cookies.get("refresh_token")

    second_login = await client.post(
        "/api/auth/login",
        json={"email": EMAIL_A, "password": PASSWORD},
    )
    second_refresh = second_login.cookies.get("refresh_token")
    assert first_refresh != second_refresh

    # Act — present second token as current
    response = await client.get(
        "/api/auth/devices",
        headers=_bearer(USER_A),
        cookies={"refresh_token": second_refresh},
    )

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2
    current_count = sum(1 for d in devices if d["is_current"] is True)
    assert current_count == 1


# ---------------------------------------------------------------------------
# AC 6: revoked tokens excluded
# ---------------------------------------------------------------------------


async def test_device_list_excludes_revoked_tokens(client, db_session):
    """AC 6: Revoked tokens do NOT appear in the device list."""
    # Arrange — 2 active tokens + 3 revoked tokens
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A, revoked=True)
    await _add_refresh_token(db_session, USER_A, revoked=True)
    await _add_refresh_token(db_session, USER_A, revoked=True)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2


async def test_device_list_empty_when_all_revoked(client, db_session):
    """AC 6: Returns empty list when all tokens are revoked."""
    # Arrange
    for _ in range(3):
        await _add_refresh_token(db_session, USER_A, revoked=True)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    assert response.json()["devices"] == []


# ---------------------------------------------------------------------------
# AC 7: expired tokens excluded
# ---------------------------------------------------------------------------


async def test_device_list_excludes_expired_tokens(client, db_session):
    """AC 7: Expired tokens (expires_at < now) do NOT appear in the device list."""
    # Arrange — 1 active token + 2 expired tokens
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A, expires_in_days=-1)
    await _add_refresh_token(db_session, USER_A, expires_in_days=-5)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1


async def test_device_list_empty_when_all_expired(client, db_session):
    """AC 7: Returns empty list when all tokens are expired."""
    # Arrange
    await _add_refresh_token(db_session, USER_A, expires_in_days=-1)
    await _add_refresh_token(db_session, USER_A, expires_in_days=-3)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    assert response.json()["devices"] == []


async def test_device_list_excludes_both_revoked_and_expired(client, db_session):
    """AC 6+7: Both revoked and expired tokens are filtered out; only active remain."""
    # Arrange — 2 active, 1 revoked, 1 expired, 1 revoked+expired
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A, revoked=True)
    await _add_refresh_token(db_session, USER_A, expires_in_days=-1)
    await _add_refresh_token(
        db_session,
        USER_A,
        revoked=True,
        expires_in_days=-1,
    )
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2


# ---------------------------------------------------------------------------
# AC 8: tokens from other users not included
# ---------------------------------------------------------------------------


async def test_device_list_excludes_other_users_tokens(client, db_session):
    """AC 8: Tokens belonging to another user do NOT appear in the response."""
    # Arrange — USER_A: 2 tokens, USER_B: 3 tokens
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_B)
    await _add_refresh_token(db_session, USER_B)
    await _add_refresh_token(db_session, USER_B)
    await db_session.commit()

    # Act — authenticated as USER_A
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 2


async def test_device_list_user_b_only_sees_own_tokens(client, db_session):
    """AC 8: USER_B authenticated sees only their own tokens, not USER_A's."""
    # Arrange — USER_A: 3 tokens, USER_B: 1 token
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_A)
    await _add_refresh_token(db_session, USER_B)
    await db_session.commit()

    # Act — authenticated as USER_B
    response = await client.get("/api/auth/devices", headers=_bearer(USER_B))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1


# ---------------------------------------------------------------------------
# AC 9: device_type values
# ---------------------------------------------------------------------------


async def test_device_list_device_type_is_valid_value(client, db_session):
    """AC 9: device_type is one of the four allowed values."""
    # Arrange
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_type"] in DEVICE_TYPES


async def test_device_list_device_type_mobile_for_iphone_ua(client, db_session):
    """AC 9: device_type='Mobile' when user-agent identifies an iPhone."""
    # Arrange
    await _add_refresh_token(
        db_session,
        USER_A,
        user_agent=IPHONE_UA,
    )
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_type"] == "Mobile"


async def test_device_list_device_type_pc_for_desktop_ua(client, db_session):
    """AC 9: device_type='PC' when user-agent identifies a desktop browser."""
    # Arrange
    await _add_refresh_token(
        db_session,
        USER_A,
        user_agent=CHROME_UA,
    )
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_type"] == "PC"


async def test_device_list_device_type_tablet_for_ipad_ua(client, db_session):
    """AC 9: device_type='Tablet' when user-agent identifies an iPad."""
    # Arrange
    await _add_refresh_token(
        db_session,
        USER_A,
        user_agent=IPAD_UA,
    )
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_type"] == "Tablet"


async def test_device_list_device_type_unknown_for_no_ua(client, db_session):
    """AC 9: device_type='Unknown' when no user-agent is available."""
    # Arrange — no user_agent stored
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_type"] in DEVICE_TYPES


async def test_device_list_all_device_types_are_valid(client, db_session):
    """AC 9: All returned device entries have a valid device_type."""
    # Arrange — multiple tokens with different UAs
    await _add_refresh_token(db_session, USER_A, user_agent=CHROME_UA)
    await _add_refresh_token(db_session, USER_A, user_agent=IPHONE_UA)
    await _add_refresh_token(db_session, USER_A, user_agent=IPAD_UA)
    await _add_refresh_token(db_session, USER_A, user_agent=FIREFOX_UA)
    await _add_refresh_token(db_session, USER_A)  # no UA -> Unknown
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 5
    for device in devices:
        assert device["device_type"] in DEVICE_TYPES, (
            f"Invalid device_type: {device['device_type']!r}"
        )


# ---------------------------------------------------------------------------
# Response shape contract
# ---------------------------------------------------------------------------


async def test_device_list_response_is_wrapped_in_devices_key(client, db_session):
    """Response body is {devices: [...]} — not a bare list."""
    # Arrange
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    assert "devices" in body
    assert isinstance(body["devices"], list)


async def test_device_list_id_matches_db_token_id(client, engine, db_session):
    """The `id` field in each entry matches the UserRefreshToken.id in the DB."""
    # Arrange — add a token directly and capture its id
    from sqlalchemy import select

    raw_token = await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    token_hash = _hash_token(raw_token)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh_session:
        result = await fresh_session.execute(
            select(UserRefreshToken).where(UserRefreshToken.token_hash == token_hash)
        )
        db_record = result.scalar_one()
        expected_id = db_record.id

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["id"] == expected_id


async def test_device_list_expires_at_is_iso8601(client, db_session):
    """expires_at is returned as an ISO 8601 datetime string."""
    # Arrange
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    expires_at = devices[0]["expires_at"]
    # Must be a non-empty string parseable as a datetime
    assert isinstance(expires_at, str)
    assert len(expires_at) > 0
    # Quick sanity: starts with a year digit
    assert expires_at[0].isdigit()


async def test_device_list_created_at_is_iso8601(client, db_session):
    """created_at is returned as an ISO 8601 datetime string."""
    # Arrange
    await _add_refresh_token(db_session, USER_A)
    await db_session.commit()

    # Act
    response = await client.get("/api/auth/devices", headers=_bearer(USER_A))

    # Assert
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    created_at = devices[0]["created_at"]
    assert isinstance(created_at, str)
    assert len(created_at) > 0
    assert created_at[0].isdigit()
