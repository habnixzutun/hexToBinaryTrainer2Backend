"""Tests for US-19: Change Password (while logged in)."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity, UserRefreshToken
from src.utils.jwt_utils import create_access_token
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "OldPass1!"
NEW_VALID_PASSWORD = "NewPass2@"
WEAK_PASSWORD = "weak"
USER_ID = "user-pw-change-001"
USER_EMAIL = "pwchange@example.com"


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
    """Seed DB with a verified user and two refresh tokens."""
    pw_hash = hash_password(VALID_PASSWORD)

    user = User(id=USER_ID, username="pwchangeuser", email=USER_EMAIL)
    db_session.add(user)

    identity = UserIdentity(
        id="identity-pw-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=USER_EMAIL,
        password_hash=pw_hash,
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)

    token1 = UserRefreshToken(
        id="refresh-token-001",
        user_id=USER_ID,
        token_hash="hash-one",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    db_session.add(token1)

    token2 = UserRefreshToken(
        id="refresh-token-002",
        user_id=USER_ID,
        token_hash="hash-two",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    db_session.add(token2)

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


def _auth_header(user_id: str = USER_ID) -> dict:
    token = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_change_password_success_returns_200(client):
    """Successful password change returns 200."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": NEW_VALID_PASSWORD},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


async def test_change_password_revokes_all_refresh_tokens(client, engine):
    """All refresh tokens for the user are revoked after a successful change."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": NEW_VALID_PASSWORD},
        headers=_auth_header(),
    )
    assert response.status_code == 200

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        tokens = result.scalars().all()

    assert len(tokens) == 2
    for token in tokens:
        assert token.revoked_at is not None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_change_password_wrong_old_password_returns_401(client):
    """Wrong old password returns 401."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": "WrongOld1!", "new_password": NEW_VALID_PASSWORD},
        headers=_auth_header(),
    )
    assert response.status_code == 401


async def test_change_password_weak_new_password_returns_400(client):
    """Weak new password returns 400."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": WEAK_PASSWORD},
        headers=_auth_header(),
    )
    assert response.status_code == 400


async def test_change_password_same_as_old_returns_400(client):
    """New password same as old password returns 400."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": VALID_PASSWORD},
        headers=_auth_header(),
    )
    assert response.status_code == 400


async def test_change_password_no_bearer_token_returns_401(client):
    """Missing Authorization header returns 401."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": NEW_VALID_PASSWORD},
    )
    assert response.status_code == 401


async def test_change_password_invalid_bearer_token_returns_401(client):
    """Malformed/invalid bearer token returns 401."""
    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": NEW_VALID_PASSWORD},
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert response.status_code == 401


async def test_change_password_expired_token_returns_401(client):
    """Expired bearer token returns 401."""
    import jwt as pyjwt
    from datetime import timedelta

    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime(2000, 1, 1, tzinfo=UTC),
        "iat": datetime(1999, 1, 1, tzinfo=UTC),
    }
    from src.utils.jwt_utils import JWT_SECRET, JWT_ALGORITHM
    expired_token = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    response = await client.post(
        "/api/user/change-password",
        json={"old_password": VALID_PASSWORD, "new_password": NEW_VALID_PASSWORD},
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
