"""Tests for US-47: Account Deletion Request endpoint.

POST /api/users/me/delete/request generates a time-limited deletion token,
stores its hash in ``account_deletion_tokens``, and sends a confirmation email.
Migration v007 creates the ``account_deletion_tokens`` table.

These tests FAIL right now (RED) because neither the endpoint nor v007 exists yet.

Run against the PostgreSQL test database:

    docker compose -f docker-compose.test.yaml up -d

The migration_engine / migration_session fixtures live in conftest.py.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.services.email_service import MockEmailService
from src.utils.jwt_utils import create_access_token
from src.utils.password import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-del-req-001"
USER_EMAIL = "delete-me@example.com"
USER_USERNAME = "deletemeuser"
VALID_PASSWORD = "Password1!"

DELETION_TOKEN_TTL_HOURS = 1


# ---------------------------------------------------------------------------
# In-memory SQLite fixtures (endpoint tests)
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """Fresh in-memory SQLite engine with all ORM tables pre-created."""
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
    """Seed one verified user with an email identity."""
    user = User(id=USER_ID, username=USER_USERNAME, email=USER_EMAIL)
    db_session.add(user)

    identity = UserIdentity(
        id="identity-del-req-001",
        user_id=USER_ID,
        provider="email",
        provider_user_id=USER_EMAIL,
        password_hash=hash_password(VALID_PASSWORD),
        verified_at=datetime.now(UTC),
    )
    db_session.add(identity)
    await db_session.commit()
    yield db_session


@pytest.fixture
def mock_email_service():
    return MockEmailService()


@pytest.fixture
async def client(engine, seeded_db, mock_email_service):
    """HTTP client wired to the in-memory DB and a mock email service."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    # Inject the mock email service used by the deletion endpoint.
    # The router is expected to expose set_email_service / get_email_service
    # following the same pattern as src.routers.user and src.routers.auth.
    from src.routers.user import set_email_service

    set_email_service(mock_email_service)
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    # Reset to default MockEmailService so other tests are not polluted.
    from src.services.email_service import MockEmailService as _Mock

    set_email_service(_Mock())


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _bearer(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# AC 1: Endpoint requires authentication
# ---------------------------------------------------------------------------


async def test_delete_request_no_token_returns_401(client):
    """POST without Bearer token returns 401 — not 404, not 500."""
    # Arrange — no Authorization header
    # Act
    response = await client.post("/api/users/me/delete/request")
    # Assert
    assert response.status_code == 401


async def test_delete_request_invalid_token_returns_401(client):
    """POST with a garbage Bearer token returns 401."""
    # Arrange
    headers = {"Authorization": "Bearer this.is.not.valid"}
    # Act
    response = await client.post("/api/users/me/delete/request", headers=headers)
    # Assert
    assert response.status_code == 401


async def test_delete_request_expired_token_returns_401(client):
    """POST with an expired access token returns 401."""
    import jwt

    from src.utils.jwt_utils import JWT_ALGORITHM, get_jwt_secret

    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime.now(UTC) - timedelta(minutes=1),
        "iat": datetime.now(UTC) - timedelta(minutes=16),
    }
    expired_token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    headers = {"Authorization": f"Bearer {expired_token}"}
    # Act
    response = await client.post("/api/users/me/delete/request", headers=headers)
    # Assert
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC 2 + 7: Token hash stored in account_deletion_tokens (raw token NOT stored)
# ---------------------------------------------------------------------------


async def test_delete_request_stores_token_hash_in_db(client, engine):
    """Successful request stores a token hash in account_deletion_tokens.

    The raw token is NEVER stored; only its SHA-256 hex-digest is written.
    """
    # Act
    response = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    assert response.status_code == 200

    # Assert — a row with matching user_id exists and its token_hash looks like
    # a SHA-256 hexdigest (64 lower-hex characters), not a raw JWT string.
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT user_id, token_hash, expires_at "
                "FROM account_deletion_tokens "
                "WHERE user_id = :uid"
            ),
            {"uid": USER_ID},
        )
        row = result.one()

    assert row.user_id == USER_ID
    # A SHA-256 hexdigest is exactly 64 lower-case hex characters.
    assert len(row.token_hash) == 64
    assert all(c in "0123456789abcdef" for c in row.token_hash)
    # The raw JWT contains at least two dots; the hash must not look like a JWT.
    assert "." not in row.token_hash


# ---------------------------------------------------------------------------
# AC 3: Confirmation email sent to user's address
# ---------------------------------------------------------------------------


