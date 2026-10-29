"""US-41: the end-to-end economy integration suite (the Sprint-4 gate).

Everything Sprint 4 promised, walked by a real user over the real stack: real
``POST /api/auth/register`` -> real verification -> real ``POST /api/auth/login``
-> a real Bearer token -> real PostgreSQL 15 -> the real gate order of
``POST /api/game/answer`` -> the real read side (``/profile``, ``/me``,
``/leaderboard``). Nothing here seeds an economy row through the service layer,
nothing mocks a service, and no test skips a layer: the point of the suite is
that the wiring holds, not that each module does.

Section A is the journey (``docs/sprint4-plan.md`` US-41, steps 1-7) as ONE
continuous user, in the order the story lists the steps:

1. the first answer creates ``user_game_profile`` lazily (US-28 AC-3) — and the
   registration before it creates nothing;
2. the streak climbs 1 -> 11 with the exact §4.5 deltas over the core-16 combo
   (``hex_to_bin``/4): 16, 16, 20, 20, 24, 24, 24, 32, 32, 32, 40;
3. the three D-2 boundaries fire on the answer that crosses them — 476 XP -> L4
   (the 8-bit shelf), 3,220 -> L8 (16-bit), 25,752 -> L13 (32-bit); the corrected
   figures of R-4, landed on exactly so no other answer can claim the delta;
4. a level-up with **no** unlock returns ``newly_unlocked: []`` — seven times in
   this journey alone ((4,5), (5,6), (6,7), (8,9), (9,10), (10,11), (11,12));
5. an incorrect answer at streak 11 applies the pure §4.7 penalty (half of core,
   **no** streak term — the same -8 the journey pays later at streak 3), resets
   ``current_streak`` and keeps ``best_streak`` at 11;
6. the OQ-4 floor: at exactly ``cum(13)`` = 25,752 a penalty is absorbed whole,
   ``xp_delta: 0``, level unchanged — and ``total_xp >= cum(level)`` is asserted
   after **every** answer of the suite, not only here (:func:`_check_invariants`);
7. ``GET /api/game/leaderboard`` reflects the journey: the player is on the board
   in the documented total order, with no PII on the entry.

Section B is the six explicit tests the story names, Section C the two edge
sequences the plan spells out with its own numbers (the ``cum(2)`` floor example
and the L6 silent toast).

Independence: every expected number below is a **plan literal**, hand-transcribed
from §4.4/§4.5/§4.7/§4.8/D-1/D-2 and produced by an oracle that never imports
``src/`` (the sequence generator lived outside the repo; the tables it walked are
the ones quoted here). A wrong constant therefore cannot hide behind a wrong
expectation — which is the whole reason a 52-answer journey is written out as a
literal matrix instead of being computed by the engine under test.

Field names are the frozen wire contract (OQ-8): the streak the story calls
"streak_count" is ``current_streak`` on the wire.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from src.config import get_settings
from src.database.engine import Base, get_db
from src.main import app
from src.models.game import UserGameProfile
from src.routers.auth import get_email_service
from src.routers.game import answer_rate_limit_session
from src.services.answer_service import rate_limit_key
from src.services.email_service import MockEmailService
from src.services.game_config import (
    ANSWER_RATE_LIMIT,
    ANSWER_RATE_WINDOW_SECONDS,
)
from uuid_utils import uuid7

# --------------------------------------------------------------------------- #
# Plan literals (docs/sprint4-plan.md). Hand-transcribed, never imported from
# src/, so a wrong constant cannot hide behind a wrong expectation.
# --------------------------------------------------------------------------- #

# D-1 / §4.8: req(n) = the span n -> n+1, and cum(L) = the XP needed to REACH L.
# Two independent rows of the same table: _check_invariants asserts they agree at
# every level the journey visits, so a transcription slip in either one surfaces.
_REQ_TO_NEXT: dict[int, int] = {
    1: 100, 2: 152, 3: 224, 4: 336, 5: 508, 6: 760, 7: 1140, 8: 1708,
    9: 2564, 10: 3844, 11: 5768, 12: 8648, 13: 12976, 14: 19460, 15: 29192,
}
_CUM: dict[int, int] = {
    1: 0, 2: 100, 3: 252, 4: 476, 5: 812, 6: 1320, 7: 2080, 8: 3220,
    9: 4928, 10: 7492, 11: 11336, 12: 17104, 13: 25752, 14: 38728,
    15: 58188, 16: 87380,
}

# D-2 / §4.9: the full 16-row unlock seed, in the table's own order (grouped by
# mode). ``_ladder_for_level`` filters it; the shelf literals below quote the
# five non-empty ``newly_unlocked`` deltas the journey produces.
_UNLOCK_SEED: tuple[tuple[str, int, int], ...] = (
    ("hex_to_bin", 4, 1), ("hex_to_bin", 8, 4), ("hex_to_bin", 16, 8),
    ("hex_to_bin", 32, 13),
    ("hex_to_dec", 4, 1), ("hex_to_dec", 8, 4), ("hex_to_dec", 16, 8),
    ("hex_to_dec", 32, 13),
    ("bin_to_dec", 4, 1), ("bin_to_dec", 8, 4), ("bin_to_dec", 16, 8),
    ("bin_to_dec", 32, 13),
    ("unicode", 8, 1), ("unicode", 16, 2), ("unicode", 24, 3), ("unicode", 32, 4),
)

_L2_UNICODE16: tuple[dict[str, Any], ...] = (
    {"mode": "unicode", "bit_width": 16, "min_level": 2},
)
_L3_UNICODE24: tuple[dict[str, Any], ...] = (
    {"mode": "unicode", "bit_width": 24, "min_level": 3},
)
_L4_SHELF: tuple[dict[str, Any], ...] = (  # the 8-bit shelf, §4.9 UN-01 cap 4
    {"mode": "hex_to_bin", "bit_width": 8, "min_level": 4},
    {"mode": "hex_to_dec", "bit_width": 8, "min_level": 4},
    {"mode": "bin_to_dec", "bit_width": 8, "min_level": 4},
    {"mode": "unicode", "bit_width": 32, "min_level": 4},
)
_L8_SHELF: tuple[dict[str, Any], ...] = (  # the 16-bit shelf (R-4's correction)
    {"mode": "hex_to_bin", "bit_width": 16, "min_level": 8},
    {"mode": "hex_to_dec", "bit_width": 16, "min_level": 8},
    {"mode": "bin_to_dec", "bit_width": 16, "min_level": 8},
)
_L13_SHELF: tuple[dict[str, Any], ...] = (  # the 32-bit shelf, the last one
    {"mode": "hex_to_bin", "bit_width": 32, "min_level": 13},
    {"mode": "hex_to_dec", "bit_width": 32, "min_level": 13},
    {"mode": "bin_to_dec", "bit_width": 32, "min_level": 13},
)

_Step = tuple[tuple[str, int, bool], dict[str, Any]]
"""One journey answer: ``((mode, bit_width, is_correct), expected_200_body)``."""


def _body(
    xp_delta: int,
    total_xp: int,
    level: int,
    xp_in_level: int,
    xp_required_for_next_level: int,
    current_streak: int,
    best_streak: int,
    correct_count: int,
    incorrect_count: int,
    newly_unlocked: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """The expected 200 body of ``POST /api/game/answer`` — all ten fields (US-33).

    ``newly_unlocked`` is copied out of the shared shelf literals so no assertion
    can ever mutate a table another assertion reads.
    """
    return {
        "xp_delta": xp_delta,
        "total_xp": total_xp,
        "level": level,
        "xp_in_level": xp_in_level,
        "xp_required_for_next_level": xp_required_for_next_level,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "newly_unlocked": [dict(entry) for entry in newly_unlocked],
    }


def _ladder_for_level(level: int) -> list[dict[str, Any]]:
    """§4.9 UN-02: every combo playable at ``level``, in seed order."""
    return [
        {"mode": mode, "bit_width": width, "min_level": min_level}
        for mode, width, min_level in _UNLOCK_SEED
        if min_level <= level
    ]


def _level_of(total_xp: int) -> int:
    """D-1's own derivation: the highest L with ``cum(L) <= total_xp``."""
    level = 1
    while _CUM[level + 1] <= total_xp:
        level += 1
    return level


