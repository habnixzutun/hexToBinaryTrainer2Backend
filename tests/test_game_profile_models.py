"""US-26: domain models for the Sprint-4 XP economy (ORM + Pydantic wire schemas).

Two halves:

* the **model trio** — ``UserGameProfile`` (new, table ``user_game_profile``) and
  the legacy ``UserStats`` (kept, plan D-6 / Rev 3 R-1: v004 is ADDITIVE, only
  US-34 may delete ``UserStats``), verified against a real PostgreSQL 16 catalog:
  columns, defaults, CHECK names, the ``total_xp DESC`` index, cascade delete and
  side-by-side coexistence of the two tables (US-26 AC-2/AC-3);
* the **schema quartet** in ``src/models/game_schemas.py`` — ``AnswerRequest``
  (per-mode ``bit_width`` validation in BITS for every mode, OQ-7), and the
  response contracts frozen by US-33/US-35/US-36 (``AnswerResponse``,
  ``ProfileResponse``, ``LeaderboardEntryV2`` + wrapper).

Expected values below are **literals copied from docs/sprint4-plan.md** (US-26,
D-6/D-7, OQ-7/OQ-8, US-33 golden fixture, R-9 unlock shape) and deliberately not
derived from ``src/``, so a wrong constant cannot hide behind a wrong expectation
— same rule as ``tests/test_game_config.py``.

The 422 behaviour is probed through a throwaway FastAPI app (never
``src/routers/game.py``, which stays untouched until US-34): the per-mode width
rule must surface as an HTTP 422 exactly once US-33 mounts ``AnswerRequest`` on
``POST /api/game/answer``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from src.models import UserStats as UserStatsFromPackage
from src.models.auth import User
from src.models.game import UserGameProfile, UserStats
from src.models.game_schemas import (
    AnswerRequest,
    AnswerResponse,
    LeaderboardEntryV2,
    LeaderboardResponseV2,
    ProfileResponse,
    UnlockEntry,
)
from src.models.game_schemas import __all__ as SCHEMA_ALL
from uuid_utils import uuid7

# --------------------------------------------------------------------------- #
# Expectations — literals from the plan (never imported from src/)
# --------------------------------------------------------------------------- #

# plan D-7 / OQ-7 (Rev 4): bit_width is BITS in every mode.
CONVERSION_WIDTHS: tuple[int, ...] = (4, 8, 16, 32)
UNICODE_WIDTHS: tuple[int, ...] = (8, 16, 24, 32)
MODES: tuple[str, ...] = ("hex_to_bin", "hex_to_dec", "bin_to_dec", "unicode")

WIDTHS_BY_MODE: dict[str, tuple[int, ...]] = {
    "hex_to_bin": CONVERSION_WIDTHS,
    "hex_to_dec": CONVERSION_WIDTHS,
    "bin_to_dec": CONVERSION_WIDTHS,
    "unicode": UNICODE_WIDTHS,
}

# US-26 story text: the exact column list of user_game_profile.
PROFILE_COLUMNS: tuple[str, ...] = (
    "user_id",
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
    "updated_at",
)

# plan US-33 (OQ-8 rename, R-9 unlock shape) — response fields, in contract order.
ANSWER_RESPONSE_FIELDS: tuple[str, ...] = (
    "xp_delta",
    "total_xp",
    "level",
    "xp_in_level",
    "xp_required_for_next_level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
    "newly_unlocked",
)

# plan US-35 — profile fields, in contract order.
PROFILE_RESPONSE_FIELDS: tuple[str, ...] = (
    "total_xp",
    "level",
    "xp_in_level",
    "xp_required_for_next_level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
    "unlocks",
)

# plan US-36 — leaderboard entry shape (drops `points`, carries no PII).
LEADERBOARD_ENTRY_FIELDS: tuple[str, ...] = (
    "user_id",
    "username",
    "total_xp",
    "level",
    "correct_count",
    "incorrect_count",
)

# plan US-33: the normative golden fixture ("verify these hold in the schema test").
GOLDEN_ANSWER_RESPONSE: dict = {
    "xp_delta": 40,
    "total_xp": 1320,
    "level": 6,
    "xp_in_level": 0,
    "xp_required_for_next_level": 760,
    "current_streak": 3,
    "best_streak": 7,
    "correct_count": 42,
    "incorrect_count": 5,
    "newly_unlocked": [],
}

# plan D-2 / §4.9 — one seed row, pinned as the R-9 element shape.
GOLDEN_UNLOCK_ROW: dict = {"mode": "hex_to_bin", "bit_width": 16, "min_level": 8}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _check_constraints(sync_conn) -> dict[str, str]:
    """{constraint name: definition} for user_game_profile, from pg_constraint."""
    rows = sync_conn.execute(
        text(
            "SELECT con.conname, pg_get_constraintdef(con.oid) AS defn "
            "FROM pg_constraint con "
            "JOIN pg_class cls ON cls.oid = con.conrelid "
            "WHERE cls.relname = 'user_game_profile' AND con.contype = 'c' "
            "ORDER BY con.conname"
        )
    ).fetchall()
    return dict(rows)


def _index_definitions(sync_conn) -> dict[str, str]:
    """{index name: indexdef} for user_game_profile, from pg_indexes."""
    rows = sync_conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'user_game_profile'"
        )
    ).fetchall()
    return dict(rows)


COLUMN_ORDER_SQL = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'user_game_profile' ORDER BY ordinal_position"
)


def _column_names(sync_conn) -> list[str]:
    """Column order of user_game_profile as Postgres actually stores it."""
    return [row[0] for row in sync_conn.execute(COLUMN_ORDER_SQL).fetchall()]


async def _seed_user(session, user_id: str) -> User:
    """A unique user row: the shared Postgres DB keeps rows across tests.

    The whole UUID is used, not a prefix — uuid7's leading 48 bits are the
    millisecond timestamp, so consecutive ids inside one millisecond share their
    first characters and would collide on the UNIQUE username/email columns.
    """
    token = user_id.replace("-", "")
    user = User(
        id=user_id,
        username=f"us26-{token[:20]}",
        email=f"{token}@example.com",
    )
    session.add(user)
    await session.commit()
    return user


# --------------------------------------------------------------------------- #
# A. ORM: shape, coexistence, D-6 survival (US-26 AC-2 / AC-3)
# --------------------------------------------------------------------------- #


def test_user_stats_survives_us26():
    """AC-2: UserStats is NOT removed here — D-6/R-1 reserves that for US-34.

    ``src/routers/game.py`` and ``src/routers/user.py`` still import it, and
    ``main.py`` imports the game router, so a deleted class would stop the app
    from booting (sprint gate #1).
    """
    assert UserStats.__tablename__ == "user_stats"
    assert {"user_id", "correct_count", "incorrect_count", "points"} <= {
        col.name for col in UserStats.__table__.columns
    }
    # still exported from the package (tests/test_user_me.py imports it)
    assert UserStatsFromPackage is UserStats


def test_user_game_profile_exported_from_package():
    """US-26: exported from ``src.models`` and listed in ``__all__``."""
    import src.models as models_package

    assert models_package.UserGameProfile is UserGameProfile
    assert "UserGameProfile" in models_package.__all__
    assert "UserStats" in models_package.__all__  # kept (D-6)


def test_profile_table_and_columns_match_the_story():
    """US-26 AC-1: table name and the exact column list."""
    assert UserGameProfile.__tablename__ == "user_game_profile"
    columns = tuple(col.name for col in UserGameProfile.__table__.columns)
    assert columns == PROFILE_COLUMNS


def test_profile_primary_key_is_the_fk_to_users_with_cascade():
    """user_id is PK *and* FK -> users.id ON DELETE CASCADE."""
    table = UserGameProfile.__table__
    pk_columns = [col.name for col in table.primary_key.columns]
    assert pk_columns == ["user_id"]

    fk = next(iter(table.foreign_keys))
    assert fk.target_fullname == "users.id"
    assert fk.ondelete == "CASCADE"


def test_profile_economy_columns_are_not_null():
    """Every economy counter is NOT NULL — a NULL would poison the level math."""
    table = UserGameProfile.__table__
    for name in PROFILE_COLUMNS:
        assert table.c[name].nullable is False, name


def test_profile_defaults_are_zero_and_level_one():
    """Sensible defaults (plan US-26): 0 everywhere, level 1, XP 0 (D-1/OQ-3)."""
    table = UserGameProfile.__table__
    assert table.c.total_xp.default.arg == 0
    assert table.c.current_streak.default.arg == 0
    assert table.c.best_streak.default.arg == 0
    assert table.c.correct_count.default.arg == 0
    assert table.c.incorrect_count.default.arg == 0
    assert table.c.level.default.arg == 1


def test_models_coexist_without_name_or_table_collision():
    """US-26 AC-3: both classes on the same MetaData, two distinct tables."""
    metadata = UserGameProfile.metadata
    assert metadata is UserStats.metadata
    assert "user_game_profile" in metadata.tables
    assert "user_stats" in metadata.tables
    assert metadata.tables["user_game_profile"] is not metadata.tables["user_stats"]

    # class names differ from each other and from the table names
    assert UserGameProfile.__tablename__ != UserStats.__tablename__
    assert UserGameProfile.__name__ != UserStats.__name__


# --------------------------------------------------------------------------- #
# B. ORM against PostgreSQL: DDL, CHECKs, index, defaults, cascade
# --------------------------------------------------------------------------- #


async def test_pg_creates_all_eight_columns(session):
    """The ORM DDL reaches Postgres with exactly the story's column list."""
    columns = await session.run_sync(_column_names)

    assert tuple(columns) == PROFILE_COLUMNS


async def test_pg_declares_the_four_named_checks(session):
    """US-26: total_xp >= 0, level >= 1, current_streak >= 0, best_streak >= 0.

    Names are explicit so migration v004 (US-27) can mirror them byte-for-byte.
    """
    constraints = await session.run_sync(_check_constraints)

    assert set(constraints) == {
        "ck_user_game_profile_total_xp",
        "ck_user_game_profile_level",
        "ck_user_game_profile_current_streak",
        "ck_user_game_profile_best_streak",
    }
    defs_by_name = constraints
    assert "total_xp >= 0" in defs_by_name["ck_user_game_profile_total_xp"]
    assert "level >= 1" in defs_by_name["ck_user_game_profile_level"]
    assert "current_streak >= 0" in defs_by_name["ck_user_game_profile_current_streak"]
    assert "best_streak >= 0" in defs_by_name["ck_user_game_profile_best_streak"]


async def test_pg_total_xp_index_is_descending(session):
    """Index on ``total_xp DESC`` for the US-36 leaderboard ordering."""
    indexes = await session.run_sync(_index_definitions)

    assert "ix_user_game_profile_total_xp" in indexes
    indexdef = indexes["ix_user_game_profile_total_xp"]
    assert "total_xp DESC" in indexdef
    assert indexdef.startswith("CREATE INDEX"), indexdef  # not UNIQUE, not PK


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("total_xp", -4),  # D-5: the DB backstop (real floor is cum(level), OQ-4)
        ("level", 0),  # D-1: players start at L1, never L0
        ("current_streak", -1),  # D-5: no negative-streak concept
        ("best_streak", -1),
    ],
)
async def test_pg_rejects_invalid_counters(session, column, value):
    """The four CHECKs of US-26 fire on bad data written straight via SQL.

    Exactly the four the story pins: ``correct_count``/``incorrect_count``
    deliberately get no CHECK — they are guarded by the ``ge=0`` wire schemas and
    by ``game_service``, and a sixth constraint would force v004 to mirror
    something the spec does not ask for.
    """
    user_id = str(uuid7())
    await _seed_user(session, user_id)

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                f"INSERT INTO user_game_profile (user_id, {column}) "
                "VALUES (:uid, :value)"
            ),
            {"uid": user_id, "value": value},
        )
        await session.commit()
    await session.rollback()


