"""Tests for US-46: Revoke a specific device session.

DELETE /api/auth/devices/{token_id} allows an authenticated user to revoke
one of their own active refresh-token records by its primary-key ID.

These tests are RED by design — the endpoint does not exist yet.  Every test
that exercises the endpoint is expected to fail with 404 (FastAPI unknown
route) until the implementation lands.

Database strategy: in-memory SQLite via a local `engine` fixture (same
pattern as test_logout_all.py and test_auth_login.py), so these tests do not
require the PostgreSQL docker-compose instance.

Note on is_current semantics: the endpoint is expected to compare the
`token_id` in the URL against the token-ID that is encoded in the caller's
own refresh-token cookie.  When they match (the caller is revoking the
session they are currently using), the response must additionally clear the
`refresh_token` cookie.  When they do not match, the cookie must remain
untouched.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import RateLimitAttempt, User, UserIdentity, UserRefreshToken
from src.utils.jwt_utils import create_access_token
from src.utils.password import hash_password

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

OWNER_ID = "device-owner-001"
OTHER_ID = "device-other-002"
OWNER_EMAIL = "device-owner@example.com"
OTHER_EMAIL = "device-other@example.com"
PASSWORD = "Password1!"

# Token IDs are UUIDs that the tests manufacture directly so we can reference
# them in both the DB seed and the URL path.
TOKEN_ID_ACTIVE = "token-active-aaa-001"
TOKEN_ID_OTHER_USER = "token-other-bbb-002"
TOKEN_ID_NONEXISTENT = "token-does-not-exist-999"
TOKEN_ID_REVOKED = "token-revoked-ccc-003"
TOKEN_ID_EXPIRED = "token-expired-ddd-004"
TOKEN_ID_CURRENT = "token-current-eee-005"  # matches caller's cookie
TOKEN_ID_DIFFERENT = "token-different-fff-006"  # same user, but not current

COOKIE_NAME = "refresh_token"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(user_id: str) -> dict[str, str]:
    """Return an Authorization header dict with a fresh access token."""
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _hash(key: str) -> str:
    """Mimic auth_service._hash_token."""
    return hashlib.sha256(key.encode()).hexdigest()


async def _token_row(engine, token_id: str) -> UserRefreshToken | None:
    """Fetch a single token row via a fresh session (avoids identity-map staleness)."""
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.id == token_id)
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_rate_limiter(engine):
    """Clear DB-based rate-limit storage between tests to prevent bleed-over."""
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()
    yield
    async with maker() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


@pytest.fixture
async def engine():
    """In-memory SQLite engine with all tables created."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    """Session for seeding; rolls back after the test."""
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def seeded_db(db_session):
    """Two users with a variety of token states covering all test scenarios.

    owner  (OWNER_ID):
      - TOKEN_ID_ACTIVE    — active, not expired
      - TOKEN_ID_REVOKED   — already revoked
      - TOKEN_ID_EXPIRED   — active flag but expires_at in the past
      - TOKEN_ID_CURRENT   — active; simulates the caller's current session
      - TOKEN_ID_DIFFERENT — active; a different session of the same owner

    other_user (OTHER_ID):
      - TOKEN_ID_OTHER_USER — active token belonging to a different user
    """
    now = datetime.now(UTC)
    future = now + timedelta(days=7)
    past = now - timedelta(days=1)

    # Seed users
    db_session.add(
        User(id=OWNER_ID, username="device-owner", email=OWNER_EMAIL)
    )
    db_session.add(
        UserIdentity(
            id=f"identity-{OWNER_ID}",
            user_id=OWNER_ID,
            provider="email",
            provider_user_id=OWNER_EMAIL,
            password_hash=hash_password(PASSWORD),
            verified_at=now,
        )
    )
    db_session.add(
        User(id=OTHER_ID, username="device-other", email=OTHER_EMAIL)
    )
    db_session.add(
        UserIdentity(
            id=f"identity-{OTHER_ID}",
            user_id=OTHER_ID,
            provider="email",
            provider_user_id=OTHER_EMAIL,
            password_hash=hash_password(PASSWORD),
            verified_at=now,
        )
    )

    # Seed tokens
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_ACTIVE,
            user_id=OWNER_ID,
            token_hash=_hash(TOKEN_ID_ACTIVE),
            expires_at=future,
            revoked_at=None,
        )
    )
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_REVOKED,
            user_id=OWNER_ID,
            token_hash=_hash(TOKEN_ID_REVOKED),
            expires_at=future,
            revoked_at=now,
        )
    )
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_EXPIRED,
            user_id=OWNER_ID,
            token_hash=_hash(TOKEN_ID_EXPIRED),
            expires_at=past,
            revoked_at=None,
        )
    )
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_CURRENT,
            user_id=OWNER_ID,
            token_hash=_hash(TOKEN_ID_CURRENT),
            expires_at=future,
            revoked_at=None,
        )
    )
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_DIFFERENT,
            user_id=OWNER_ID,
            token_hash=_hash(TOKEN_ID_DIFFERENT),
            expires_at=future,
            revoked_at=None,
        )
    )
    db_session.add(
        UserRefreshToken(
            id=TOKEN_ID_OTHER_USER,
            user_id=OTHER_ID,
            token_hash=_hash(TOKEN_ID_OTHER_USER),
            expires_at=future,
            revoked_at=None,
        )
    )

    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seeded_db):
    """AsyncClient wired to the FastAPI app with the test DB injected."""
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
# 1. Authentication guard
# ---------------------------------------------------------------------------


async def test_revoke_device_requires_authentication(client):
    """DELETE /api/auth/devices/{id} without Authorization header returns 401."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_ACTIVE}"

    # Act
    response = await client.delete(url)

    # Assert
    assert response.status_code == 401