# US-35: the eight economy numbers of ``GET /api/game/profile``.
_ECONOMY_FIELDS: tuple[str, ...] = (
    "total_xp",
    "level",
    "xp_in_level",
    "xp_required_for_next_level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
)

# US-37 froze ``/api/user/me`` at nine fields, so the two progress numbers of the
# D-1 span (``xp_in_level``, ``xp_required_for_next_level``) live on /profile only.
# The three-way agreement of US-37 AC-1 is therefore about these six.
_ME_ECONOMY_FIELDS: tuple[str, ...] = (
    "total_xp",
    "level",
    "current_streak",
    "best_streak",
    "correct_count",
    "incorrect_count",
)

_ME_FIELDS: frozenset[str] = frozenset({
    "id", "username", "email", "total_xp", "level", "current_streak",
    "best_streak", "correct_count", "incorrect_count",
})

_BOARD_FIELDS: frozenset[str] = frozenset(
    {"user_id", "username", "total_xp", "level", "correct_count", "incorrect_count"}
)


def _check_invariants(body: dict[str, Any], answers: int) -> None:
    """The economy's standing rules, applied to EVERY answer of this suite.

    US-41 asks for ``total_xp >= cum(level)`` "anywhere in the journey", not at one
    hand-picked step (OQ-4/LV-08), so the rule lives here and runs on all 52 journey
    answers, all 120 budget answers and every edge sequence. It also pins D-8 (no
    float ever reaches the wire) and LV-10 (``xp_in_level`` is never negative), and
    it cross-reads the two hand-transcribed tables of §4.8 against each other.
    """
    for key, value in body.items():
        if key == "newly_unlocked":
            for entry in value:
                assert set(entry) == {"mode", "bit_width", "min_level"}, entry
                assert type(entry["bit_width"]) is int, entry
                assert type(entry["min_level"]) is int, entry
            continue
        assert type(value) is int, (key, value)  # D-8: ints, never floats, never bool

    total, level = body["total_xp"], body["level"]
    assert level == _level_of(total) >= 1                      # D-1 derivation
    assert _CUM[level] <= total < _CUM[level + 1]              # OQ-4 floor, no leap
    assert body["xp_in_level"] == total - _CUM[level] >= 0     # LV-10
    assert _CUM[level + 1] - _CUM[level] == _REQ_TO_NEXT[level]
    assert body["xp_required_for_next_level"] == _REQ_TO_NEXT[level]
    assert body["xp_delta"] % 4 == 0                           # D-8: deltas live in 4z
    assert body["best_streak"] >= body["current_streak"]       # the high-water mark
    assert body["correct_count"] + body["incorrect_count"] == answers