async def test_pg_row_defaults_apply(session):
    """A bare INSERT (no economy columns) lands at 0 XP / level 1 / streak 0."""
    user_id = str(uuid7())
    await _seed_user(session, user_id)
    await session.execute(
        text("INSERT INTO user_game_profile (user_id) VALUES (:uid)"),
        {"uid": user_id},
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT total_xp, level, current_streak, best_streak, "
                "correct_count, incorrect_count, updated_at "
                "FROM user_game_profile WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).one()
    assert row.total_xp == 0
    assert row.level == 1
    assert (row.current_streak, row.best_streak) == (0, 0)
    assert (row.correct_count, row.incorrect_count) == (0, 0)
    assert row.updated_at is not None


async def test_pg_updated_at_advances_on_update(session):
    """``updated_at`` is maintained on UPDATE (not only on insert)."""
    user_id = str(uuid7())
    await _seed_user(session, user_id)
    session.add(UserGameProfile(user_id=user_id))
    await session.commit()

    before = (
        await session.execute(
            select(UserGameProfile.updated_at).where(UserGameProfile.user_id == user_id)
        )
    ).scalar_one()

    profile = (
        await session.execute(
            select(UserGameProfile).where(UserGameProfile.user_id == user_id)
        )
    ).scalar_one()
    profile.total_xp = 40
    await session.commit()

    after = (
        await session.execute(
            select(UserGameProfile.updated_at).where(UserGameProfile.user_id == user_id)
        )
    ).scalar_one()
    assert after > before


async def test_pg_deleting_the_user_cascades(session):
    """US-27 AC-4 (also proven at model level): FK ON DELETE CASCADE."""
    user_id = str(uuid7())
    await _seed_user(session, user_id)
    session.add(UserGameProfile(user_id=user_id, total_xp=100, level=2))
    await session.commit()

    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await session.commit()

    remaining = (
        await session.execute(
            text("SELECT COUNT(*) FROM user_game_profile WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()
    assert remaining == 0


async def test_pg_orphan_user_id_is_rejected(session):
    """The FK is enforced: no profile row without a user."""
    with pytest.raises(IntegrityError):
        await session.execute(
            text("INSERT INTO user_game_profile (user_id) VALUES (:uid)"),
            {"uid": str(uuid7())},
        )
        await session.commit()
    await session.rollback()


async def test_pg_both_tables_hold_rows_for_the_same_user(session):
    """US-26 AC-3: the legacy table and the new one coexist per user (D-6)."""
    user_id = str(uuid7())
    await _seed_user(session, user_id)
    session.add_all(
        [
            UserStats(user_id=user_id, correct_count=3, incorrect_count=1, points=12),
            UserGameProfile(
                user_id=user_id, total_xp=128, level=2, current_streak=2, best_streak=2
            ),
        ]
    )
    await session.commit()

    stats = (
        await session.execute(select(UserStats).where(UserStats.user_id == user_id))
    ).scalar_one()
    profile = (
        await session.execute(
            select(UserGameProfile).where(UserGameProfile.user_id == user_id)
        )
    ).scalar_one()

    assert stats.points == 12  # legacy column untouched
    assert profile.total_xp == 128
    assert profile.level == 2


# --------------------------------------------------------------------------- #
# C. AnswerRequest — per-mode width validation (D-7 / OQ-7)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("slot", (0, 1, 2, 3))
def test_answer_request_accepts_every_plan_combo(mode, slot):
    """All 16 (mode, width) rows of plan §4.2 are accepted."""
    bit_width = WIDTHS_BY_MODE[mode][slot]
    request = AnswerRequest(mode=mode, bit_width=bit_width, is_correct=True)

    assert request.mode == mode
    assert request.bit_width == bit_width
    assert request.is_correct is True


def test_unicode_slot_one_is_eight_bits_not_four():
    """OQ-7 (Rev 4): unicode speaks BITS — 8 is valid slot 1, 4 is not a width."""
    assert AnswerRequest(mode="unicode", bit_width=8, is_correct=True).bit_width == 8

    with pytest.raises(ValidationError) as excinfo:
        AnswerRequest(mode="unicode", bit_width=4, is_correct=True)
    assert "unicode" in str(excinfo.value)


@pytest.mark.parametrize("mode", ("hex_to_bin", "hex_to_dec", "bin_to_dec"))
def test_conversion_modes_reject_24_bits(mode):
    """24 is a unicode width and NOT a conversion width — no global width set."""
    with pytest.raises(ValidationError):
        AnswerRequest(mode=mode, bit_width=24, is_correct=True)


def test_unicode_accepts_24_bits():
    """The mirror case: 24 is valid for unicode (3 bytes)."""
    assert AnswerRequest(mode="unicode", bit_width=24, is_correct=False).bit_width == 24


def test_answer_request_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        AnswerRequest(mode="bin_to_hex", bit_width=8, is_correct=True)


def test_answer_request_requires_is_correct():
    with pytest.raises(ValidationError):
        AnswerRequest(mode="hex_to_bin", bit_width=8)


@pytest.mark.parametrize("junk", ["true", "yes", 1, 0, 2, None, [True]])
def test_answer_request_rejects_non_bool_is_correct(junk):
    """Strict bool: the XP write path never coerces client truthiness (US-32 AC-1)."""
    with pytest.raises(ValidationError):
        AnswerRequest(mode="hex_to_bin", bit_width=8, is_correct=junk)


@pytest.mark.parametrize("junk", [True, False, "8", 8.0, 8.5, None])
def test_answer_request_rejects_non_int_bit_width(junk):
    """Strict int: ``bit_width: true`` must not become width 1."""
    with pytest.raises(ValidationError):
        AnswerRequest(mode="hex_to_bin", bit_width=junk, is_correct=True)


@pytest.mark.parametrize(
    "field", ["bits", "track", "xp", "total_xp", "streak", "points"]
)
def test_answer_request_forbids_extra_fields(field):
    """extra="forbid": the client sends ONLY {mode, bit_width, is_correct} (D-7)."""
    with pytest.raises(ValidationError):
        AnswerRequest(
            mode="hex_to_bin",
            bit_width=8,
            is_correct=True,
            **{field: 1},
        )


def test_answer_request_rejects_legacy_payload():
    """The Sprint-2 body ``{bits: 8}`` is a hard rejection, not a silent pass."""
    with pytest.raises(ValidationError):
        AnswerRequest.model_validate({"bits": 8})


def test_width_error_message_names_mode_and_allowed_widths():
    """The 422 detail must tell the client what the mode accepts (US-32/US-38)."""
    with pytest.raises(ValidationError) as excinfo:
        AnswerRequest(mode="unicode", bit_width=4, is_correct=True)

    message = str(excinfo.value)
    assert "4" in message
    assert "8" in message and "16" in message and "24" in message and "32" in message


# --------------------------------------------------------------------------- #
# D. AnswerRequest through the HTTP layer: 422, not 400/500
# --------------------------------------------------------------------------- #


def _probe_app() -> FastAPI:
    """Minimal app mounting the US-26 request schema (US-33 will do the same)."""
    app = FastAPI()

    @app.post("/probe/answer")
    async def probe(payload: AnswerRequest) -> dict:
        return payload.model_dump()

    return app


async def _post_probe(body: dict) -> tuple[int, dict]:
    transport = ASGITransport(app=_probe_app())
    async with AsyncClient(transport=transport, base_url="http://probe") as client:
        response = await client.post("/probe/answer", json=body)
    return response.status_code, response.json()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"mode": "unicode", "bit_width": 4, "is_correct": True}, 422),
        ({"mode": "unicode", "bit_width": 8, "is_correct": True}, 200),
        ({"mode": "unicode", "bit_width": 24, "is_correct": False}, 200),
        ({"mode": "hex_to_bin", "bit_width": 24, "is_correct": True}, 422),
        ({"mode": "hex_to_bin", "bit_width": 4, "is_correct": True}, 200),
        ({"mode": "bin_to_hex", "bit_width": 8, "is_correct": True}, 422),
        ({"mode": "hex_to_bin", "bit_width": 8}, 422),
        ({"mode": "hex_to_bin", "bit_width": 8, "is_correct": "true"}, 422),
        ({"mode": "hex_to_bin", "bit_width": 8, "is_correct": True, "xp": 999}, 422),
        ({"bits": 8}, 422),
    ],
)
async def test_http_validation_status_codes(body, expected):
    """Every US-32 rule surfaces as 422 (FastAPI) — never 400, never 500."""
    status_code, payload = await _post_probe(body)

    assert status_code == expected, payload
    if expected == 422:
        assert payload["detail"], payload


