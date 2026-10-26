"""Unit tests for Sprint 2 services (auth, password_reset, user, email)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base
from src.models.auth import PasswordResetToken, User, UserIdentity
from src.services.auth_service import login, logout, refresh
from src.services.email_service import MockEmailService
from src.services.password_reset_service import request_password_reset, reset_password
from src.services.user_service import change_password
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
    user = User(id=user_id, username="testuser", email="test@example.com")
    db_session.add(user)

    identity = UserIdentity(
        id="identity-123",
        user_id=user_id,
        provider="email",
        provider_user_id="test@example.com",
        password_hash=hash_password("TestPass123!"),
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)
    await db_session.commit()
    return user


@pytest.fixture
async def unverified_user(db_session):
    """Create a test user without verified email."""
    user_id = "unverified-user-456"
    user = User(id=user_id, username="unverified", email="unverified@example.com")
    db_session.add(user)

    identity = UserIdentity(
        id="identity-456",
        user_id=user_id,
        provider="email",
        provider_user_id="unverified@example.com",
        password_hash=hash_password("TestPass123!"),
        verified_at=None,
    )
    db_session.add(identity)
    await db_session.commit()
    return user


# ===== auth_service tests =====


@pytest.mark.asyncio
async def test_login_success(db_session, test_user):
    """Test successful login returns tokens."""
    access_token, refresh_token = await login(
        db_session, "test@example.com", "TestPass123!"
    )
    assert access_token is not None
    assert refresh_token is not None
    assert isinstance(access_token, str)
    assert isinstance(refresh_token, str)


@pytest.mark.asyncio
async def test_login_wrong_password(db_session, test_user):
    """Test login with wrong password raises ValueError."""
    with pytest.raises(ValueError, match="unauthorized"):
        await login(db_session, "test@example.com", "WrongPassword!")


@pytest.mark.asyncio
async def test_login_nonexistent_user(db_session):
    """Test login with non-existent user raises ValueError."""
    with pytest.raises(ValueError, match="unauthorized"):
        await login(db_session, "nonexistent@example.com", "TestPass123!")


@pytest.mark.asyncio
async def test_login_unverified_account(db_session, unverified_user):
    """Test login with unverified account raises ValueError."""
    with pytest.raises(ValueError, match="unverified"):
        await login(db_session, "unverified@example.com", "TestPass123!")


@pytest.mark.asyncio
async def test_refresh_success(db_session, test_user):
    """Test successful token refresh (US-02: rotation returns both tokens)."""
    _, refresh_token = await login(db_session, "test@example.com", "TestPass123!")
    new_access_token, new_refresh_token = await refresh(db_session, refresh_token)
    assert new_access_token is not None
    assert new_refresh_token is not None
    assert isinstance(new_access_token, str)
    assert isinstance(new_refresh_token, str)
    # Rotation: new refresh token must differ from the one we presented
    assert new_refresh_token != refresh_token


@pytest.mark.asyncio
async def test_refresh_invalid_token(db_session):
    """Test refresh with invalid token raises ValueError."""
    with pytest.raises(ValueError, match="unauthorized"):
        await refresh(db_session, "invalid-token")


@pytest.mark.asyncio
async def test_logout_revokes_token(db_session, test_user):
    """Test logout revokes the refresh token.

    US-02 update: presenting a revoked token now triggers the replay-detection
    path (all sessions revoked, ValueError("replay_detected")), not the
    generic "unauthorized" path.
    """
    _, refresh_token = await login(db_session, "test@example.com", "TestPass123!")
    await logout(db_session, refresh_token)

    # Token should be revoked, refresh should fail with replay_detected
    with pytest.raises(ValueError, match="replay_detected"):
        await refresh(db_session, refresh_token)


# ===== password_reset_service tests =====


@pytest.mark.asyncio
async def test_request_password_reset(db_session, test_user):
    """Test password reset request creates token."""
    email_service = MockEmailService()
    await request_password_reset(db_session, "test@example.com", email_service)

    # Check token was created
    result = await db_session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == test_user.id
        )
    )
    token = result.scalar_one_or_none()
    assert token is not None
    assert token.used_at is None


@pytest.mark.asyncio
async def test_request_password_reset_nonexistent_user(db_session):
    """Test password reset for non-existent user doesn't error."""
    email_service = MockEmailService()
    # Should not raise, just return silently
    await request_password_reset(db_session, "nonexistent@example.com", email_service)


@pytest.mark.asyncio
async def test_reset_password_success(db_session, test_user):
    """Test successful password reset."""
    email_service = MockEmailService()
    await request_password_reset(db_session, "test@example.com", email_service)

    result = await db_session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == test_user.id
        )
    )
    token_record = result.scalar_one()

    # Use the raw token (we need to generate it properly)
    from src.utils.jwt_utils import create_password_reset_token
    raw_token = create_password_reset_token(test_user.id)

    # Create matching record
    import hashlib
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_record.token_hash = token_hash
    await db_session.commit()

    await reset_password(db_session, raw_token, "NewPass456!")

    # Verify password was changed
    identity_result = await db_session.execute(
        select(UserIdentity).where(UserIdentity.user_id == test_user.id)
    )
    identity = identity_result.scalar_one()
    from src.utils.password import verify_password
    assert verify_password("NewPass456!", identity.password_hash)


# ===== user_service tests =====


@pytest.mark.asyncio
async def test_change_password_success(db_session, test_user):
    """Test successful password change."""
    await change_password(
        db_session, test_user.id, "TestPass123!", "NewPass456!"
    )

    result = await db_session.execute(
        select(UserIdentity).where(UserIdentity.user_id == test_user.id)
    )
    identity = result.scalar_one()
    from src.utils.password import verify_password
    assert verify_password("NewPass456!", identity.password_hash)


@pytest.mark.asyncio
async def test_change_password_wrong_old_password(db_session, test_user):
    """Test password change with wrong old password."""
    with pytest.raises(ValueError, match="wrong_password"):
        await change_password(
            db_session, test_user.id, "WrongPassword!", "NewPass456!"
        )


@pytest.mark.asyncio
async def test_change_password_same_password(db_session, test_user):
    """Test password change with same password."""
    with pytest.raises(ValueError, match="same_password"):
        await change_password(
            db_session, test_user.id, "TestPass123!", "TestPass123!"
        )


@pytest.mark.asyncio
async def test_change_password_weak_new_password(db_session, test_user):
    """Test password change with weak new password."""
    with pytest.raises(ValueError, match="weak_password"):
        await change_password(
            db_session, test_user.id, "TestPass123!", "weak"
        )