# --------------------------------------------------------------------------- #
# The expectation matrix
#
# Generated by walking the plan tables (an oracle outside the repo) and written out
# as literals: the answer tuple, then the exact 200 body it must produce. Counters
# are cumulative across each tuple, so a block is only meaningful replayed whole.
# --------------------------------------------------------------------------- #
# steps 1-6 of US-41, one continuous user; counters are cumulative
_JOURNEY: tuple[_Step, ...] = (
    (("hex_to_bin", 4, True),
     _body(16, 16, 1, 16, 100, 1, 1, 1, 0)),
    (("hex_to_bin", 4, True),
     _body(16, 32, 1, 32, 100, 2, 2, 2, 0)),
    (("hex_to_bin", 4, True),
     _body(20, 52, 1, 52, 100, 3, 3, 3, 0)),
    (("hex_to_bin", 4, True),
     _body(20, 72, 1, 72, 100, 4, 4, 4, 0)),
    (("hex_to_bin", 4, True),
     _body(24, 96, 1, 96, 100, 5, 5, 5, 0)),
    (("hex_to_bin", 4, True),
     _body(24, 120, 2, 20, 152, 6, 6, 6, 0, _L2_UNICODE16)),
    (("hex_to_bin", 4, True),
     _body(24, 144, 2, 44, 152, 7, 7, 7, 0)),
    (("hex_to_bin", 4, True),
     _body(32, 176, 2, 76, 152, 8, 8, 8, 0)),
    (("hex_to_bin", 4, True),
     _body(32, 208, 2, 108, 152, 9, 9, 9, 0)),
    (("hex_to_bin", 4, True),
     _body(32, 240, 2, 140, 152, 10, 10, 10, 0)),
    (("hex_to_bin", 4, True),
     _body(40, 280, 3, 28, 224, 11, 11, 11, 0, _L3_UNICODE24)),
    (("hex_to_bin", 4, False),
     _body(-8, 272, 3, 20, 224, 0, 11, 11, 1)),
    (("hex_to_dec", 4, True),
     _body(48, 320, 3, 68, 224, 1, 11, 12, 1)),
    (("bin_to_dec", 4, True),
     _body(128, 448, 3, 196, 224, 2, 11, 13, 1)),
    (("hex_to_bin", 4, True),
     _body(20, 468, 3, 216, 224, 3, 11, 14, 1)),
    (("hex_to_bin", 4, False),
     _body(-8, 460, 3, 208, 224, 0, 11, 14, 2)),
    (("hex_to_bin", 4, True),
     _body(16, 476, 4, 0, 336, 1, 11, 15, 2, _L4_SHELF)),
    (("bin_to_dec", 8, True),
     _body(256, 732, 4, 256, 336, 2, 11, 16, 2)),
    (("bin_to_dec", 8, True),
     _body(320, 1052, 5, 240, 508, 3, 11, 17, 2)),
    (("bin_to_dec", 8, True),
     _body(320, 1372, 6, 52, 760, 4, 11, 18, 2)),
    (("hex_to_bin", 8, True),
     _body(48, 1420, 6, 100, 760, 5, 11, 19, 2)),
    (("bin_to_dec", 8, True),
     _body(384, 1804, 6, 484, 760, 6, 11, 20, 2)),
    (("bin_to_dec", 8, True),
     _body(384, 2188, 7, 108, 1140, 7, 11, 21, 2)),
    (("bin_to_dec", 8, True),
     _body(512, 2700, 7, 620, 1140, 8, 11, 22, 2)),
    (("bin_to_dec", 8, True),
     _body(512, 3212, 7, 1132, 1140, 9, 11, 23, 2)),
    (("hex_to_bin", 4, False),
     _body(-8, 3204, 7, 1124, 1140, 0, 11, 23, 3)),
    (("hex_to_bin", 4, True),
     _body(16, 3220, 8, 0, 1708, 1, 11, 24, 3, _L8_SHELF)),
    (("hex_to_dec", 16, True),
     _body(192, 3412, 8, 192, 1708, 2, 11, 25, 3)),
    (("hex_to_dec", 4, True),
     _body(60, 3472, 8, 252, 1708, 3, 11, 26, 3)),
    (("bin_to_dec", 16, True),
     _body(640, 4112, 8, 892, 1708, 4, 11, 27, 3)),
    (("bin_to_dec", 8, True),
     _body(384, 4496, 8, 1276, 1708, 5, 11, 28, 3)),
    (("bin_to_dec", 16, True),
     _body(768, 5264, 9, 336, 2564, 6, 11, 29, 3)),
    (("bin_to_dec", 16, True),
     _body(768, 6032, 9, 1104, 2564, 7, 11, 30, 3)),
    (("bin_to_dec", 16, True),
     _body(1024, 7056, 9, 2128, 2564, 8, 11, 31, 3)),
    (("bin_to_dec", 16, True),
     _body(1024, 8080, 10, 588, 3844, 9, 11, 32, 3)),
    (("bin_to_dec", 16, True),
     _body(1024, 9104, 10, 1612, 3844, 10, 11, 33, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 10384, 10, 2892, 3844, 11, 11, 34, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 11664, 11, 328, 5768, 12, 12, 35, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 12944, 11, 1608, 5768, 13, 13, 36, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 14224, 11, 2888, 5768, 14, 14, 37, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 15504, 11, 4168, 5768, 15, 15, 38, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 16784, 11, 5448, 5768, 16, 16, 39, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 18064, 12, 960, 8648, 17, 17, 40, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 19344, 12, 2240, 8648, 18, 18, 41, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 20624, 12, 3520, 8648, 19, 19, 42, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 21904, 12, 4800, 8648, 20, 20, 43, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 23184, 12, 6080, 8648, 21, 21, 44, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 24464, 12, 7360, 8648, 22, 22, 45, 3)),
    (("bin_to_dec", 16, True),
     _body(1280, 25744, 12, 8640, 8648, 23, 23, 46, 3)),
    (("hex_to_bin", 4, False),
     _body(-8, 25736, 12, 8632, 8648, 0, 23, 46, 4)),
    (("hex_to_bin", 4, True),
     _body(16, 25752, 13, 0, 12976, 1, 23, 47, 4, _L13_SHELF)),
    (("hex_to_bin", 4, False),
     _body(0, 25752, 13, 0, 12976, 0, 23, 47, 5)),
)

# OQ-4 at cum(2) = 100: the whole penalty 8 is absorbed
_FLOOR_SEQUENCE: tuple[_Step, ...] = (
    (("hex_to_bin", 4, True),
     _body(16, 16, 1, 16, 100, 1, 1, 1, 0)),
    (("hex_to_bin", 4, True),
     _body(16, 32, 1, 32, 100, 2, 2, 2, 0)),
    (("hex_to_dec", 4, True),
     _body(60, 92, 1, 92, 100, 3, 3, 3, 0)),
    (("hex_to_bin", 4, False),
     _body(-8, 84, 1, 84, 100, 0, 3, 3, 1)),
    (("hex_to_bin", 4, True),
     _body(16, 100, 2, 0, 152, 1, 3, 4, 1, _L2_UNICODE16)),
    (("hex_to_bin", 4, False),
     _body(0, 100, 2, 0, 152, 0, 3, 4, 2)),
)

# L5 -> L6 at cum(6) = 1320: a level-up, no unlock (R-9)
_LEVELUP_NO_UNLOCK: tuple[_Step, ...] = (
    (("hex_to_bin", 4, True),
     _body(16, 16, 1, 16, 100, 1, 1, 1, 0)),
    (("hex_to_bin", 4, True),
     _body(16, 32, 1, 32, 100, 2, 2, 2, 0)),
    (("bin_to_dec", 4, True),
     _body(160, 192, 2, 92, 152, 3, 3, 3, 0, _L2_UNICODE16)),
    (("bin_to_dec", 4, True),
     _body(160, 352, 3, 100, 224, 4, 4, 4, 0, _L3_UNICODE24)),
    (("bin_to_dec", 4, True),
     _body(192, 544, 4, 68, 336, 5, 5, 5, 0, _L4_SHELF)),
    (("bin_to_dec", 8, True),
     _body(384, 928, 5, 116, 508, 6, 6, 6, 0)),
    (("bin_to_dec", 8, True),
     _body(384, 1312, 5, 500, 508, 7, 7, 7, 0)),
    (("hex_to_bin", 4, False),
     _body(-8, 1304, 5, 492, 508, 0, 7, 7, 1)),
    (("hex_to_bin", 4, True),
     _body(16, 1320, 6, 0, 760, 1, 7, 8, 1)),
)