# --------------------------------------------------------------------------- #
# E. Response contracts (US-33 / US-35 / US-36)
# --------------------------------------------------------------------------- #


def test_answer_response_field_set_is_exact():
    """US-33 contract, byte-for-byte: names and order (OQ-8 rename included)."""
    assert tuple(AnswerResponse.model_fields) == ANSWER_RESPONSE_FIELDS
    assert "xp_required_for_level" not in AnswerResponse.model_fields


def test_answer_response_accepts_the_golden_fixture():
    """plan US-33: the normative example is a valid AnswerResponse."""
    response = AnswerResponse.model_validate(GOLDEN_ANSWER_RESPONSE)

    assert response.model_dump() == GOLDEN_ANSWER_RESPONSE
    assert response.xp_required_for_next_level == 760
    assert response.newly_unlocked == []


def test_answer_response_rejects_the_old_wire_name():
    """OQ-8: a stale ``xp_required_for_level`` key is refused, not ignored."""
    stale = {**GOLDEN_ANSWER_RESPONSE, "xp_required_for_level": 760}
    stale.pop("xp_required_for_next_level")

    with pytest.raises(ValidationError):
        AnswerResponse.model_validate(stale)


def test_profile_response_field_set_is_exact():
    assert tuple(ProfileResponse.model_fields) == PROFILE_RESPONSE_FIELDS


