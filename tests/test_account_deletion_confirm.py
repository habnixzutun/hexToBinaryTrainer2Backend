"""Tests for US-48: Account Deletion Confirm endpoint.

These tests are intentionally RED: POST /api/users/me/delete/confirm does not
exist yet. They drive the implementation.

Flow under test:
1. A logged-in user requests account deletion (US-47, separate endpoint).
   That endpoint creates a PasswordResetToken row with type="account_deletion"
   and emails the raw token to the user.
2. The user clicks the link, which calls this endpoint with the raw token and
   their current password.
3. This endpoint:
   a. Validates the token (not expired, not used, correct type).
   b. Verifies the password against the user's stored hash.
   c. Sets token.used_at before DELETE (double-submission safety).
   d. Deletes the user (cascade removes identities, refresh tokens, audit logs,
      game profile, and any other child rows with ON DELETE CASCADE).
   e. Clears the refresh-token cookie.
   f. Sends a confirmation email to the (now-deleted) user's address.
   g. Returns 200 {"message": "Account deleted"}.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import (
    AccountDeletionToken,
    User,
    UserAuditLog,
    UserIdentity,
    UserRefreshToken,
)
from src.models.game import UserGameProfile
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import JWT_ALGORITHM, get_jwt_secret
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "Password1!"
WRONG_PASSWORD = "WrongPass2@"
USER_ID = "user-deletion-001"
USER_EMAIL = "deletion-user@example.com"
USER_USERNAME = "deletionuser"

CONFIRM_URL = "/api/users/me/delete/confirm"

DELETION_TOKEN_TYPE = "account_deletion"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _make_deletion_token(user_id: str, ttl_hours: int = 1) -> str:
    payload = {
        "sub": user_id,
        "type": DELETION_TOKEN_TYPE,
        "exp": datetime.now(UTC) + timedelta(hours=ttl_hours),
        "iat": datetime.now(UTC),
    }
    return pyjwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def _make_expired_deletion_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": DELETION_TOKEN_TYPE,
        "exp": datetime.now(UTC) - timedelta(hours=1),
        "iat": datetime.now(UTC) - timedelta(hours=2),
    }
    return pyjwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)

    @event.listens_for(eng.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

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
    user = User(id=USER_ID, username=USER_USERNAME, email=USER_EMAIL)
    db_session.add(user)

    identity = UserIdentity(
        id="identity-deletion-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=USER_EMAIL,
        password_hash=hash_password(VALID_PASSWORD),
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)

    profile = UserGameProfile(
        user_id=USER_ID,
        total_xp=100,
        level=2,
        current_streak=3,
        best_streak=5,
        correct_count=10,
        incorrect_count=2,
    )
    db_session.add(profile)

    rt = UserRefreshToken(
        id="rt-deletion-001",
        user_id=USER_ID,
        token_hash=_hash_token("some-active-refresh-token"),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(rt)

    await db_session.commit()
    yield db_session


@pytest.fixture
async def mock_email_service():
    return MockEmailService()


@pytest.fixture
async def client(engine, seeded_db, mock_email_service):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    from src.routers.user import set_email_service

    set_email_service(mock_email_service)

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    from src.services.email_service import MockEmailService as _Mock

    set_email_service(_Mock())


async def test_valid_token_and_password_returns_200(client, engine, mock_email_service):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-happy-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Account deleted"}


async def test_valid_token_and_password_user_is_deleted(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-deleted-user-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == USER_ID))
        assert result.scalar_one_or_none() is None


async def test_valid_token_and_password_refreshtoken_cookie_cleared(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-cookie-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 200
    cookie_val = response.cookies.get("refresh_token")
    assert not cookie_val


async def test_all_child_data_cascade_deleted(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-cascade-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        s.add(
            UserAuditLog(
                id="audit-cascade-001",
                user_id=USER_ID,
                action="account_deletion_requested",
            )
        )
        await s.commit()

    await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    async with async_session() as s:
        identity_rows = (
            await s.execute(
                select(UserIdentity).where(UserIdentity.user_id == USER_ID)
            )
        ).scalars().all()
        refresh_rows = (
            await s.execute(
                select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
            )
        ).scalars().all()
        profile_row = (
            await s.execute(
                select(UserGameProfile).where(UserGameProfile.user_id == USER_ID)
            )
        ).scalar_one_or_none()
        audit_rows = (
            await s.execute(
                select(UserAuditLog).where(UserAuditLog.user_id == USER_ID)
            )
        ).scalars().all()

    assert identity_rows == []
    assert refresh_rows == []
    assert profile_row is None
    assert audit_rows == []


async def test_confirmation_email_sent_after_deletion(client, engine, mock_email_service):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-email-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    sent_to_user = [m for m in mock_email_service.sent if m["to"] == USER_EMAIL]
    assert len(sent_to_user) >= 1
    assert any(
        m.get("type") in ("account_deleted", "security_notification", "raw")
        for m in sent_to_user
    )


async def test_token_used_at_set_before_delete(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    raw_token = _make_deletion_token(USER_ID)
    async with async_session() as s:
        s.add(
            AccountDeletionToken(
                id="prt-used-at-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    first = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )
    assert first.status_code == 200

    second = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    assert second.status_code in (400, 404)


async def test_wrong_password_returns_401_user_not_deleted(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-wrong-pw-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": WRONG_PASSWORD},
    )

    assert response.status_code == 401

    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == USER_ID))
        assert result.scalar_one_or_none() is not None


async def test_expired_token_returns_400_user_not_deleted(client, engine):
    expired_token = _make_expired_deletion_token(USER_ID)

    response = await client.post(
        CONFIRM_URL,
        json={"token": expired_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 400

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == USER_ID))
        assert result.scalar_one_or_none() is not None


async def test_expired_token_error_message_is_clear(client):
    expired_token = _make_expired_deletion_token(USER_ID)

    response = await client.post(
        CONFIRM_URL,
        json={"token": expired_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 400
    body = response.json()
    assert "detail" in body or "message" in body
    detail = body.get("detail") or body.get("message", "")
    assert len(detail) > 0


async def test_already_used_token_returns_400_user_not_deleted(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-already-used-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await s.commit()

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 400

    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == USER_ID))
        assert result.scalar_one_or_none() is not None


async def test_nonexistent_token_returns_400_user_not_deleted(client, engine):
    orphan_token = _make_deletion_token(USER_ID)

    response = await client.post(
        CONFIRM_URL,
        json={"token": orphan_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 400

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == USER_ID))
        assert result.scalar_one_or_none() is not None


async def test_completely_invalid_token_returns_400(client):
    response = await client.post(
        CONFIRM_URL,
        json={"token": "this.is.not.a.jwt", "password": VALID_PASSWORD},
    )

    assert response.status_code == 400


async def test_missing_token_field_returns_422(client):
    response = await client.post(
        CONFIRM_URL,
        json={"password": VALID_PASSWORD},
    )

    assert response.status_code == 422


async def test_missing_password_field_returns_422(client):
    raw_token = _make_deletion_token(USER_ID)

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token},
    )

    assert response.status_code == 422


async def test_endpoint_does_not_require_authorization_header(client, engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        raw_token = _make_deletion_token(USER_ID)
        s.add(
            AccountDeletionToken(
                id="prt-no-auth-001",
                user_id=USER_ID,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                used_at=None,
            )
        )
        await s.commit()

    response = await client.post(
        CONFIRM_URL,
        json={"token": raw_token, "password": VALID_PASSWORD},
        headers={},
    )

    assert response.status_code != 401
    assert response.status_code == 200


async def test_wrong_token_type_returns_400(client):
    wrong_type_payload = {
        "sub": USER_ID,
        "type": "password_reset",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    wrong_token = pyjwt.encode(
        wrong_type_payload, get_jwt_secret(), algorithm=JWT_ALGORITHM
    )

    response = await client.post(
        CONFIRM_URL,
        json={"token": wrong_token, "password": VALID_PASSWORD},
    )

    assert response.status_code == 400