# body of the 120th (allowed) answer of a 120x HB-4 correct run
_RATE_LIMIT_120TH = _body(40, 4640, 8, 1420, 1708, 120, 120, 120, 0)
# --------------------------------------------------------------------------- #
# Wiring: the real app, the real test database, one synthetic client IP per test
# --------------------------------------------------------------------------- #

_ANSWER_URL = "/api/game/answer"
_PROFILE_URL = "/api/game/profile"
_LEADERBOARD_URL = "/api/game/leaderboard"
_ME_URL = "/api/user/me"
_REGISTER_URL = "/api/auth/register"
_VERIFY_URL = "/api/auth/verify-email"
_LOGIN_URL = "/api/auth/login"

_PASSWORD = "StrongPass1!"  # clears validate_password_strength (US-06)

# The one answer this suite repeats: hex_to_bin at 4 bits, the cheapest combo and
# the only one every level can play (D-2 slot 1).
_HB4: dict[str, Any] = {"mode": "hex_to_bin", "bit_width": 4, "is_correct": True}

# The persisted row over raw SQL, ``updated_at`` included: a rejected or refused
# answer has to leave the row byte-identical, and the identity map must not be
# allowed to answer that question for us.
_ROW_SQL = text(
    "SELECT total_xp, level, current_streak, best_streak, correct_count, "
    "incorrect_count, updated_at FROM user_game_profile WHERE user_id = :u"
)
_ATTEMPTS_SQL = text("SELECT count(*) FROM rate_limit_attempt WHERE key = :k")


