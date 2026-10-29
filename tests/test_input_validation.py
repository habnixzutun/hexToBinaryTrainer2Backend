"""US-32: input validation at the answer boundary — the negative matrix.

``AnswerRequest`` (US-26) is the whole rule set, so almost every assertion here is
made against the schema directly: it is fast, it needs no database, and it tests
the object the endpoint actually uses instead of a copy of its behaviour. The two
HTTP cases (the 422s reaching the wire, and N8's identity rule end-to-end) reuse
``conftest``'s ``app_dependency_override``.

What the matrix is defending:

* **Per-mode width whitelist (AC-1, OQ-7, D-7).** ``4`` is a legal conversion
  width and an illegal unicode one; ``24`` is the reverse. A single global set —
  or a hardcoded tuple inside the schema — passes the obvious cases and fails
  these two, so N3 walks the whole ``(mode, width)`` grid against
  :data:`game_config.VALID_WIDTHS_BY_MODE` (the totality canary) and the plan's
  literal table cross-checks the registry itself.
* **Strict typing (D-8).** ``True == 1`` and ``8.0 == 8`` in Python: a coercing
  schema would score a malformed request, and a float anywhere in the economy
  would break the "every total is a multiple of 4" invariant.
* **Identity comes from the bearer only (N8).** The body has no field for a user,
  and ``extra="forbid"`` makes a client-supplied ``user_id`` a refusal rather than
  something to ignore.
* **No DoS in the parser (N5).** A 4301-digit integer literal is refused by both
  JSON stacks involved (CPython's ``int()`` limit, pydantic-core's integer
  precision bound) instead of being converted, compared or stored.
"""

from __future__ import annotations

import itertools
import time

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from src.models.game_schemas import AnswerRequest
from src.services.game_config import MODES, VALID_WIDTHS_BY_MODE
from src.utils.jwt_utils import create_access_token
from uuid_utils import uuid7

# US-32 AC-1 / D-7 / OQ-7 as the plan spells them out — literals, so a registry
# that drifts from the plan is caught here and not by a test that reads the
# registry back at itself.
PLAN_WIDTHS: dict[str, tuple[int, ...]] = {
    "hex_to_bin": (4, 8, 16, 32),
    "hex_to_dec": (4, 8, 16, 32),
    "bin_to_dec": (4, 8, 16, 32),
    "unicode": (8, 16, 24, 32),  # 1..4 BYTES, and NOT 4 bits (D-7)
}

BASE: dict[str, object] = {
    "mode": "hex_to_bin",
    "bit_width": 4,
    "is_correct": True,
}

# Every width a hostile-but-valid-JSON client could plausibly try, covering the
# two swap cases (4 for unicode, 24 for the conversions) and the power-of-two
# neighbours a client might confuse with them.
PROBE_WIDTHS: tuple[int, ...] = tuple(range(1, 33)) + (48, 64, 128, 256, 1024)


def _accepts(**overrides: object) -> AnswerRequest:
    """Validate a body built from the legal default, with the given fields set."""
    return AnswerRequest.model_validate({**BASE, **overrides})


def _rejection(body: dict) -> list[dict]:
    """Assert the body is refused and return pydantic's error records."""
    with pytest.raises(ValidationError) as excinfo:
        AnswerRequest.model_validate(body)
    return excinfo.value.errors()


def _fields_of(errors: list[dict]) -> set[str | tuple[()]]:
    """The error locations: field names, plus ``()`` for model-level validators.

    The per-mode width rule lives in a ``model_validator(mode="after")``, so its
    error has no field location — which is exactly why the loc of a refusal is
    part of every assertion below instead of just "a 422 happened".
    """
    return {e["loc"][0] if e["loc"] else () for e in errors}


# --------------------------------------------------------------------------- #
# The registry is the plan (AC-1, cross-checked against literals)
# --------------------------------------------------------------------------- #