async def test_delete_request_sends_confirmation_email(client, mock_email_service):
    """Successful request sends exactly one confirmation email to the user."""
    # Act
    response = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    assert response.status_code == 200

    # Assert — exactly one email, sent to the right address, right type.
    assert len(mock_email_service.sent) == 1
    sent = mock_email_service.sent[0]
    assert sent["to"] == USER_EMAIL
    assert sent["type"] == "account_deletion_confirmation"


# ---------------------------------------------------------------------------
# AC 4: Returns 200 with expected message
# ---------------------------------------------------------------------------


async def test_delete_request_returns_200_with_message(client):
    """Successful request returns HTTP 200 and the expected message body."""
    # Act
    response = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    # Assert
    assert response.status_code == 200
    assert response.json() == {"message": "Confirmation email sent"}


# ---------------------------------------------------------------------------
# AC 5: Token expires in 1 hour
# ---------------------------------------------------------------------------


async def test_delete_request_token_expires_in_one_hour(client, engine):
    """The stored token row has expires_at ≈ now + 1 hour (within 60 s tolerance)."""
    # Arrange
    before = datetime.now(UTC)
    # Act
    response = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    assert response.status_code == 200
    after = datetime.now(UTC)

    # Assert
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT expires_at FROM account_deletion_tokens WHERE user_id = :uid"
            ),
            {"uid": USER_ID},
        )
        row = result.one()

    expires_at = row.expires_at
    # SQLite returns datetimes as strings — parse if needed.
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    # Normalise naive datetimes returned by SQLite.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    expected_min = before + timedelta(hours=DELETION_TOKEN_TTL_HOURS) - timedelta(seconds=60)
    expected_max = after + timedelta(hours=DELETION_TOKEN_TTL_HOURS) + timedelta(seconds=60)
    assert expected_min <= expires_at <= expected_max


# ---------------------------------------------------------------------------
# AC 6: A second request replaces/invalidates the active token
# ---------------------------------------------------------------------------


async def test_delete_request_second_call_replaces_old_token(client, engine):
    """A second POST replaces the previous pending token — no stale rows accumulate."""
    # Arrange — first request
    response1 = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    assert response1.status_code == 200

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT token_hash FROM account_deletion_tokens WHERE user_id = :uid"
            ),
            {"uid": USER_ID},
        )
        first_hash = result.scalar_one()

    # Act — second request
    response2 = await client.post(
        "/api/users/me/delete/request", headers=_bearer()
    )
    assert response2.status_code == 200

    # Assert — only one active (non-used) row; its hash differs from the first.
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT token_hash FROM account_deletion_tokens "
                "WHERE user_id = :uid AND used_at IS NULL"
            ),
            {"uid": USER_ID},
        )
        active_rows = result.fetchall()

    assert len(active_rows) == 1
    assert active_rows[0].token_hash != first_hash


async def test_delete_request_second_call_sends_second_email(
    client, mock_email_service
):
    """Each POST sends exactly one email; two POSTs send two total."""
    # Act
    await client.post("/api/users/me/delete/request", headers=_bearer())
    await client.post("/api/users/me/delete/request", headers=_bearer())
    # Assert
    assert len(mock_email_service.sent) == 2
    assert all(m["to"] == USER_EMAIL for m in mock_email_service.sent)
    assert all(m["type"] == "account_deletion_confirmation" for m in mock_email_service.sent)


# ---------------------------------------------------------------------------
# Migration v007 tests (use PostgreSQL fixtures from conftest)
# ---------------------------------------------------------------------------


def _get_table_names(sync_conn):
    inspector = inspect(sync_conn)
    return inspector.get_table_names()


def _get_columns(sync_conn, table_name):
    inspector = inspect(sync_conn)
    return {col["name"]: col for col in inspector.get_columns(table_name)}


def _get_indexes(sync_conn, table_name):
    inspector = inspect(sync_conn)
    return {idx["name"]: idx for idx in inspector.get_indexes(table_name)}


def _get_foreign_keys(sync_conn, table_name):
    inspector = inspect(sync_conn)
    return inspector.get_foreign_keys(table_name)


async def test_v007_creates_account_deletion_tokens_table(
    migration_engine, migration_session
):
    """v007 creates the account_deletion_tokens table."""
    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    async with migration_engine.connect() as conn:
        tables = await conn.run_sync(_get_table_names)
        assert "account_deletion_tokens" in tables