@pytest.fixture
async def async_engine(_test_db_ready):
    """One ephemeral PostgreSQL schema per test — how this module stays hermetic.

    Shadowing conftest's ``async_engine`` is deliberate. conftest builds its engine
    over ``public``, and ``public`` is shared by every pytest session pointed at the
    test container on 5434 — which Sprint 4's parallel agents do. Two sessions on one
    schema therefore interleave in both directions: a foreign row lands inside this
    module's board assertions (US-36's board ranks the WHOLE table, so a default-limit
    top-50 cannot tell my one journey row from someone else's fifty), and the cleanup
    this module would need in exchange — the ``TRUNCATE users CASCADE`` an earlier
    revision ran — deletes the OTHER session's users mid-test. That trade was measured
    as ``assert 58 == 50`` on the default-limit board and a missing top-10 row in the
    journey, on a file that passes green in isolation.

    So nothing here is cleaned; nothing here is shared instead. Every table this module
    touches lives in a schema that exists for exactly one test and is dropped with it,
    and ``search_path`` is pinned on the connection through asyncpg's server settings —
    so no statement of this module, and no statement of the app behind the overridden
    ``get_db`` / ``answer_rate_limit_session``, can resolve against ``public`` at all.
    A leaked row becomes impossible by construction rather than by discipline, in both
    directions.
    """
    settings = get_settings()
    schema = f"us41_{str(uuid7()).replace('-', '')}"

    bootstrap = create_async_engine(settings.async_database_url, echo=False)
    try:
        async with bootstrap.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        await bootstrap.dispose()

    engine = create_async_engine(
        settings.async_database_url,
        echo=False,
        connect_args={"server_settings": {"search_path": f'"{schema}"'}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()
        teardown = create_async_engine(settings.async_database_url, echo=False)
        try:
            async with teardown.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await teardown.dispose()


_used_ips: set[str] = set()


def _client_ip() -> str:
    """A client IP no other test of this session shares.

    The auth rate limits (``register:{ip}:{email}``, ``login:{ip}:{email}``) and the
    US-05 brute-force lockout key on the client IP, so against a shared table the
    failed-login tests elsewhere in the suite could lock THIS suite's logins out at
    random: green in isolation, red in ``pytest``. :func:`async_engine` now gives each
    test its own schema, which settles that for the tables of this module; the distinct
    addresses stay because they are also what makes the walk a real one — every
    ``audit_log.ip_address`` row here carries a plausible origin instead of the loopback
    the other suites share. The addresses are real IPv4 shapes because that column is
    an ``inet`` written verbatim.
    """
    while True:
        tail = str(uuid7()).replace("-", "")[-4:]
        candidate = f"10.41.{int(tail[:2], 16)}.{int(tail[2:], 16)}"
        if candidate not in _used_ips:
            _used_ips.add(candidate)
            return candidate


@pytest.fixture
async def db(async_engine) -> AsyncSession:
    """This test's own read session, over the engine of :func:`async_engine`.

    Deliberately the SAME engine the app runs on: a read side that agreed with the
    endpoint only because it queried a different schema would prove nothing.
    """
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest.fixture
def email_service() -> MockEmailService:
    """A capture-only mailer for ONE test, so the verification token is readable.

    Registration is real HTTP here; only the transport of the notification is
    replaced, which is what makes the e-mail the only way a test can obtain the
    token — the same way a player gets it.
    """
    service = MockEmailService()
    app.dependency_overrides[get_email_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_email_service, None)


@pytest.fixture
async def client(async_engine, email_service):
    """The real app with BOTH of /answer's sessions on the test database.

    ``answer_rate_limit_session`` is overridden alongside ``get_db`` because the
    pair IS the claim Rev 3 R-2 makes: two sessions, two transactions, so the
    limiter's commit can never release the answer's ``FOR UPDATE`` lock. Stubbing
    the limiter away would test the economy against a control that does not exist
    in production. ``raise_app_exceptions=False`` keeps an application error inside
    the response, where the assertion can name the request that caused it.
    """
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[answer_rate_limit_session] = _session
    transport = ASGITransport(
        app=app, client=(_client_ip(), 123), raise_app_exceptions=False
    )
    async with AsyncClient(transport=transport, base_url="http://trainer.test") as c:
        yield c
    app.dependency_overrides.clear()


async def _register_verify_login(
    client: AsyncClient, email_service: MockEmailService
) -> tuple[str, dict[str, str], str]:
    """The story's setup, for real: register -> verify -> login -> Bearer.

    Returns the user id, the Authorization header and the username. The id is read
    back from ``GET /api/user/me`` rather than minted here: a hand-built JWT would
    bypass the very auth path this suite is supposed to walk. The username comes
    along because it is the leaderboard's only identity claim (US-36 R-3).
    """
    identity = str(uuid7()).replace("-", "")
    username = f"us41-{identity[-12:]}"
    email = f"{identity[-12:]}@example.com"

    register = await client.post(
        _REGISTER_URL,
        json={"email": email, "username": username, "password": _PASSWORD},
    )
    assert register.status_code == 201, register.text
    assert register.json() == {
        "message": "If this email is available, a verification mail has been sent.",
        "user_id": None,
    }
    assert len(email_service.sent) == 1, email_service.sent

    token = email_service.sent[-1]["token"]
    verify = await client.post(_VERIFY_URL, json={"token": token})
    assert verify.status_code == 200, verify.text

    login = await client.post(
        _LOGIN_URL, json={"email": email, "password": _PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["token_type"] == "bearer"

    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = (await client.get(_ME_URL, headers=headers)).json()
    assert (me["username"], me["email"]) == (username, email)
    assert (me["total_xp"], me["level"], me["correct_count"]) == (0, 1, 0)
    return me["id"], headers, username


async def _answer(
    client: AsyncClient, headers: dict[str, str], step: _Step, answers: int
) -> dict[str, Any]:
    """POST one journey answer and demand the exact 200 body the plan promises."""
    (mode, bit_width, is_correct), expected = step
    response = await client.post(
        _ANSWER_URL,
        json={"mode": mode, "bit_width": bit_width, "is_correct": is_correct},
        headers=headers,
    )
    assert response.status_code == 200, (
        f"answer #{answers} {(mode, bit_width, is_correct)} -> "
        f"HTTP {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body == expected, (
        f"answer #{answers} {(mode, bit_width, is_correct)}:\n"
        f"  expected {expected}\n  got      {body}"
    )
    _check_invariants(body, answers)
    return body


async def _reads_agree(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> None:
    """``/profile`` and ``/me`` must say what ``/answer`` just said (US-37 AC-1).

    Three requests, three transactions, so nothing here can pass on a shared object;
    and the profile's ``unlocks`` has to be the full D-2 ladder for the level the
    answer derived, not the delta set (US-35 §4.9 UN-02). ``/me`` carries six of the
    eight numbers (US-37 froze it at nine fields), so the comparison is split:
    profile against the answer on all eight, ``/me`` against both on the six.
    """
    profile_res = await client.get(_PROFILE_URL, headers=headers)
    me_res = await client.get(_ME_URL, headers=headers)
    assert profile_res.status_code == 200, profile_res.text
    assert me_res.status_code == 200, me_res.text
    profile, me = profile_res.json(), me_res.json()

    assert set(profile) == {*_ECONOMY_FIELDS, "unlocks"}
    assert set(me) == _ME_FIELDS
    said = {field: body[field] for field in _ECONOMY_FIELDS}
    assert {field: profile[field] for field in _ECONOMY_FIELDS} == said
    shared = {field: body[field] for field in _ME_ECONOMY_FIELDS}
    assert {field: me[field] for field in _ME_ECONOMY_FIELDS} == shared
    assert profile["unlocks"] == _ladder_for_level(body["level"])


async def _row(db: AsyncSession, user_id: str):
    """The persisted economy row, from the database rather than the session."""
    await db.rollback()  # no ambient transaction, so no stale snapshot
    return (await db.execute(_ROW_SQL, {"u": user_id})).first()


async def _attempts(db: AsyncSession, user_id: str) -> int:
    """How many ``answer:{user_id}`` rows the limiter has recorded."""
    await db.rollback()
    result = await db.execute(_ATTEMPTS_SQL, {"k": rate_limit_key(user_id)})
    return result.scalar_one()


def _assert_board_order(entries: list[dict[str, Any]]) -> None:
    """US-36's total order: ``total_xp DESC``, then ``incorrect_count ASC``, then
    ``user_id ASC`` — stable across a 30-second client poll by construction."""
    keys = [
        (-entry["total_xp"], entry["incorrect_count"], entry["user_id"])
        for entry in entries
    ]
    assert keys == sorted(keys), [
        (entry["total_xp"], entry["incorrect_count"]) for entry in entries
    ]


# --------------------------------------------------------------------------- #
# Section A. The journey: US-41 steps 1-7, one user, 52 answers
# --------------------------------------------------------------------------- #


async def test_journey_from_registration_to_the_leaderboard(client, db, email_service):
    """Steps 1-7 in story order, on one continuous user (the suite's headline).

    The matrix below is replayed answer by answer with the exact 200 body asserted
    for each and the standing invariants of :func:`_check_invariants` re-checked, so
    "``total_xp >= cum(level)`` always" is a property of all 52 answers rather than a
    claim about one of them. The landmarks after the loop read the REPLAYED bodies,
    which is what makes them evidence rather than restatement.
    """
    user_id, headers, username = await _register_verify_login(client, email_service)

    # Step 1, precondition: registration and login create no economy row at all
    # (US-28 AC-3 reserves creation for the first answer), and the reads that report
    # on the missing row answer the zero state without writing it (US-35).
    assert await _row(db, user_id) is None
    zero = await client.get(_PROFILE_URL, headers=headers)
    assert zero.status_code == 200, zero.text
    assert zero.json() == {
        "total_xp": 0, "level": 1, "xp_in_level": 0,
        "xp_required_for_next_level": _REQ_TO_NEXT[1], "current_streak": 0,
        "best_streak": 0, "correct_count": 0, "incorrect_count": 0,
        "unlocks": _ladder_for_level(1),
    }
    assert await _row(db, user_id) is None

    # Steps 2-6: the journey itself.
    bodies: list[dict[str, Any]] = []
    for index, step in enumerate(_JOURNEY, start=1):
        bodies.append(await _answer(client, headers, step, index))
        await _reads_agree(client, headers, bodies[-1])

    row = await _row(db, user_id)
    assert row is not None  # step 1's effect: the lazy row exists now
    assert (row.total_xp, row.level) == (25752, 13)  # the stored level agrees (H1)

    # Step 2: the streak ladder 1 -> 11 over the core-16 combo. §4.5's tiers on
    # 16 are 16, 16, 20, 20, 24, 24, 24, 32, 32, 32, 40 (XP-G01..G11) — capped at
    # x2.5 from streak 11 on, and 280 XP total, which is nowhere near the D-2
    # boundaries. That is why the boundaries need their own legs (step 3).
    assert [b["xp_delta"] for b in bodies[:11]] == [
        16, 16, 20, 20, 24, 24, 24, 32, 32, 32, 40,
    ]
    assert [b["current_streak"] for b in bodies[:11]] == list(range(1, 12))
    assert (bodies[10]["best_streak"], bodies[10]["total_xp"]) == (11, 280)

    # Step 3: the three D-2 boundaries fire on the answer that crosses them, and
    # only there. 120/280 are the unicode slots at L2/L3; 476/3220/25752 are the
    # corrected conversion figures of R-4, landed on EXACTLY (xp_in_level 0).
    crossings = [b for b in bodies if b["newly_unlocked"]]
    assert [b["total_xp"] for b in crossings] == [120, 280, 476, 3220, 25752]
    assert [b["level"] for b in crossings] == [2, 3, 4, 8, 13]
    assert [b["xp_in_level"] for b in crossings] == [20, 28, 0, 0, 0]
    assert [b["newly_unlocked"] for b in crossings] == [
        list(_L2_UNICODE16), list(_L3_UNICODE24), list(_L4_SHELF), list(_L8_SHELF),
        list(_L13_SHELF),
    ]

    # Step 4: the common case is a level-up that unlocks NOTHING (R-9). Seven of
    # the journey's twelve level-ups are of this kind.
    quiet = [
        (a["level"], b["level"])
        for a, b in pairwise(bodies)
        if b["level"] > a["level"] and not b["newly_unlocked"]
    ]
    assert quiet == [
        (4, 5), (5, 6), (6, 7), (8, 9), (9, 10), (10, 11), (11, 12),
    ]

    # Step 5: four penalties, all of them exactly -8 = §4.7's half of core 16, paid
    # at streaks 11, 3, 9 and 23 — the streak term is absent by construction (D-5),
    # the streak resets (LV-09b) and the high-water mark stays where it was.
    penalties = [b for b in bodies if b["xp_delta"] < 0]
    assert [b["xp_delta"] for b in penalties] == [-8, -8, -8, -8]
    assert [b["current_streak"] for b in penalties] == [0, 0, 0, 0]
    assert [b["incorrect_count"] for b in penalties] == [1, 2, 3, 4]
    assert penalties[0]["best_streak"] == 11  # the streak-11 penalty keeps best 11

    # Step 6: the OQ-4 floor at the top of the curve. The journey's last answer is
    # incorrect while sitting on exactly cum(13), so the whole penalty is absorbed:
    # xp_delta 0, level 13 unchanged, streak reset. LV-09(b) through the wire.
    floor = bodies[-1]
    assert (floor["xp_delta"], floor["total_xp"], floor["level"]) == (0, 25752, 13)
    assert floor["total_xp"] == _CUM[13]  # exactly on the floor, never below
    assert (floor["current_streak"], floor["best_streak"]) == (0, 23)
    assert (floor["correct_count"], floor["incorrect_count"]) == (47, 5)

    # Step 7: the board reflects the journey — requested WITHOUT a token, because
    # US-36 kept the endpoint public (R-3/OQ-9).
    board = await client.get(_LEADERBOARD_URL, params={"limit": 10})
    assert board.status_code == 200, board.text
    entries = board.json()["entries"]
    _assert_board_order(entries)
    mine = [entry for entry in entries if entry["user_id"] == user_id]
    assert len(mine) == 1, [entry["username"] for entry in entries]
    assert mine[0] == {
        "user_id": user_id, "username": username, "total_xp": 25752, "level": 13,
        "correct_count": 47, "incorrect_count": 5,
    }
    for entry in entries:
        assert set(entry) == _BOARD_FIELDS  # no PII, no legacy `points` (US-34)


# --------------------------------------------------------------------------- #
# Section B. The six explicit tests of the story
# --------------------------------------------------------------------------- #


async def test_rate_limit_refusal_leaves_no_partial_state(client, db, email_service):
    """Explicit test 1 (US-32 AC-3): the 121st answer is refused and costs nothing.

    120 real answers exhaust the budget, and the 121st gets 429 with ``Retry-After``.
    The profile row is then compared BYTE-IDENTICAL — ``updated_at`` included —
    because a refusal is not a partial write: the limiter counts and commits on its
    OWN session (:func:`src.routers.game.answer_rate_limit_session`) and, on the
    blocked path, does not even record the attempt it refused (Rev 3 R-2). The row
    count of ``rate_limit_attempt`` is part of the assertion for that reason.
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)

    body: dict[str, Any] = {}
    for index in range(1, ANSWER_RATE_LIMIT + 1):
        response = await client.post(_ANSWER_URL, json=_HB4, headers=headers)
        assert response.status_code == 200, f"answer #{index}: {response.text}"
        body = response.json()
        _check_invariants(body, index)
        assert (
            body["current_streak"], body["best_streak"], body["correct_count"],
            body["incorrect_count"],
        ) == (index, index, index, 0)
    assert body == _RATE_LIMIT_120TH  # 4,640 XP -> L8, still inside the budget

    before = await _row(db, user_id)
    assert before is not None
    assert await _attempts(db, user_id) == ANSWER_RATE_LIMIT

    refused = await client.post(_ANSWER_URL, json=_HB4, headers=headers)
    assert refused.status_code == 429, refused.text
    assert refused.json() == {"detail": "Too many answers for this user."}
    assert refused.headers["Retry-After"] == str(ANSWER_RATE_WINDOW_SECONDS)

    assert await _row(db, user_id) == before  # updated_at included: nothing written
    assert await _attempts(db, user_id) == ANSWER_RATE_LIMIT  # a refusal is not a row
    profile = (await client.get(_PROFILE_URL, headers=headers)).json()
    assert profile["total_xp"] == before.total_xp == body["total_xp"]


async def test_locked_content_is_refused_naming_its_level(client, db, email_service):
    """Explicit test 2 (§4.9 UN-05): ``hex_to_bin``/8 below L4 -> 403, level in full.

    The detail the frontend renders its "Level X" badge from is
    ``"Content locked until level 4."`` — the required level spelled out, not a
    generic "not allowed". Two more claims ride along, both about the frozen gate
    order (authenticate -> validate -> rate-limit -> 404 -> unlock -> transact): the
    gate runs BEFORE any write, so the user still has no profile row afterwards, and
    AFTER the limiter, so a locked combo does spend budget (a client that could 403
    for free could probe the whole seed).
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)

    response = await client.post(
        _ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True},
        headers=headers,
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Content locked until level 4."}
    assert response.json()["detail"].endswith("4.")  # the min_level of the 8-bit slot

    assert await _row(db, user_id) is None      # nothing written behind the gate
    assert await _attempts(db, user_id) == 1    # but the limiter counted it
    profile = (await client.get(_PROFILE_URL, headers=headers)).json()
    assert (profile["total_xp"], profile["level"]) == (0, 1)
    eight_bit = {"mode": "hex_to_bin", "bit_width": 8, "min_level": 4}
    assert eight_bit not in profile["unlocks"]  # the ladder the client renders from


async def test_concurrent_double_submit_serializes_with_the_limiter_active(
    client, db, email_service
):
    """Explicit test 3 (US-28 AC-1/AC-2): two simultaneous first answers, one row.

    Both POSTs arrive before ``user_game_profile`` exists, so they race on the lazy
    create AND on the economy, with the rate limiter live for both (its own session,
    its own commit — R-2). Serialization is what makes the outcome exact instead of
    merely plausible: the waiter sees the winner's commit, so the streaks come out
    1 and 2 — never 1 and 1 (a lost update, which would still hand out two 200s) and
    never 3 from two submissions. If the transaction boundary is drawn in the wrong
    place this is the test that says so (Rev 3 R-2's whole point).
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)
    assert await _row(db, user_id) is None

    async def _post() -> dict[str, Any]:
        response = await client.post(_ANSWER_URL, json=_HB4, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    async with asyncio.TaskGroup() as group:
        first = group.create_task(_post())
        second = group.create_task(_post())
    bodies = [first.result(), second.result()]

    assert sorted(b["current_streak"] for b in bodies) == [1, 2]
    assert sorted(b["total_xp"] for b in bodies) == [16, 32]
    assert sorted(b["xp_delta"] for b in bodies) == [16, 16]  # §4.5 tiers x1, x1

    row = await _row(db, user_id)
    assert row is not None, "the lazy create lost the race"
    assert (row.total_xp, row.current_streak, row.best_streak) == (32, 2, 2)
    assert (row.correct_count, row.incorrect_count) == (2, 0)
    assert row.level == 1  # 32 XP < cum(2) = 100
    assert await _attempts(db, user_id) == 2  # the limiter saw both, once each


async def test_leaderboard_is_public_with_no_authorization_at_all(
    client, db, email_service
):
    """Explicit test 4 (Rev 3 R-3 / OQ-9): the board answers an anonymous request.

    US-36 deliberately kept this endpoint PUBLIC — the entry shape is a showcase
    (``username`` plus game numbers, no e-mail, no PII) and the abuse control is the
    bounded ``limit``, not auth. This is the regression test for the Rev 2 mistake of
    making it auth-required, which would have broken the documented public contract
    and left the frontend's un-try/caught 30-second poll spinning. The bound is
    asserted at the boundary too: 0 and 101 are a 422, never a silent clamp (BE-6).
    """
    _, headers, _ = await _register_verify_login(client, email_service)
    warmup = await client.post(_ANSWER_URL, json=_HB4, headers=headers)
    assert warmup.status_code == 200, warmup.text

    rows = (
        await db.execute(text("SELECT count(*) FROM user_game_profile"))
    ).scalar_one()
    anonymous = await client.get(_LEADERBOARD_URL)  # NO Authorization header at all
    assert anonymous.status_code == 200, anonymous.text
    payload = anonymous.json()
    assert set(payload) == {"entries"}
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) == min(rows, 50)  # the US-36 default bound
    _assert_board_order(payload["entries"])
    for entry in payload["entries"]:
        assert set(entry) == _BOARD_FIELDS
        assert "email" not in entry and "points" not in entry

    authenticated = await client.get(_LEADERBOARD_URL, headers=headers)
    assert authenticated.status_code == 200, authenticated.text
    assert authenticated.json() == payload  # auth changes nothing here (R-3)

    for limit in (0, 101):
        over = await client.get(_LEADERBOARD_URL, params={"limit": limit})
        assert over.status_code == 422, (limit, over.text)
    # US-36's bound is 1..100 (default 50), enforced at the boundary and behind it
    # in the service, so the widest board is finite and never silently clamped.
    widest = await client.get(_LEADERBOARD_URL, params={"limit": 100})
    assert widest.status_code == 200, widest.text
    assert len(widest.json()["entries"]) == min(rows, 100)


async def test_profile_and_me_agree_on_every_economy_field(client, db, email_service):
    """Explicit test 5 (US-37 AC-1): the report paths never disagree with the wire.

    The journey cross-reads after all 52 of its answers; this test pins the FIELD
    MAPPING itself on a short sequence, and picks the two values a read path is most
    likely to get wrong: the zero state before the first answer (a level of 1 and a
    ``req(1)`` span of 100 out of thin air, with the row still not created), and a
    CLAMPED penalty, where ``xp_delta`` is the post-clamp -32 rather than §4.7's
    nominal -64.
    """
    user_id, headers, username = await _register_verify_login(client, email_service)

    assert await _row(db, user_id) is None
    profile_res = await client.get(_PROFILE_URL, headers=headers)
    me_res = await client.get(_ME_URL, headers=headers)
    assert profile_res.status_code == 200, profile_res.text
    assert me_res.status_code == 200, me_res.text
    zero = {
        "total_xp": 0, "level": 1, "xp_in_level": 0,
        "xp_required_for_next_level": 100, "current_streak": 0, "best_streak": 0,
        "correct_count": 0, "incorrect_count": 0,
    }
    assert profile_res.json() == {**zero, "unlocks": _ladder_for_level(1)}
    me = me_res.json()
    assert set(me) == _ME_FIELDS
    assert (me["id"], me["username"]) == (user_id, username)
    assert me["email"].endswith("@example.com")
    assert {field: me[field] for field in _ME_ECONOMY_FIELDS} == {
        field: zero[field] for field in _ME_ECONOMY_FIELDS
    }
    assert await _row(db, user_id) is None  # the reads did not create the row

    sequence: tuple[_Step, ...] = (
        (("hex_to_bin", 4, True), _body(16, 16, 1, 16, 100, 1, 1, 1, 0)),
        (("unicode", 8, True), _body(16, 32, 1, 32, 100, 2, 2, 2, 0)),
        # bin_to_dec/4 costs 64 (§4.7, half of core 128); 32 XP on the clock with
        # cum(1) = 0 as the floor -> the clamp absorbs nothing yet, delta is -32.
        (("bin_to_dec", 4, False), _body(-32, 0, 1, 0, 100, 0, 2, 2, 1)),
    )
    bodies: list[dict[str, Any]] = []
    for index, step in enumerate(sequence, start=1):
        bodies.append(await _answer(client, headers, step, index))
        await _reads_agree(client, headers, bodies[-1])

    assert (bodies[-1]["total_xp"], bodies[-1]["level"]) == (0, 1)  # LV-09(d)
    row = await _row(db, user_id)
    assert (row.total_xp, row.current_streak, row.best_streak) == (0, 0, 2)
    assert (row.correct_count, row.incorrect_count) == (2, 1)


async def test_us33_golden_fixture_reproduces_byte_for_byte(client, db, email_service):
    """Explicit test 6: US-33's normative example, over the real auth chain.

    Everything up to the answer is real HTTP (register -> verify -> login -> Bearer);
    only the PRE-state is a row, because the fixture needs streak 2 on top of 1,280
    XP with a high-water mark of 7 and 41/5 answers behind it — a history no answer
    sequence can reach in a bounded budget. The 200 body is compared as a whole
    dict: ten fields, no extras, no renames (OQ-8), which is the frozen contract
    US-31's producer and US-38's renderer parse.
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)
    db.add(
        UserGameProfile(
            user_id=user_id, total_xp=1280, level=5, current_streak=2,
            best_streak=7, correct_count=41, incorrect_count=5,
        )
    )
    await db.commit()

    response = await client.post(
        _ANSWER_URL,
        json={"mode": "hex_to_bin", "bit_width": 8, "is_correct": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    golden = response.json()
    assert golden == {
        "xp_delta": 40,                     # 32 core x 5/4 (XP-G04, streak 3)
        "total_xp": 1320,                   # = cum(6)
        "level": 6,                         # LV-05/LV-07: the boundary is inclusive
        "xp_in_level": 0,                   # landed exactly on the threshold
        "xp_required_for_next_level": 760,  # req(6), the 6 -> 7 span
        "current_streak": 3,
        "best_streak": 7,
        "correct_count": 42,
        "incorrect_count": 5,
        "newly_unlocked": [],               # a level-up with no unlock (R-9)
    }
    _check_invariants(golden, 47)  # 42 + 5 = the seeded history plus this answer
    await _reads_agree(client, headers, golden)

    row = await _row(db, user_id)
    assert (row.total_xp, row.level, row.current_streak) == (1320, 6, 3)
    assert row.best_streak == 7  # the high-water mark is never lowered by a level-up


# --------------------------------------------------------------------------- #
# Section C. The two edge sequences the plan spells out with its own numbers
# --------------------------------------------------------------------------- #


async def test_floor_at_cum_two_absorbs_the_whole_penalty(client, db, email_service):
    """Section C, OQ-4/LV-09(b) at the plan's own example: ``cum(2)`` = 100.

    The journey demonstrates the floor at L13; this is the worked example the story
    names, where the arithmetic is small enough to check by eye. Five answers build a
    streak of three and a 92-XP balance, the sixth lifts it onto exactly 100 — and
    the seventh, an incorrect answer worth -8 (§4.7, half of core 16), is absorbed
    whole: ``xp_delta: 0``, level 2 kept, streak reset. Demotion is impossible by
    construction, with no ``max_level_reached`` column anywhere (LV-08).
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)

    bodies: list[dict[str, Any]] = []
    for index, step in enumerate(_FLOOR_SEQUENCE, start=1):
        bodies.append(await _answer(client, headers, step, index))
        await _reads_agree(client, headers, bodies[-1])

    floor = bodies[-1]
    assert (floor["xp_delta"], floor["total_xp"], floor["level"]) == (0, 100, 2)
    assert floor["current_streak"] == 0        # the streak resets even so
    assert floor["best_streak"] == 3
    assert floor["total_xp"] == _CUM[2]        # the floor IS cum(level)
    row = await _row(db, user_id)
    assert (row.total_xp, row.level, row.incorrect_count) == (100, 2, 2)


async def test_level_up_to_six_unlocks_nothing(client, db, email_service):
    """Section C, R-9's silent toast: 1,312 -> 1,320 = ``cum(6)`` with an empty delta.

    The D-2 seed has no row at ``min_level`` 6 (the conversion ladder is 1/4/8/13 and
    unicode tops out at 4), so a level-up here moves the level and unlocks nothing —
    the common case, and the one US-38's toast must swallow without rendering an
    empty popup. The full ladder at L6 is therefore the full ladder at L5, which the
    seed literal says directly.
    """
    user_id, headers, _ = await _register_verify_login(client, email_service)

    bodies: list[dict[str, Any]] = []
    for index, step in enumerate(_LEVELUP_NO_UNLOCK, start=1):
        bodies.append(await _answer(client, headers, step, index))
        await _reads_agree(client, headers, bodies[-1])

    last = bodies[-1]
    assert (bodies[-2]["level"], last["level"]) == (5, 6)
    assert (last["total_xp"], last["xp_in_level"]) == (1320, 0)
    assert last["newly_unlocked"] == []
    assert _ladder_for_level(6) == _ladder_for_level(5)   # nothing new at L6
    assert len(_ladder_for_level(6)) == 10  # 4 x L1 + 3 x 8-bit + 3 x unicode
    assert len(_ladder_for_level(13)) == 16  # §4.9 UN-02: the whole seed
    row = await _row(db, user_id)
    assert (row.total_xp, row.level) == (1320, 6)