def test_the_registry_holds_the_plan_width_table():
    """``VALID_WIDTHS_BY_MODE`` IS US-32 AC-1, and the schema reads it (no copies)."""
    assert dict(VALID_WIDTHS_BY_MODE) == PLAN_WIDTHS
    assert set(MODES) == set(PLAN_WIDTHS)
    # D-7: bits in EVERY mode, unicode measured in bytes -> 8/16/24/32.
    assert PLAN_WIDTHS["unicode"][0] == 8
    assert 4 not in PLAN_WIDTHS["unicode"]
    assert 24 in PLAN_WIDTHS["unicode"]
    for mode in ("hex_to_bin", "hex_to_dec", "bin_to_dec"):
        assert PLAN_WIDTHS[mode] == (4, 8, 16, 32)


def test_every_registry_combo_is_accepted():
    """Totality, half A: every ``(mode, width)`` the registry declares is legal."""
    for mode in MODES:
        for width in VALID_WIDTHS_BY_MODE[mode]:
            request = _accepts(mode=mode, bit_width=width)
            assert (request.mode, request.bit_width) == (mode, width)


# --------------------------------------------------------------------------- #
# N3: the totality canary — per mode, every width, in both directions
# --------------------------------------------------------------------------- #


def test_n3_totality_every_mode_width_pair_is_ruled_exactly_once():
    """N3: the whitelist is complete and per-mode, walked over the whole grid.

    For all 4 modes x all widths 1..32 (plus the larger power-of-two traps): the
    schema accepts exactly the registry's pairs and refuses everything else with
    the model-level rule, never with a missing-field or type error. A global width
    set, a per-mode typo, or a hardcoded tuple in the schema all fail here — and
    they fail on the two cells that a global set cannot express: ``("unicode", 4)``
    and ``("hex_to_bin", 24)``.
    """
    grid = itertools.product(MODES, PROBE_WIDTHS)
    accepted: list[tuple[str, int]] = []
    refused: list[tuple[str, int]] = []

    for mode, width in grid:
        try:
            _accepts(mode=mode, bit_width=width)
        except ValidationError as exc:
            errors = exc.errors()
            assert _fields_of(errors) == {()}, (mode, width, errors)
            message = " ".join(str(error["msg"]) for error in errors)
            assert str(width) in message, (mode, width, message)
            for allowed in VALID_WIDTHS_BY_MODE[mode]:
                assert str(allowed) in message, (mode, width, message)
            refused.append((mode, width))
        else:
            accepted.append((mode, width))

    assert accepted == [
        (mode, width)
        for mode in MODES
        for width in PLAN_WIDTHS[mode]  # plan literals, not the registry
    ]
    assert set(accepted) & set(refused) == set()
    assert len(accepted) + len(refused) == len(MODES) * len(PROBE_WIDTHS)
    # The two cells a global set gets backwards, spelled out.
    assert ("unicode", 4) in refused
    assert ("hex_to_bin", 24) in refused
    assert ("unicode", 8) in accepted
    assert ("bin_to_dec", 4) in accepted


# --------------------------------------------------------------------------- #
# N1: missing fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["mode", "bit_width", "is_correct"])
def test_n1_missing_field_is_refused_and_names_the_field(missing: str) -> None:
    """N1: every field is required, and the error blames exactly that field."""
    body = {key: value for key, value in BASE.items() if key != missing}

    errors = _rejection(body)

    assert _fields_of(errors) == {missing}
    assert all(error["type"] == "missing" for error in errors)
    assert "Field required" in errors[0]["msg"]


def test_an_empty_body_refuses_all_three_fields_at_once() -> None:
    """No defaults, no partial acceptance: ``{}`` is three errors, not one."""
    errors = _rejection({})

    assert _fields_of(errors) == {"mode", "bit_width", "is_correct"}


# --------------------------------------------------------------------------- #
# N2 / N6: wrong types and unknown modes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", 123),
        ("mode", None),
        ("mode", ["hex_to_bin"]),
        ("mode", {"mode": "hex_to_bin"}),
        ("mode", ""),
        ("mode", "HEX_TO_BIN"),  # the registry is lowercase (D-4)
        ("mode", "hex_to_binary"),  # a near miss is not a fuzzy match
        ("mode", "convert_hex"),  # the OLD frontend vocabulary
        ("bit_width", None),
        ("bit_width", "four"),
        ("bit_width", [4]),
        ("is_correct", None),
        ("is_correct", "yes"),
        ("is_correct", {}),
    ],
)
def test_n2_wrong_type_is_refused(field: str, value: object) -> None:
    """N2: a wrong type is a 422 on that field, never a coercion and never a default."""
    errors = _rejection({**BASE, field: value})

    assert field in _fields_of(errors), errors
    assert all(error["type"] != "missing" for error in errors)