def test_unlock_element_type_is_shared():
    """R-9: newly_unlocked and unlocks use ONE element type of shape
    {mode, bit_width, min_level}."""
    assert AnswerResponse.model_fields["newly_unlocked"].annotation == list[UnlockEntry]
    assert ProfileResponse.model_fields["unlocks"].annotation == list[UnlockEntry]
    assert tuple(UnlockEntry.model_fields) == ("mode", "bit_width", "min_level")


def test_unlock_entry_round_trips_the_seed_row():
    entry = UnlockEntry.model_validate(GOLDEN_UNLOCK_ROW)

    assert entry.mode == "hex_to_bin"
    assert entry.bit_width == 16
    assert entry.min_level == 8
    assert entry.model_dump() == GOLDEN_UNLOCK_ROW


def test_unlock_entry_rejects_unknown_keys_and_invalid_mode():
    with pytest.raises(ValidationError):
        UnlockEntry.model_validate({**GOLDEN_UNLOCK_ROW, "sticky": True})
    with pytest.raises(ValidationError):
        UnlockEntry(mode="ascii", bit_width=8, min_level=1)


def test_profile_response_carries_unlocks():
    profile = ProfileResponse(
        total_xp=476,
        level=4,
        xp_in_level=224,
        xp_required_for_next_level=336,
        current_streak=0,
        best_streak=9,
        correct_count=30,
        incorrect_count=2,
        unlocks=[UnlockEntry.model_validate(GOLDEN_UNLOCK_ROW)],
    )

    dumped = profile.model_dump()
    assert dumped["unlocks"] == [GOLDEN_UNLOCK_ROW]
    assert dumped["level"] == 4


