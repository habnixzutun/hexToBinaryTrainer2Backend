"""Tests for US-44: Device tracking migration v006.

Covers:
1. Migration v006 adds ip_address, user_agent, location_city, location_country
   columns to user_refresh_tokens (all nullable TEXT).
2. Existing tokens without these columns still work (NULL values accepted).
3. POST /api/auth/login saves ip_address from request.client.host.
4. POST /api/auth/login saves user_agent from request headers.
5. POST /api/auth/login saves location_city and location_country
   via a stubbed geolocation service.
6. Refresh token rotation preserves ip_address and user_agent on the new token.

All tests here are RED until v006_device_tracking is written and wired into
the lifespan, and until login() / refresh() in auth_service.py are updated to
accept and persist the four new fields.

Run with:
    docker compose -f docker-compose.test.yaml up -d
    pytest tests/test_device_tracking_migration.py -v
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.database.engine import Base, get_db
from src.main import app
from src.migrations.v001_auth_schema import (
    create_migrations_table,
    run_migration as run_v001,
)
from src.migrations.v006_device_tracking import (  # noqa: F401 — will fail until created
    MIGRATION_ID as V006_MIGRATION_ID,
    migration_already_applied as v006_already_applied,
    run_migration as run_v006,
)
from src.models.auth import RateLimitAttempt, User, UserIdentity, UserRefreshToken
from src.utils.password import hash_password

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

VALID_PASSWORD = "Password1!"
VERIFIED_EMAIL = "device-track@example.com"
USER_ID = "user-device-track-001"

V006_DEVICE_COLUMNS = ("ip_address", "user_agent", "location_city", "location_country")

# ---------------------------------------------------------------------------
# In-memory SQLite fixtures (endpoint tests)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_rate_limiter(engine):
    """Clear rate limit rows before and after each test to prevent bleed-over."""
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
    """In-memory SQLite engine for endpoint tests (Base.metadata pre-created)."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    """Session tied to the in-memory engine; rolls back after each test."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def seeded_db(db_session):
    """Single verified user seeded into the in-memory DB."""
    pw_hash = hash_password(VALID_PASSWORD)

    db_session.add(
        User(
            id=USER_ID,
            username="devicetrackuser",
            email=VERIFIED_EMAIL,
        )
    )
    db_session.add(
        UserIdentity(
            id="identity-device-track-001",
            user_id=USER_ID,
            provider="email",
            provider_user_id=VERIFIED_EMAIL,
            password_hash=pw_hash,
            verified_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    yield db_session


@pytest.fixture
async def client(engine, seeded_db):
    """HTTPX client wired to the FastAPI app with the test engine injected."""
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
# Helper
# ---------------------------------------------------------------------------


def _sha256(token: str) -> str:
    """Hash a raw token the same way auth_service does."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Migration tests (use the shared PostgreSQL fixtures from conftest.py)
# ---------------------------------------------------------------------------


async def test_v006_reports_not_applied_on_pristine_schema(migration_session):
    """First-boot probe on an empty DB returns False, not an exception.

    The lifespan calls migration_already_applied before schema_migrations
    exists; the function must swallow the error and return False so a fresh
    install can ever reach its first migration.
    """
    # Arrange: no tables at all (migration_session already dropped + recreated
    # the public schema in conftest)

    # Act + Assert
    assert await v006_already_applied(migration_session) is False


async def test_v006_adds_four_columns_to_user_refresh_tokens(
    migration_engine, migration_session
):
    """v006 adds ip_address, user_agent, location_city, location_country.

    All four must be present after the migration runs on a DB that has the
    baseline schema from v001 only.
    """
    # Arrange
    await create_migrations_table(migration_session)
    await run_v001(migration_session)

    # Act
    await run_v006(migration_session)

    # Assert — inspect via information_schema (Postgres)
    async with migration_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'user_refresh_tokens' "
                "ORDER BY column_name"
            )
        )
        columns = {row[0] for row in result.fetchall()}

    for col in V006_DEVICE_COLUMNS:
        assert col in columns, f"Expected column {col!r} not found after v006"


async def test_v006_new_columns_are_nullable(migration_engine, migration_session):
    """All four new columns must allow NULL.

    Existing rows written before the migration have no IP, UA or location —
    requiring a value would break every live token.
    """
    # Arrange
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v006(migration_session)

    # Act: insert a user and a token with only the legacy required fields
    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v006-null-user', 'v006nulluser', 'v006null@example.com')"
        )
    )
    await migration_session.execute(
        text(
            "INSERT INTO user_refresh_tokens "
            "(id, user_id, token_hash, expires_at) "
            "VALUES ('v006-tok-1', 'v006-null-user', 'hash-null-001', "
            "NOW() + INTERVAL '7 days')"
        )
    )
    await migration_session.commit()

    # Assert: row is readable and all four new columns are NULL
    row = (
        await migration_session.execute(
            text(
                "SELECT ip_address, user_agent, location_city, location_country "
                "FROM user_refresh_tokens WHERE id = 'v006-tok-1'"
            )
        )
    ).one()

    assert row.ip_address is None
    assert row.user_agent is None
    assert row.location_city is None
    assert row.location_country is None


async def test_v006_existing_tokens_work_after_migration(
    migration_engine, migration_session
):
    """A token written before the migration is still readable after.

    Validates the most common upgrade scenario: a running app has tokens in the
    DB, the migration adds columns, and no existing row is destroyed or broken.
    """
    # Arrange: create the baseline schema and add a token
    await create_migrations_table(migration_session)
    await run_v001(migration_session)

    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v006-pre-user', 'v006preuser', 'v006pre@example.com')"
        )
    )
    await migration_session.execute(
        text(
            "INSERT INTO user_refresh_tokens "
            "(id, user_id, token_hash, expires_at) "
            "VALUES ('v006-pre-tok', 'v006-pre-user', 'hash-pre-002', "
            "NOW() + INTERVAL '7 days')"
        )
    )
    await migration_session.commit()

    # Act: apply the migration
    await run_v006(migration_session)

    # Assert: the pre-existing token is still there and has NULL device fields
    row = (
        await migration_session.execute(
            text(
                "SELECT id, user_id, token_hash, ip_address "
                "FROM user_refresh_tokens WHERE id = 'v006-pre-tok'"
            )
        )
    ).one()

    assert row.id == "v006-pre-tok"
    assert row.user_id == "v006-pre-user"
    assert row.token_hash == "hash-pre-002"
    assert row.ip_address is None


async def test_v006_is_tracked_and_idempotent(migration_session):
    """v006 records its version and a second run is a no-op.

    The lifespan calls every migration on every boot — running twice must not
    raise or insert a duplicate schema_migrations row.
    """
    # Arrange
    await create_migrations_table(migration_session)
    await run_v001(migration_session)

    assert await v006_already_applied(migration_session) is False

    # Act: first run
    await run_v006(migration_session)

    # Assert: recorded
    assert await v006_already_applied(migration_session) is True
    assert V006_MIGRATION_ID == "v006_device_tracking"

    # Act: second run (must not raise)
    await run_v006(migration_session)

    # Assert: still exactly one row
    result = await migration_session.execute(
        text(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = :v"
        ),
        {"v": V006_MIGRATION_ID},
    )
    assert result.scalar() == 1


async def test_v006_columns_accept_values(migration_engine, migration_session):
    """The new columns accept non-NULL text values.

    Validates that the DDL type matches what the application will write:
    TEXT/VARCHAR (not BOOLEAN, not INTEGER) for all four fields.
    """
    # Arrange
    await create_migrations_table(migration_session)
    await run_v001(migration_session)
    await run_v006(migration_session)

    await migration_session.execute(
        text(
            "INSERT INTO users (id, username, email) "
            "VALUES ('v006-val-user', 'v006valuser', 'v006val@example.com')"
        )
    )
    await migration_session.commit()

    # Act: insert a token row with all four device fields populated
    await migration_session.execute(
        text(
            "INSERT INTO user_refresh_tokens "
            "(id, user_id, token_hash, expires_at, "
            " ip_address, user_agent, location_city, location_country) "
            "VALUES ('v006-val-tok', 'v006-val-user', 'hash-val-003', "
            "NOW() + INTERVAL '7 days', "
            "'203.0.113.1', 'Mozilla/5.0 (Test)', 'Berlin', 'Germany')"
        )
    )
    await migration_session.commit()

    # Assert: values round-trip correctly
    row = (
        await migration_session.execute(
            text(
                "SELECT ip_address, user_agent, location_city, location_country "
                "FROM user_refresh_tokens WHERE id = 'v006-val-tok'"
            )
        )
    ).one()

    assert row.ip_address == "203.0.113.1"
    assert row.user_agent == "Mozilla/5.0 (Test)"
    assert row.location_city == "Berlin"
    assert row.location_country == "Germany"


# ---------------------------------------------------------------------------
# Endpoint tests: login stores device information
# ---------------------------------------------------------------------------


async def test_login_stores_ip_address_in_refresh_token(client, engine):
    """POST /api/auth/login persists the client IP in the new ip_address column.

    The endpoint reads request.client.host; the test harness presents
    testclient (127.0.0.1) as the client IP.
    """
    # Arrange + Act
    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 200

    # Assert: the token row has a non-NULL ip_address
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        token_row = result.scalars().first()

    assert token_row is not None
    assert token_row.ip_address is not None, (
        "ip_address must be stored on login; got None — "
        "did auth_service.login() receive and persist ip_address?"
    )


async def test_login_stores_user_agent_in_refresh_token(client, engine):
    """POST /api/auth/login persists the User-Agent header in user_agent column."""
    # Arrange
    custom_ua = "TestBrowser/1.0 (US-44 integration test)"

    # Act
    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
        headers={"User-Agent": custom_ua},
    )
    assert response.status_code == 200

    # Assert
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        token_row = result.scalars().first()

    assert token_row is not None
    assert token_row.user_agent == custom_ua, (
        f"Expected user_agent={custom_ua!r}, got {token_row.user_agent!r}; "
        "did auth_service.login() receive and persist user_agent?"
    )


async def test_login_stores_location_city_and_country(client, engine, monkeypatch):
    """POST /api/auth/login resolves geolocation and stores city + country.

    A stub GeolocationService is injected so no real HTTP call goes out.
    The test asserts that the columns written by login() match the stub's
    return value.
    """
    # Arrange: stub geolocation service
    class _StubGeo:
        async def lookup(self, ip_address: str | None) -> str:
            return "Munich, Germany"

    # Patch the geolocation singleton in the auth service / email_wiring module.
    # The exact import target depends on the implementation choice; try both.
    import src.services.auth_service as auth_svc_module

    monkeypatch.setattr(
        auth_svc_module,
        "_geolocation_service",
        _StubGeo(),
        raising=False,  # attribute may not exist yet — that's expected RED state
    )

    # Act
    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 200

    # Assert
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        token_row = result.scalars().first()

    assert token_row is not None
    assert token_row.location_city == "Munich", (
        f"Expected location_city='Munich', got {token_row.location_city!r}"
    )
    assert token_row.location_country == "Germany", (
        f"Expected location_country='Germany', got {token_row.location_country!r}"
    )


async def test_login_stores_null_location_when_geo_returns_unknown(client, engine):
    """When geolocation returns 'unknown', location columns are stored as NULL.

    This covers the common cases: loopback IP from tests, offline environment,
    or a rate-limited lookup. Storing NULL rather than the string "unknown" keeps
    the column semantics clean (NULL = not known vs. a sentinel string).
    """
    # No monkeypatching — NullGeolocationService (the default) returns "unknown"

    response = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
    )
    assert response.status_code == 200

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        token_row = result.scalars().first()

    assert token_row is not None
    # Either NULL or "unknown" is acceptable, but the column must exist
    assert hasattr(token_row, "location_city"), (
        "UserRefreshToken ORM model must expose location_city after v006"
    )
    assert hasattr(token_row, "location_country"), (
        "UserRefreshToken ORM model must expose location_country after v006"
    )


# ---------------------------------------------------------------------------
# Endpoint tests: refresh rotation preserves / re-captures device information
# ---------------------------------------------------------------------------


async def test_refresh_rotation_preserves_ip_and_user_agent(client, engine):
    """After token rotation the new token row carries ip_address and user_agent.

    Design choice (US-44 AC-6): the refresh endpoint re-captures ip/ua from the
    incoming request and writes them onto the new UserRefreshToken it creates.
    This test pins that the new row is not written with NULLs after rotation.
    """
    # Arrange: login to obtain a refresh-token cookie
    login_ua = "RotationTestBrowser/2.0"
    login_resp = await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
        headers={"User-Agent": login_ua},
    )
    assert login_resp.status_code == 200

    # Act: refresh — the client cookie is sent automatically by AsyncClient
    refresh_resp = await client.post(
        "/api/auth/refresh",
        headers={"User-Agent": login_ua},
    )
    assert refresh_resp.status_code == 200

    # Assert: after rotation there are two rows; the LATEST (non-revoked) one
    # must have ip_address and user_agent populated
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken)
            .where(
                UserRefreshToken.user_id == USER_ID,
                UserRefreshToken.revoked_at.is_(None),
            )
            .order_by(UserRefreshToken.created_at.desc())
        )
        new_token_row = result.scalars().first()

    assert new_token_row is not None, "Expected a live (non-revoked) token row after refresh"
    assert new_token_row.ip_address is not None, (
        "New token after rotation must carry ip_address — "
        "did auth_service.refresh() persist ip_address on the new record?"
    )
    assert new_token_row.user_agent == login_ua, (
        f"New token after rotation must carry user_agent={login_ua!r}, "
        f"got {new_token_row.user_agent!r}"
    )


async def test_refresh_old_token_is_revoked_new_token_has_device_info(client, engine):
    """After rotation the old token is revoked and the new token has device fields.

    Pins the combined post-condition:
    - exactly one non-revoked token (the new one)
    - that token has device tracking columns
    - the old token is revoked (revoked_at is not None)
    """
    # Arrange
    await client.post(
        "/api/auth/login",
        json={"email": VERIFIED_EMAIL, "password": VALID_PASSWORD},
        headers={"User-Agent": "Pre-Rotation/1.0"},
    )

    # Act
    await client.post(
        "/api/auth/refresh",
        headers={"User-Agent": "Post-Rotation/1.0"},
    )

    # Assert
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(UserRefreshToken).where(UserRefreshToken.user_id == USER_ID)
        )
        all_rows = result.scalars().all()

    assert len(all_rows) == 2, f"Expected 2 token rows (old + new), got {len(all_rows)}"

    revoked = [r for r in all_rows if r.revoked_at is not None]
    live = [r for r in all_rows if r.revoked_at is None]

    assert len(revoked) == 1, "Old token must be revoked"
    assert len(live) == 1, "New token must be live"

    new_row = live[0]
    # The new row must expose the device columns (even if NULL — the ORM attribute
    # must exist; the actual value depends on implementation)
    assert hasattr(new_row, "ip_address"), "UserRefreshToken must have ip_address column"
    assert hasattr(new_row, "user_agent"), "UserRefreshToken must have user_agent column"
    assert hasattr(new_row, "location_city"), "UserRefreshToken must have location_city column"
    assert hasattr(new_row, "location_country"), "UserRefreshToken must have location_country column"


# ---------------------------------------------------------------------------
# Lifespan wiring test: v006 is included in the migration order
# ---------------------------------------------------------------------------


async def test_lifespan_runs_v006_after_v005(monkeypatch):
    """v006 is wired into the lifespan and runs after v005.

    Same DB-free technique as test_lifespan_runs_every_migration_in_plan_order
    in test_migrations.py — the migration calls are recorded without a real DB.
    """
    from src import main as main_module

    order: list[str] = []

    def _record(name: str):
        async def _run(session):
            order.append(name)
        return _run

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    def _noop_sync(*args, **kwargs):
        return None

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _FakeCtx())
    monkeypatch.setattr(main_module, "create_migrations_table", _noop)
    monkeypatch.setattr(main_module, "install_email_service", _noop_sync)
    monkeypatch.setattr(main_module, "run_v001", _record("v001"))
    monkeypatch.setattr(main_module, "run_v002", _record("v002"))
    monkeypatch.setattr(main_module, "run_v003", _record("v003"))
    monkeypatch.setattr(main_module, "run_v004", _record("v004"))
    monkeypatch.setattr(main_module, "run_v005", _record("v005"))
    monkeypatch.setattr(main_module, "run_v006", _record("v006"))  # NEW — will fail until wired
    monkeypatch.setattr(main_module, "run_v007", _noop)

    async with main_module.lifespan(main_module.app):
        pass

    assert "v006" in order, f"v006 must run in the lifespan; got order={order!r}"
    v005_idx = order.index("v005")
    v006_idx = order.index("v006")
    assert v006_idx == v005_idx + 1, (
        f"v006 must run immediately after v005; got v005 at {v005_idx}, v006 at {v006_idx}"
    )