@pytest.mark.parametrize(
    "value",
    [
        "hex_to_bin ",
        " hex_to_bin",
        "hexToBin",
        "hex-to-bin",
        "unicode_char",
        "binary_to_decimal",
    ],
)
def test_n6_mode_is_a_closed_literal(value: str) -> None:
    """N6: the mode set is the registry's closed Literal.

    Whitespace, casing, hyphens and neighbour spellings all have to be refused with a
    ``literal_error`` on ``mode`` and nothing else — no aliasing, no trimming.
    """
    errors = _rejection({**BASE, "mode": value})

    assert _fields_of(errors) == {"mode"}
    assert errors[0]["type"] == "literal_error"


# --------------------------------------------------------------------------- #
# N4: strict typing — bool is not int, float is not int
# --------------------------------------------------------------------------- #


def test_n4_bool_is_not_an_integer_bit_width() -> None:
    """N4: ``True`` is ``1`` to Python and must still be refused (D-8)."""
    for value in (True, False):
        errors = _rejection({**BASE, "bit_width": value})
        assert "bit_width" in _fields_of(errors), (value, errors)


@pytest.mark.parametrize("value", [8.0, 4.0, 4.5, 1e1, float("inf")])
def test_n4_floats_are_refused_even_when_integral(value: float) -> None:
    """N4: an integral float is not an int here — the economy has no float seam.

    ``8.0 == 8`` is true and would let a JSON ``8.0`` through a non-strict schema,
    after which every downstream product would inherit a float total and the
    "multiple of 4" invariant (D-8) would be lost silently.
    """
    errors = _rejection({**BASE, "bit_width": value})

    assert "bit_width" in _fields_of(errors), (value, errors)


@pytest.mark.parametrize("value", [1, 0, "true", "True", "1", 1.0, 0.0, [True]])
def test_n4_is_correct_accepts_only_a_json_boolean(value: object) -> None:
    """N4 / AC-1: ``is_correct`` is strictly a boolean — no 0/1, no "true".

    A truthy-string client bug must not be able to buy XP: ``"true"`` is truthy in
    Python, and ``1`` is a valid int for every other field on this body.
    """
    errors = _rejection({**BASE, "is_correct": value})

    assert "is_correct" in _fields_of(errors), (value, errors)


@pytest.mark.parametrize("value", ["4", "8"])
def test_a_numeric_string_bit_width_is_refused_by_strict_mode(value: str) -> None:
    """``strict=True`` also closes the ``"8" -> 8`` string coercion (N4)."""
    errors = _rejection({**BASE, "bit_width": value})

    assert "bit_width" in _fields_of(errors), errors


# --------------------------------------------------------------------------- #
# N7: widths inside the legal range but outside the mode's set
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mode", "width"),
    [
        ("unicode", 4),  # D-7: unicode has no 4-bit slot at all
        ("hex_to_bin", 24),
        ("hex_to_dec", 24),
        ("bin_to_dec", 12),
        ("hex_to_bin", 3),
        ("unicode", 12),
        ("unicode", 64),
    ],
)
def test_n7_width_outside_the_mode_whitelist(mode: str, width: int) -> None:
    """N7: a width another mode speaks is refused, never reinterpreted (AC-1).

    The message is part of the contract for the client: it names the refused width
    and the allowed set, so a 422 is actionable without the docs open.
    """
    errors = _rejection({"mode": mode, "bit_width": width, "is_correct": True})

    assert _fields_of(errors) == {()}  # the model-level per-mode rule
    message = " ".join(str(error["msg"]) for error in errors)
    assert str(width) in message
    assert str(VALID_WIDTHS_BY_MODE[mode][0]) in message
    assert "not a valid width" in message


@pytest.mark.parametrize("width", [0, -4, -1])
def test_bit_width_below_one_is_a_range_error_not_a_whitelist_error(
    width: int,
) -> None:
    """``ge=1`` fires on the field, so the loc is the field and not the model."""
    errors = _rejection({**BASE, "bit_width": width})

    assert "bit_width" in _fields_of(errors), errors
    assert any(error["type"] == "greater_than_equal" for error in errors)


