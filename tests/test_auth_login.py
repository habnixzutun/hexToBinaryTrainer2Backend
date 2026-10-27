"""Tests for US-14: Login, Token Refresh, and Logout endpoints."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import RateLimitAttempt, User, UserIdentity, UserRefreshToken
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "Password1!"
VERIFIED_EMAIL = "verified@example.com"
UNVERIFIED_EMAIL = "unverified@example.com"
UNKNOWN_EMAIL = "unknown@example.com"
RATE_LIMIT_EMAIL = "ratelimit-unique-14@example.com"


@pytest.fixture(autouse=True)
async def reset_rate_limiter(engine):
    """Clear database rate limit entries between tests to prevent bleed-over."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


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
    """Seed DB with a verified user and an unverified user."""
    pw_hash = hash_password(VALID_PASSWORD)

    verified_user = User(
        id="user-verified-001",
        username="verifieduser",
        email=VERIFIED_EMAIL,
    )
    db_session.add(verified_user)

    verified_identity = UserIdentity(
        id="identity-verified-001",
        user_id="user-verified-001",
        provider="email",
        provider_user_id=VERIFIED_EMAIL,
        password_hash=pw_hash,
        verified_at=datetime.now(UTC),
    )
    db_session.add(verified_identity)

    unverified_user = User(
        id="user-unverified-001",
        username="unverifieduser",
        email=UNVERIFIED_EMAIL,
    )
    db_session.add(unverified_user)

    unverified_identity = UserIdentity(
        id="identity-unverified-001",
        user_id="user-unverified-001",
        provider="email",
        provider_user_id=UNVERIFIED_EMAIL,
        password_hash=pw_hash,
        verified_at=None,
    )
    db_session.add(unverified_identity)

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
# Login tests
# ---------------------------------------------------------------------------


async def test_login_success_returns_access_token(client):
    """Successful login returns 200 with access_token and sets refresh cookie."""
    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert response.cookies.get("refresh_token") is not None


async def test_login_wrong_password_returns_401(client):
    """Wrong password returns 401."""
    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": "WrongPass1!"},
    )
    assert response.status_code == 401


async def test_login_unverified_account_returns_403(client):
    """Login with unverified account returns 403."""
    response = await client.post(
        "/api/auth/login",
        json={"email": UNVERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 403


async def test_login_unknown_user_returns_401(client):
    """Login with unknown email returns 401."""
    response = await client.post(
        "/api/auth/login",
        json={"email": UNKNOWN_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 401


async def test_login_rate_limit_returns_429(client):
    """Exceeding 5 login attempts per minute per email returns 429."""
    for _ in range(5):
        await client.post(
            "/api/auth/login",
            json={"email": RATE_LIMIT_EMAIL, "password": "WrongPass1!"},
        )
    response = await client.post(
        "/api/auth/login",
        json={"email": RATE_LIMIT_EMAIL, "password": "WrongPass1!"},
    )
    assert response.status_code == 429


async def test_login_missing_fields_returns_422(client):
    """Missing required fields returns 422."""
    response = await client.post("/api/auth/login", json={"email": VERIFIED_EMAIL})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Refresh tests
# ---------------------------------------------------------------------------


async def test_refresh_success_returns_new_access_token(client):
    """Valid refresh token cookie returns new access_token."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert login.status_code == 200

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_refresh_no_cookie_returns_401(client):
    """Refresh without a cookie returns 401."""
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_refresh_revoked_token_returns_401(client, engine):
    """Revoked refresh token returns 401."""
    login = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert login.status_code == 200

    # Revoke the token in DB directly
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(UserRefreshToken))
        token_row = result.scalars().first()
        assert token_row is not None
        token_row.revoked_at = datetime.now(UTC)
        await session.commit()

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Logout tests
# ---------------------------------------------------------------------------


async def test_logout_success_clears_cookie(client):
    """Successful logout returns 200 and clears the refresh_token cookie."""
    await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    response = await client.post("/api/auth/logout")
    assert response.status_code == 200
    # Cookie should be deleted (empty value or absent)
    cookie_val = response.cookies.get("refresh_token")
    assert not cookie_val


async def test_logout_without_cookie_is_idempotent(client):
    """Logout without a cookie returns 200 (idempotent)."""
    response = await client.post("/api/auth/logout")
    assert response.status_code == 200


async def test_logout_revokes_token_in_db(client, engine):
    """After logout the refresh token is revoked in DB."""
    await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    await client.post("/api/auth/logout")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(UserRefreshToken))
        token_row = result.scalars().first()
        assert token_row is not None
        assert token_row.revoked_at is not None