async def test_revoke_device_rejects_invalid_token(client):
    """A malformed Bearer token is rejected with 401."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_ACTIVE}"

    # Act
    response = await client.delete(
        url, headers={"Authorization": "Bearer not-a-valid-jwt"}
    )

    # Assert
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 2. Success — revoke an active token owned by the caller
# ---------------------------------------------------------------------------


async def test_revoke_device_success_returns_200(client):
    """Revoking an owned active token returns 200 with {revoked: true}."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_ACTIVE}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"revoked": True}


async def test_revoke_device_sets_revoked_at_in_db(client, engine):
    """After a successful revoke the token row has revoked_at set."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_ACTIVE}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 200
    row = await _token_row(engine, TOKEN_ID_ACTIVE)
    assert row is not None
    assert row.revoked_at is not None


# ---------------------------------------------------------------------------
# 3. Cross-user protection — token belongs to another user → 404
# ---------------------------------------------------------------------------


async def test_revoke_device_returns_404_for_other_users_token(client):
    """A token that belongs to a different user returns 404 (not 403).

    The endpoint must not reveal whether the token exists; callers should
    receive the same 404 they would get for a completely unknown token_id.
    """
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_OTHER_USER}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 404


async def test_revoke_device_other_users_token_remains_untouched(client, engine):
    """A 404 response for another user's token must not revoke that token."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_OTHER_USER}"

    # Act
    await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert — other user's token must still be active
    row = await _token_row(engine, TOKEN_ID_OTHER_USER)
    assert row is not None
    assert row.revoked_at is None


# ---------------------------------------------------------------------------
# 4. Non-existent token_id → 404
# ---------------------------------------------------------------------------


async def test_revoke_device_returns_404_for_nonexistent_token(client):
    """A token_id that does not exist in the DB returns 404."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_NONEXISTENT}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 5. Already-revoked token → 404
# ---------------------------------------------------------------------------


async def test_revoke_device_returns_404_for_already_revoked_token(client):
    """An already-revoked token returns 404 (not 200, not 409)."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_REVOKED}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 6. is_current = true — revoking the caller's own current session
# ---------------------------------------------------------------------------


async def test_revoke_current_session_revokes_token(client, engine):
    """When revoking the current session the token row gets revoked_at set."""
    # Arrange — pass TOKEN_ID_CURRENT as both the URL and the cookie so the
    # endpoint can recognise it as is_current=true.
    # The raw cookie value the endpoint sees is the token string whose hash
    # matches token_hash stored in TOKEN_ID_CURRENT.  Since we seeded
    # token_hash = _hash(TOKEN_ID_CURRENT), the raw cookie must be TOKEN_ID_CURRENT.
    url = f"/api/auth/devices/{TOKEN_ID_CURRENT}"

    # Act
    response = await client.delete(
        url,
        headers=_bearer(OWNER_ID),
        cookies={COOKIE_NAME: TOKEN_ID_CURRENT},
    )

    # Assert
    assert response.status_code == 200
    row = await _token_row(engine, TOKEN_ID_CURRENT)
    assert row is not None
    assert row.revoked_at is not None