# --------------------------------------------------------------------------- #
# Extra fields: the client cannot talk about the economy or about its own identity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field",
    [
        "user_id",  # N8: identity comes from the bearer
        "username",
        "xp",
        "xp_delta",
        "total_xp",  # D-7: no client-computed XP, ever
        "points",  # the Sprint-2 score name US-34 retired with its table
        "level",
        "current_streak",
        "best_streak",
        "correct_count",
        "incorrect_count",
        "bits",  # the body field of the retired legacy writers
        "direction",
        "timestamp",
    ],
)
def test_extra_fields_are_forbidden_not_ignored(field: str) -> None:
    """``extra="forbid"``: an unknown key is a 422, so typos cannot pass silently.

    This is how N8's "the body's ``user_id`` is ignored" is actually realized —
    refused outright, which is strictly stronger than parsed-and-discarded: no code
    path can ever read it.
    """
    errors = _rejection({**BASE, field: "anything"})

    assert _fields_of(errors) == {field}, errors
    assert any(error["type"] == "extra_forbidden" for error in errors)


# --------------------------------------------------------------------------- #
# N5: a huge integer literal cannot cost more than a refusal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("digits", [4301, 4400, 20_000])
def test_n5_huge_integer_literals_are_refused_quickly(digits: int) -> None:
    """N5: the parse itself is bounded — no conversion, no comparison, no storage.

    Two independent limits close this door, and both are refusals rather than work.
    pydantic-core's own JSON scanner rejects a numeric literal it cannot represent
    (``json_invalid`` / "number out of range") during the scan, so the value never
    becomes an ``int`` at all; CPython's 4300-digit ``int()`` cap
    (``sys.set_int_max_str_digits``) closes the stdlib-JSON path that FastAPI uses
    for ``await request.json()``, as a ``ValueError`` the app renders as a 400. The
    wall-clock bound is what turns "neither hangs" into an assertion instead of a
    hope. The endpoint-level twin in ``tests/test_answer_endpoint.py`` shows the
    4 KB body cap stopping the same request even earlier — 4301 digits cannot fit in
    4096 bytes, so the cap SUBSUMES the digit limit rather than racing it.
    """
    raw = (
        b'{"mode": "hex_to_bin", "bit_width": '
        + b"9" * digits
        + b', "is_correct": true}'
    )

    started = time.monotonic()
    with pytest.raises((ValidationError, ValueError)) as excinfo:
        AnswerRequest.model_validate_json(raw)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"{digits}-digit literal took {elapsed:.3f}s"
    if isinstance(excinfo.value, ValidationError):
        types = {error["type"] for error in excinfo.value.errors()}
        assert types <= {"json_invalid", "int_precision", "value_error"}, (
            digits,
            excinfo.value.errors(),
        )


def test_the_digit_limit_is_not_a_validation_hole() -> None:
    """Under the cap the whitelist still rules: 4300 ones is an int, and an illegal one.

    4300 digits is the largest ``int()`` conversion CPython permits, so this is the
    worst case that reaches the schema as a real integer — and it is refused by the
    per-mode rule exactly like 1024 is, in bounded time.
    """
    raw = (
        b'{"mode": "hex_to_bin", "bit_width": ' + b"1" * 4300 + b', "is_correct": true}'
    )

    started = time.monotonic()
    with pytest.raises(ValidationError) as excinfo:
        AnswerRequest.model_validate_json(raw)
    assert time.monotonic() - started < 1.0

    assert _fields_of(excinfo.value.errors()) & {(), "bit_width"}


# --------------------------------------------------------------------------- #
# The wire: the same refusals arrive as 422s, and N8 holds end to end
# --------------------------------------------------------------------------- #