async def test_v007_table_has_required_columns(migration_engine, migration_session):
    """v007 creates account_deletion_tokens with all required columns."""
    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    async with migration_engine.connect() as conn:
        columns = await conn.run_sync(_get_columns, "account_deletion_tokens")

    required = {"id", "user_id", "token_hash", "expires_at", "created_at"}
    assert required <= set(columns)


async def test_v007_table_token_hash_is_unique(migration_session):
    """token_hash column has a UNIQUE constraint — no two tokens share a hash."""
    from sqlalchemy.exc import IntegrityError

    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    # Insert a user first (FK target).
    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v007-user-1', 'v007user', 'v007@example.com')"
        )
    )
    await migration_session.commit()

    same_hash = "a" * 64
    await migration_session.execute(
        text(
            "INSERT INTO account_deletion_tokens (id, user_id, token_hash, expires_at) "
            "VALUES ('tok-1', 'v007-user-1', :h, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
        ),
        {"h": same_hash},
    )
    await migration_session.commit()

    with pytest.raises(IntegrityError):
        await migration_session.execute(
            text(
                "INSERT INTO account_deletion_tokens "
                "(id, user_id, token_hash, expires_at) "
                "VALUES ('tok-2', 'v007-user-1', :h, "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {"h": same_hash},
        )
        await migration_session.commit()
    await migration_session.rollback()


async def test_v007_table_has_user_fk_with_cascade(
    migration_engine, migration_session
):
    """account_deletion_tokens.user_id has a CASCADE FK to users.id."""
    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    async with migration_engine.connect() as conn:
        fk_constraints = await conn.run_sync(
            _get_foreign_keys, "account_deletion_tokens"
        )

    assert len(fk_constraints) > 0
    user_fk = next(
        (fk for fk in fk_constraints if "user_id" in fk.get("constrained_columns", [])),
        None,
    )
    assert user_fk is not None
    assert user_fk["options"].get("ondelete") == "CASCADE"


async def test_v007_cascade_delete_removes_deletion_tokens(migration_session):
    """Deleting a user cascades to account_deletion_tokens rows."""
    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v007-del-1', 'v007deluser', 'v007del@example.com')"
        )
    )
    await migration_session.execute(
        text(
            "INSERT INTO account_deletion_tokens (id, user_id, token_hash, expires_at) "
            "VALUES ('del-tok-1', 'v007-del-1', :h, "
            "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
        ),
        {"h": "b" * 64},
    )
    await migration_session.commit()

    await migration_session.execute(
        text("DELETE FROM users WHERE id = 'v007-del-1'")
    )
    await migration_session.commit()

    result = await migration_session.execute(
        text(
            "SELECT COUNT(*) FROM account_deletion_tokens "
            "WHERE user_id = 'v007-del-1'"
        )
    )
    assert result.scalar() == 0


async def test_v007_is_tracked_and_idempotent(migration_session):
    """v007 records its version and a second run is a no-op."""
    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import MIGRATION_ID as V007_MIGRATION_ID
    from src.migrations.v007_account_deletion import (
        migration_already_applied as v007_already_applied,
    )
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)

    assert await v007_already_applied(migration_session) is False

    await run_v007(migration_session)
    assert await v007_already_applied(migration_session) is True

    # Second boot must not raise.
    await run_v007(migration_session)

    result = await migration_session.execute(
        text("SELECT COUNT(*) FROM schema_migrations WHERE version = :v"),
        {"v": V007_MIGRATION_ID},
    )
    assert result.scalar() == 1


async def test_v007_migration_id_constant():
    """MIGRATION_ID constant has the expected value."""
    from src.migrations.v007_account_deletion import MIGRATION_ID as V007_MIGRATION_ID

    assert V007_MIGRATION_ID == "v007_account_deletion"


async def test_v007_expires_at_is_not_null(migration_session):
    """expires_at is NOT NULL — a deletion token without a TTL must be rejected."""
    from sqlalchemy.exc import IntegrityError

    from src.migrations.v001_auth_schema import create_migrations_table
    from src.migrations.v001_auth_schema import run_migration as run_v001
    from src.migrations.v007_account_deletion import run_migration as run_v007

    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v007(migration_session)

    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v007-noexp', 'v007noexp', 'noexp@example.com')"
        )
    )
    await migration_session.commit()

    with pytest.raises(IntegrityError):
        await migration_session.execute(
            text(
                "INSERT INTO account_deletion_tokens (id, user_id, token_hash) "
                "VALUES ('noexp-tok', 'v007-noexp', :h)"
            ),
            {"h": "c" * 64},
        )
        await migration_session.commit()
    await migration_session.rollback()