def test_answer_and_profile_reject_impossible_economy_numbers():
    """level >= 1, counters >= 0 — a negative streak never reaches the wire."""
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate({**GOLDEN_ANSWER_RESPONSE, "level": 0})
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate({**GOLDEN_ANSWER_RESPONSE, "current_streak": -1})
    with pytest.raises(ValidationError):
        ProfileResponse.model_validate(
            {
                "total_xp": -4,
                "level": 1,
                "xp_in_level": 0,
                "xp_required_for_next_level": 100,
                "current_streak": 0,
                "best_streak": 0,
                "correct_count": 0,
                "incorrect_count": 0,
                "unlocks": [],
            }
        )


def test_xp_delta_may_be_zero_or_negative():
    """D-5/LV-09b: a floor-clamped penalty yields ``xp_delta: 0``; a normal
    penalty is negative."""
    for delta in (0, -8, -512, 40):
        assert (
            AnswerResponse.model_validate(
                {**GOLDEN_ANSWER_RESPONSE, "xp_delta": delta}
            ).xp_delta
            == delta
        )


def test_leaderboard_entry_v2_has_no_points_and_no_pii():
    """US-36 AC-4: no ``points``, no email — username plus game numbers only."""
    assert tuple(LeaderboardEntryV2.model_fields) == LEADERBOARD_ENTRY_FIELDS

    entry = LeaderboardEntryV2(
        user_id="u-1",
        username="alice",
        total_xp=1320,
        level=6,
        correct_count=42,
        incorrect_count=5,
    )
    assert entry.model_dump() == {
        "user_id": "u-1",
        "username": "alice",
        "total_xp": 1320,
        "level": 6,
        "correct_count": 42,
        "incorrect_count": 5,
    }
    assert "points" not in entry.model_dump()
    assert "email" not in entry.model_dump()