def _bearer(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "hex_to_bin", "bit_width": 4},  # N1: is_correct missing
        {"bit_width": 4, "is_correct": True},  # N1: mode missing
        {"mode": "hex_to_bin", "is_correct": True},  # N1: bit_width missing
        {"mode": "hex_to_decimal", "bit_width": 8, "is_correct": True},  # N6
        {"mode": "unicode", "bit_width": 4, "is_correct": True},  # N7 / D-7
        {"mode": "hex_to_bin", "bit_width": "8", "is_correct": True},  # N4
        {"mode": "hex_to_bin", "bit_width": 8.0, "is_correct": True},  # N4
        {"mode": "hex_to_bin", "bit_width": 4, "is_correct": 1},  # N4
        {
            "mode": "hex_to_bin",
            "bit_width": 4,
            "is_correct": True,
            "total_xp": 9999,
        },  # D-7
        {
            "mode": "hex_to_bin",
            "bit_width": 4,
            "is_correct": True,
            "user_id": "x",
        },  # N8
    ],
)
async def test_the_schema_refusals_surface_as_422(
    app_dependency_override, body
) -> None:
    """US-32 AC-1/AC-2 on the wire: pydantic's refusal is FastAPI's 422, unchanged.

    No database access is needed to prove this (the override exists so the request
    cannot reach the development database if the environment ever changes), and no
    rate-limit row may be spent by a body that never validated.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app_dependency_override), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/game/answer", json=body, headers=_bearer("someone")
        )

    assert response.status_code == 422, (body, response.text)
    detail = response.json()["detail"]
    assert detail, body
    # The refusal came from the BODY (not a header, not a query parameter), which is
    # what makes it US-32's input validation rather than an auth or routing accident.
    assert all(error["loc"][0] == "body" for error in detail), detail


async def test_n8_identity_comes_from_the_bearer_alone(
    app_dependency_override, async_engine
) -> None:
    """N8: a body cannot name a user, and a valid request scores the bearer's row.

    Two halves, because "ignored" has to be proved from both sides: a body that
    carries ``user_id`` is refused outright (``extra="forbid"`` — stronger than
    discarding it, since no code path can read it), and a clean body scores the
    token's subject even when the token belongs to a different user than some other
    row in the database.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from src.models.auth import User

    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        caller = str(uuid7())
        victim = str(uuid7())
        # The RANDOM tail, not the leading hex: two uuid7s minted in the same
        # millisecond share their first 20 hex digits, and ``users.username`` is
        # unique — slicing the front would collide across these very rows.
        session.add_all(
            [
                User(
                    id=caller,
                    username=f"n8-{caller.replace('-', '')[-12:]}",
                    email=f"{caller}@e.com",
                ),
                User(
                    id=victim,
                    username=f"n8-{victim.replace('-', '')[-12:]}",
                    email=f"{victim}@e.com",
                ),
            ]
        )
        await session.commit()

        # The limiter of US-32 opens a SECOND session, through a dependency this
        # pre-US-33 fixture knows nothing about. Without the override below it
        # checks a connection out of the app-global ``AsyncSessionLocal`` pool —
        # created in whichever earlier test's event loop touched ``src.database.
        # engine`` first — which surfaces as a cross-loop ``RuntimeError`` only in a
        # full-suite run, and would write a real ``rate_limit_attempt`` row besides.
        # This is the one new test that sends a VALID body, i.e. the only one whose
        # dependencies actually run: a 422 never reaches them.
        from src.routers.game import answer_rate_limit_session

        async def _limiter_session():
            async with factory() as limiter_session:
                yield limiter_session

        app_dependency_override.dependency_overrides[answer_rate_limit_session] = (
            _limiter_session
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override), base_url="http://test"
        ) as client:
            smuggled = await client.post(
                "/api/game/answer",
                json={
                    "mode": "hex_to_bin",
                    "bit_width": 4,
                    "is_correct": True,
                    "user_id": victim,
                },
                headers=_bearer(caller),
            )
            scored = await client.post(
                "/api/game/answer",
                json={"mode": "hex_to_bin", "bit_width": 4, "is_correct": True},
                headers=_bearer(caller),
            )

            rows = {
                row.user_id: row
                for row in (
                    await session.execute(
                        text(
                            "SELECT user_id, total_xp FROM user_game_profile "
                            "WHERE user_id = ANY(:ids)"
                        ),
                        {"ids": [caller, victim]},
                    )
                ).all()
            }

    assert smuggled.status_code == 422, smuggled.text
    assert scored.status_code == 200, scored.text
    assert scored.json()["total_xp"] == 16
    assert rows.get(caller) is not None and rows[caller].total_xp == 16
    assert victim not in rows  # the named user was never touched
