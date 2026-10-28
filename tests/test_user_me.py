"""Tests for ``GET /api/user/me``: identity plus the Sprint-4 economy numbers.

US-37 replaced the legacy ``points`` field with ``{total_xp, level,
current_streak, best_streak}`` and switched the read from ``user_stats`` to
``user_game_profile``, so this endpoint and ``GET /api/game/profile`` (US-35)
report the same numbers from the same row. The economy literals are copied from
``docs/sprint4-plan.md`` (D-1, US-33/US-35 golden fixture), never derived from
``src/``.
"""

from datetime import UTC, datetime

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from src.database.engine import Base, get_db
from src.main import app
from src.models.auth import User, UserIdentity
from src.models.game import UserGameProfile
from src.utils.jwt_utils import (
    JWT_ALGORITHM,
    create_access_token,
    create_refresh_token,
    get_jwt_secret,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

USER_ID = "user-me-001"
USER_EMAIL = "me@example.com"
USER_USERNAME = "meuser"

# The keys US-37 froze for this endpoint: identity, then the economy numbers.
ME_FIELDS: tuple[str, ...] = (
    "id",
    "username",
    "email",
    "correct_count",
    "incorrect_count",
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
)

# The six fields the two authenticated reads share (US-37 AC-1).
SHARED_ECONOMY_FIELDS: tuple[str, ...] = (
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
)

# Plan D-1 / US-33 golden fixture: 1320 XP IS level 6 (cum(6) = 1320).
ECONOMY_SEED: dict[str, int] = {
    "total_xp": 1320,
    "level": 6,
    "current_streak": 3,
    "best_streak": 7,
    "correct_count": 42,
    "incorrect_count": 5,
}


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
    db_session.add(User(id=USER_ID, username=USER_USERNAME, email=USER_EMAIL))
    db_session.add(
        UserIdentity(
            id="identity-me-001",
            user_id=USER_ID,
            provider="email",
            provider_user_id=USER_EMAIL,
            password_hash="secret-hash",
            verified_at=datetime.now(UTC),
        )
    )
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


def _bearer(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _add_profile(session: AsyncSession, user_id: str, **columns: int) -> None:
    """A ``user_game_profile`` row (the read source US-37 switched ``/me`` to)."""
    session.add(UserGameProfile(user_id=user_id, **columns))


async def test_me_returns_economy_zeros_before_the_first_answer(client):
    """US-37 AC-2: no profile row yet -> zeros at level 1, and no 500.

    The profile is created lazily by the first ``POST /api/game/answer`` (US-28
    AC-3), so ``/me`` must answer the missing row, not trip over it.
    """
    resp = await client.get("/api/user/me", headers=_bearer())

    assert resp.status_code == 200
    assert resp.json() == {
        "id": USER_ID,
        "username": USER_USERNAME,
        "email": USER_EMAIL,
        "correct_count": 0,
        "incorrect_count": 0,
        "total_xp": 0,
        "level": 1,  # D-1/OQ-3: a player starts at level 1 with 0 XP
        "current_streak": 0,
        "best_streak": 0,
    }
    assert "points" not in resp.text


async def test_me_returns_the_stored_economy(client, seeded_db):
    """US-37: the four economy fields come straight off the profile row."""
    _add_profile(seeded_db, USER_ID, **ECONOMY_SEED)
    await seeded_db.commit()

    resp = await client.get("/api/user/me", headers=_bearer())

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_xp"] == 1320
    assert data["level"] == 6  # cum(6) = 1320, plan D-1
    assert data["current_streak"] == 3
    assert data["best_streak"] == 7
    assert data["correct_count"] == 42
    assert data["incorrect_count"] == 5


async def test_me_reports_the_economy_profile_not_the_legacy_counters(
    client, seeded_db
):
    """D-6: ``user_stats`` no longer feeds ``/me``.

    A streak that was never earned and counts that were never answered must not
    appear: the row behind these numbers is ``user_game_profile`` and nothing else.
    """
    _add_profile(
        seeded_db,
        USER_ID,
        total_xp=48,
        level=1,
        current_streak=1,
        best_streak=1,
        correct_count=1,
        incorrect_count=0,
    )
    await seeded_db.commit()

    data = (await client.get("/api/user/me", headers=_bearer())).json()

    assert (data["total_xp"], data["level"]) == (48, 1)
    assert (data["current_streak"], data["best_streak"]) == (1, 1)
    assert (data["correct_count"], data["incorrect_count"]) == (1, 0)


async def test_me_field_set_is_frozen_and_leaks_no_hash(client):
    """US-37 AC-4: exactly the nine keys — no ``points``, no password material."""
    resp = await client.get("/api/user/me", headers=_bearer())

    assert set(resp.json()) == set(ME_FIELDS)
    assert "points" not in resp.json()
    assert "secret-hash" not in resp.text


async def test_me_and_profile_agree_field_for_field(client, seeded_db):
    """US-37 AC-1 / US-35 AC-1: the SHARED FIXTURE pin.

    One seeded row, two endpoints, six identical numbers — this is the test that
    makes the two read sides one contract rather than two implementations that
    happen to look alike today. It also pins the status code alignment of Rev 3
    R-10: both authenticated reads answer the same identity questions the same way.
    """
    _add_profile(seeded_db, USER_ID, **ECONOMY_SEED)
    await seeded_db.commit()

    me = (await client.get("/api/user/me", headers=_bearer())).json()
    profile = (await client.get("/api/game/profile", headers=_bearer())).json()

    assert {field: me[field] for field in SHARED_ECONOMY_FIELDS} == {
        field: profile[field] for field in SHARED_ECONOMY_FIELDS
    }
    assert me["total_xp"] == 1320 and profile["total_xp"] == 1320
    assert me["level"] == profile["level"] == 6
    # The profile's progress pair is the same total's D-1 position, so the two
    # endpoints cannot report a level that contradicts ``xp_in_level``.
    assert profile["xp_in_level"] == 0  # cum(6) = 1320
    assert profile["xp_required_for_next_level"] == 760  # req(6)
    assert len(profile["unlocks"]) == 10  # the D-2 filter at L6


async def test_me_and_profile_agree_on_a_missing_profile_too(client, seeded_db):
    """The zero state is shared too: the agreement is not only for rows that exist."""
    me = (await client.get("/api/user/me", headers=_bearer())).json()
    profile = (await client.get("/api/game/profile", headers=_bearer())).json()

    assert {field: me[field] for field in SHARED_ECONOMY_FIELDS} == {
        field: profile[field] for field in SHARED_ECONOMY_FIELDS
    }
    assert [me[field] for field in SHARED_ECONOMY_FIELDS] == [0, 1, 0, 0, 0, 0]


async def test_me_without_token_returns_401(client):
    resp = await client.get("/api/user/me")

    assert resp.status_code == 401


async def test_me_with_invalid_token_returns_401(client):
    resp = await client.get(
        "/api/user/me", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert resp.status_code == 401


async def test_me_rejects_expired_token(client):
    payload = {
        "sub": USER_ID,
        "type": "access",
        "exp": datetime.now(UTC).timestamp() - 60,
        "iat": datetime.now(UTC).timestamp() - 1200,
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    resp = await client.get(
        "/api/user/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token has expired."


async def test_me_rejects_refresh_token(client):
    token = create_refresh_token(USER_ID)
    resp = await client.get(
        "/api/user/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token type."


async def test_me_for_unknown_user_returns_404(client):
    """US-37 AC-3 / Rev 3 R-10: a vanished identity is a 404, not a 401.

    401 is reserved for a missing or invalid token; the token here is perfectly
    valid, the user it names is gone. ``/api/game/answer`` (US-33) and
    ``/api/game/profile`` (US-35) already answer 404 for this exact condition, and
    the three must not diverge — a client cannot tell "log in again" from "this
    account no longer exists" apart from the code.
    """
    resp = await client.get("/api/user/me", headers=_bearer("does-not-exist"))

    assert resp.status_code == 404
    assert resp.json()["detail"] == "User not found."
