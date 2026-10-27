"""Tests for US-06: Logout-all Sessions.

Covers the integration and adversarial cases from docs/auth-hardening-test-plan.md
(section "US-06 · Logout-all Sessions").

Database strategy: in-memory SQLite via a local `engine` fixture (same pattern as
tests/test_user_me.py and tests/test_auth_login.py), so these tests do not depend
on the PostgreSQL instance from docker-compose.test.yaml.

Note on stale identity maps: `logout_all` issues a bulk UPDATE, so any session
that already loaded token objects would see stale `revoked_at` values after the
commit (expire_on_commit=False). Every DB assertion in this file therefore opens
a FRESH session through the shared engine instead of reusing the seeding session.
"""

import hashlib
import json
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
from src.utils.jwt_utils import create_access_token, create_refresh_token
from src.utils.password import hash_password

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_A = "user-a-001"
USER_B = "user-b-002"
EMAIL_A = "logout-a@example.com"
EMAIL_B = "logout-b@example.com"
PASSWORD = "Password1!"

ACTIVE_TOKEN_COUNT = 5  # "devices" of user A (test plan: 5 active tokens)
VICTIM_TOKEN_COUNT = 2  # "devices" of user B

EXPECTED_MESSAGE = "All sessions revoked."
AUDIT_ACTION = "all_sessions_revoked"
CLIENT_IP = "127.0.0.1"  # httpx ASGITransport default peer address


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bearer(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _token_rows(engine, user_id: str | None = None) -> list[UserRefreshToken]:
    """Read token rows in a fresh session (bulk UPDATE bypasses identity maps)."""
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        stmt = select(UserRefreshToken)
        if user_id is not None:
            stmt = stmt.where(UserRefreshToken.user_id == user_id)
        result = await session.execute(stmt.order_by(UserRefreshToken.id))
        return list(result.scalars().all())


async def _audit_rows(engine, user_id: str) -> list[UserAuditLog]:
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        result = await session.execute(
            select(UserAuditLog)
            .where(UserAuditLog.user_id == user_id)
            .order_by(UserAuditLog.id)
        )
        return list(result.scalars().all())


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


async def _add_refresh_tokens(
    session: AsyncSession,
    user_id: str,
    count: int,
    *,
    prefix: str = "active",
    expires_in_days: int = 7,
    revoked: bool = False,
) -> None:
    """Seed refresh-token rows the way `login` would have persisted them."""
    now = datetime.now(UTC)
    for i in range(count):
        key = f"{prefix}-{user_id}-{i}"
        session.add(
            UserRefreshToken(
                id=key,
                user_id=user_id,
                token_hash=hashlib.sha256(key.encode()).hexdigest(),
                expires_at=now + timedelta(days=expires_in_days),
                revoked_at=now if revoked else None,
            )
        )


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
    """Two verified users, no refresh tokens yet (login-driven tests)."""
    await _add_user(db_session, USER_A, EMAIL_A)
    await _add_user(db_session, USER_B, EMAIL_B)
    await db_session.commit()
    yield db_session


@pytest.fixture
async def seeded_db(seed_users):
    """User A with ACTIVE_TOKEN_COUNT sessions, user B with VICTIM_TOKEN_COUNT."""
    await _add_refresh_tokens(seed_users, USER_A, ACTIVE_TOKEN_COUNT)
    await _add_refresh_tokens(seed_users, USER_B, VICTIM_TOKEN_COUNT)
    await seed_users.commit()
    yield seed_users


@pytest.fixture
async def client(engine):
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
# integration tests
# ---------------------------------------------------------------------------


async def test_logout_all_revokes_all_tokens(client, engine, seeded_db):
    """Given 5 active tokens, POST /logout-all revokes all 5 and returns count 5."""
    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    assert response.json()["count"] == ACTIVE_TOKEN_COUNT

    rows = await _token_rows(engine, USER_A)
    assert len(rows) == ACTIVE_TOKEN_COUNT
    assert all(row.revoked_at is not None for row in rows)


async def test_logout_all_response_format(client, seeded_db):
    """Body is exactly {message, count} with the spec'd message text."""
    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    assert response.json() == {"message": EXPECTED_MESSAGE, "count": ACTIVE_TOKEN_COUNT}


async def test_logout_all_requires_auth(client, seeded_db):
    """Without an Authorization header the endpoint returns 401."""
    response = await client.post("/api/auth/logout-all")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid Authorization header."


async def test_logout_all_without_token_revokes_nothing(client, engine, seeded_db):
    """The unauthenticated call must be rejected and touch no session."""
    response = await client.post("/api/auth/logout-all")

    assert response.status_code == 401
    rows = await _token_rows(engine, USER_A)
    assert all(row.revoked_at is None for row in rows)


async def test_logout_all_with_invalid_token_returns_401(client, engine, seeded_db):
    """A malformed Bearer token returns 401 and revokes nothing."""
    response = await client.post(
        "/api/auth/logout-all", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token."
    rows = await _token_rows(engine, USER_A)
    assert all(row.revoked_at is None for row in rows)


async def test_logout_all_rejects_refresh_token(client, engine, seeded_db):
    """A refresh-type JWT is not accepted as access credential (401)."""
    token = create_refresh_token(USER_A)
    response = await client.post(
        "/api/auth/logout-all", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token type."
    rows = await _token_rows(engine, USER_A)
    assert all(row.revoked_at is None for row in rows)


async def test_logout_all_creates_audit_event(client, engine, seeded_db):
    """Audit row `all_sessions_revoked` carries user_id, ip and session_count."""
    response = await client.post(
        "/api/auth/logout-all",
        headers={**_bearer(USER_A), "User-Agent": "pytest-agent/1.0"},
    )

    assert response.status_code == 200
    entries = [a for a in await _audit_rows(engine, USER_A) if a.action == AUDIT_ACTION]
    assert len(entries) == 1
    audit = entries[0]
    assert audit.user_id == USER_A
    assert audit.ip_address == CLIENT_IP
    assert audit.user_agent == "pytest-agent/1.0"
    assert json.loads(audit.metadata_json) == {"session_count": ACTIVE_TOKEN_COUNT}


async def test_logout_all_after_two_logins_revokes_both_devices(
    client, engine, seed_users
):
    """AC 5: two logins (= two devices) -> logout-all -> both refresh tokens dead."""
    first = await client.post(
        "/api/auth/login", json={"email": EMAIL_A, "password": PASSWORD}
    )
    second = await client.post(
        "/api/auth/login", json={"email": EMAIL_A, "password": PASSWORD}
    )
    assert first.status_code == 200 and second.status_code == 200
    device_tokens = [
        first.cookies.get("refresh_token"),
        second.cookies.get("refresh_token"),
    ]
    assert all(device_tokens)

    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    assert response.json()["count"] == 2
    rows = await _token_rows(engine, USER_A)
    assert len(rows) == 2
    assert all(row.revoked_at is not None for row in rows)

    for raw_token in device_tokens:
        refreshed = await client.post(
            "/api/auth/refresh", cookies={"refresh_token": raw_token}
        )
        assert refreshed.status_code == 401


async def test_refresh_after_logout_all_returns_401(client, seed_users):
    """A session killed by logout-all cannot mint new access tokens anymore."""
    login = await client.post(
        "/api/auth/login", json={"email": EMAIL_A, "password": PASSWORD}
    )
    raw_token = login.cookies.get("refresh_token")

    await client.post("/api/auth/logout-all", headers=_bearer(USER_A))
    response = await client.post(
        "/api/auth/refresh", cookies={"refresh_token": raw_token}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# adversarial tests
# ---------------------------------------------------------------------------


async def test_logout_all_leaves_other_users_sessions_untouched(
    client, engine, seeded_db
):
    """Access token of user A must never revoke user B's sessions."""
    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    victim_rows = await _token_rows(engine, USER_B)
    assert len(victim_rows) == VICTIM_TOKEN_COUNT
    assert all(row.revoked_at is None for row in victim_rows)


async def test_logout_all_idempotent(client, engine, seeded_db):
    """Second call returns 200 with count 0 and adds no duplicate audit entry."""
    first = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))
    second = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert first.status_code == 200
    assert first.json()["count"] == ACTIVE_TOKEN_COUNT
    assert second.status_code == 200
    assert second.json() == {"message": EXPECTED_MESSAGE, "count": 0}

    audits = [a for a in await _audit_rows(engine, USER_A) if a.action == AUDIT_ACTION]
    assert len(audits) == 1


async def test_logout_all_revokes_expired_tokens_too(client, engine, seed_users):
    """3 tokens of which 2 are already expired: all 3 get revoked, count == 3."""
    await _add_refresh_tokens(seed_users, USER_A, 1, prefix="live")
    await _add_refresh_tokens(seed_users, USER_A, 2, prefix="dead", expires_in_days=-1)
    await seed_users.commit()

    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    assert response.json()["count"] == 3
    rows = await _token_rows(engine, USER_A)
    assert len(rows) == 3
    assert all(row.revoked_at is not None for row in rows)


async def test_logout_all_skips_already_revoked_tokens(client, engine, seed_users):
    """Already revoked tokens are not counted twice."""
    await _add_refresh_tokens(seed_users, USER_A, 2, prefix="fresh")
    await _add_refresh_tokens(seed_users, USER_A, 3, prefix="gone", revoked=True)
    await seed_users.commit()

    response = await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    assert response.status_code == 200
    assert response.json()["count"] == 2


async def test_logout_all_never_leaves_active_token(client, engine, seeded_db):
    """Invariant behind the race scenario: after logout-all no session is open."""
    await client.post("/api/auth/logout-all", headers=_bearer(USER_A))

    rows = await _token_rows(engine, USER_A)
    assert rows, "fixture should have seeded tokens"
    assert not any(row.revoked_at is None for row in rows)


async def test_logout_all_for_unknown_user_returns_zero_without_audit(
    client, engine, seeded_db
):
    """A valid token for a non-existent user is a no-op: count 0, no audit row."""
    response = await client.post("/api/auth/logout-all", headers=_bearer("ghost-user"))

    assert response.status_code == 200
    assert response.json() == {"message": EXPECTED_MESSAGE, "count": 0}
    assert await _audit_rows(engine, "ghost-user") == []


# ---------------------------------------------------------------------------
# service unit test
# ---------------------------------------------------------------------------


async def test_logout_all_service_returns_revoked_count(engine, seeded_db):
    """auth_service.logout_all() returns the number of tokens it revoked."""
    count = await auth_service.logout_all(seeded_db, USER_A)

    assert count == ACTIVE_TOKEN_COUNT
    rows = await _token_rows(engine, USER_A)
    assert all(row.revoked_at is not None for row in rows)