async def test_revoke_current_session_clears_refresh_cookie(client):
    """When is_current=true the response must delete the refresh_token cookie."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_CURRENT}"

    # Act
    response = await client.delete(
        url,
        headers=_bearer(OWNER_ID),
        cookies={COOKIE_NAME: TOKEN_ID_CURRENT},
    )

    # Assert
    assert response.status_code == 200
    # The server signals cookie deletion by setting Max-Age=0 or an empty
    # value; httpx exposes this as an absent or empty cookie.
    cookie_val = response.cookies.get(COOKIE_NAME)
    assert not cookie_val


# ---------------------------------------------------------------------------
# 7. is_current = false — revoking a different session of the same user
# ---------------------------------------------------------------------------


async def test_revoke_different_session_revokes_token(client, engine):
    """Revoking a session that is NOT the caller's current one revokes it."""
    # Arrange — cookie is TOKEN_ID_CURRENT (current session), URL is TOKEN_ID_DIFFERENT
    url = f"/api/auth/devices/{TOKEN_ID_DIFFERENT}"

    # Act
    response = await client.delete(
        url,
        headers=_bearer(OWNER_ID),
        cookies={COOKIE_NAME: TOKEN_ID_CURRENT},
    )

    # Assert
    assert response.status_code == 200
    row = await _token_row(engine, TOKEN_ID_DIFFERENT)
    assert row is not None
    assert row.revoked_at is not None


async def test_revoke_different_session_does_not_clear_current_cookie(client, engine):
    """Revoking a different session must NOT touch the caller's current cookie."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_DIFFERENT}"

    # Act
    response = await client.delete(
        url,
        headers=_bearer(OWNER_ID),
        cookies={COOKIE_NAME: TOKEN_ID_CURRENT},
    )

    # Assert — current session token should remain untouched in DB
    assert response.status_code == 200
    current_row = await _token_row(engine, TOKEN_ID_CURRENT)
    assert current_row is not None
    assert current_row.revoked_at is None

    # The refresh cookie in the *response* must NOT be cleared
    # (either absent from response headers or still carries a value)
    cookie_val = response.cookies.get(COOKIE_NAME)
    # httpx only reports cookies that the server explicitly Set-Cookie'd back;
    # if the server did not touch the cookie there will be nothing here, which
    # is the desired outcome — we assert it was NOT deleted (non-empty or None
    # meaning "server did not override it").
    # A deletion is signalled by an empty string value or a Max-Age=0 cookie.
    assert cookie_val != ""


async def test_revoke_different_session_does_not_revoke_current_session(client, engine):
    """Revoking a non-current session must leave the current session active."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_DIFFERENT}"

    # Act
    await client.delete(
        url,
        headers=_bearer(OWNER_ID),
        cookies={COOKIE_NAME: TOKEN_ID_CURRENT},
    )

    # Assert — current session must still be usable (revoked_at is None)
    current_row = await _token_row(engine, TOKEN_ID_CURRENT)
    assert current_row is not None
    assert current_row.revoked_at is None


# ---------------------------------------------------------------------------
# 8. Expired (but not revoked) tokens → 404
# ---------------------------------------------------------------------------


async def test_revoke_device_returns_404_for_expired_token(client):
    """An expired token (expires_at < now, not revoked) returns 404.

    Expired tokens are no longer valid sessions; the endpoint must treat them
    the same as non-existent ones and refuse to revoke them with a 404.
    """
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_EXPIRED}"

    # Act
    response = await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert
    assert response.status_code == 404


async def test_revoke_device_expired_token_row_unchanged(client, engine):
    """The expired token row is not mutated when the endpoint returns 404."""
    # Arrange
    url = f"/api/auth/devices/{TOKEN_ID_EXPIRED}"

    # Act
    await client.delete(url, headers=_bearer(OWNER_ID))

    # Assert — revoked_at should still be None (token was not revoked)
    row = await _token_row(engine, TOKEN_ID_EXPIRED)
    assert row is not None
    assert row.revoked_at is None