def test_leaderboard_entry_v2_refuses_a_points_field():
    """The Rev-3 decision: no deprecated ``points`` alias (US-36, YAGNI)."""
    with pytest.raises(ValidationError):
        LeaderboardEntryV2.model_validate(
            {
                "user_id": "u-1",
                "username": "alice",
                "total_xp": 1,
                "level": 1,
                "correct_count": 0,
                "incorrect_count": 0,
                "points": 12,
            }
        )


def test_leaderboard_response_wrapper():
    """The ``{entries: [...]}`` wrapper is kept (US-36)."""
    response = LeaderboardResponseV2(
        entries=[
            LeaderboardEntryV2(
                user_id="u-1",
                username="alice",
                total_xp=1320,
                level=6,
                correct_count=42,
                incorrect_count=5,
            )
        ]
    )

    assert tuple(LeaderboardResponseV2.model_fields) == ("entries",)
    assert response.model_dump()["entries"][0]["total_xp"] == 1320


def test_schema_public_names():
    """US-33/US-35/US-36 import by name — the module's ``__all__`` is complete."""
    assert set(SCHEMA_ALL) >= {
        "AnswerRequest",
        "AnswerResponse",
        "ProfileResponse",
        "LeaderboardEntryV2",
        "LeaderboardResponseV2",
        "UnlockEntry",
    }
