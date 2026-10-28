"""US-26: Pydantic v2 wire schemas for the Sprint-4 XP economy.

The contract layer between client and the economy. Normative source:
``docs/sprint4-plan.md`` — US-26 (this module), US-32 (validation rules),
US-33 (``POST /api/game/answer`` request/response), US-35 (``/profile``),
US-36 (leaderboard v2), plus D-6/D-7/D-8 and the PO decisions OQ-7/OQ-8.

What is pinned here and consumed downstream:

* :class:`AnswerRequest` — the ONLY body the answer endpoint accepts:
  ``{mode, bit_width, is_correct}``, extra fields forbidden (D-7). The server
  never trusts a client-computed XP/points/streak value, so there is no field
  for one — but the trust boundary stops at the *verdict*: ``is_correct`` is the
  client's own assertion and the server has **no oracle** to check it against
  (no challenge nonce, no answer-history table). Every XP figure is then derived
  faithfully from a premise the client chose, which is a Sprint-4 PO decision
  (2026-10-27) recorded in ``docs/sprint4-plan.md`` (out-of-scope list and the
  risk table), not an oversight of this schema; the 120/60 s rate limit is the
  only mitigation, and it is a DoS control rather than an XP ceiling. Pinned by
  ``tests/test_answer_endpoint.py::test_t01_is_correct_is_client_asserted_with_no_oracle``.
  ``bit_width`` is validated **per mode** and carries **bits in every
  mode** (OQ-7): 4/8/16/32 for the conversion modes, 8/16/24/32 for ``unicode``
  — so ``("unicode", 4)`` is a 422 while ``("unicode", 8)`` is valid slot 1. The
  rule is driven by :data:`src.services.game_config.VALID_WIDTHS_BY_MODE`, never
  by a hardcoded tuple and never by a global width set (``4`` is a legal
  conversion width and an illegal unicode one; ``24`` is the reverse).
* :class:`AnswerResponse` / :class:`ProfileResponse` — the US-33/US-35 response
  shapes. The per-level span field is named **``xp_required_for_next_level``**
  on the wire (OQ-8); a stale ``xp_required_for_level`` key is rejected, not
  ignored, because both models forbid extra fields.
* :class:`UnlockEntry` — ONE element type for ``newly_unlocked`` (US-33) and
  ``unlocks`` (US-35), shape ``{mode, bit_width, min_level}`` (R-9).
* :class:`LeaderboardEntryV2` + :class:`LeaderboardResponseV2` — US-36: entry
  without ``points`` and without PII, inside the unchanged ``{entries: [...]}``
  wrapper.

Like ``game_config``, this module is import-side-effect free (pydantic + the
frozen registry), so routers, tests and OpenAPI generation can all import it.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.game_config import VALID_WIDTHS_BY_MODE, GameMode

__all__ = [
    "ANSWER_PAYLOAD_BUDGET_BYTES",
    "AnswerRequest",
    "AnswerResponse",
    "LeaderboardEntryV2",
    "LeaderboardResponseV2",
    "ProfileResponse",
    "UnlockEntry",
]

# --------------------------------------------------------------------------- #
# Request (US-32 / D-7)
# --------------------------------------------------------------------------- #

# S-5 (Wave 2a review) — the ceiling on the REFUSAL, complementary to the 4 KiB
# ceiling on the REQUEST in ``src/middleware/body_size.py``. Generous against the
# largest legal body by far: every valid request is three short fields (mode <= 13
# chars, bit_width <= 2 digits), so a legal body estimates well under 60 bytes and
# no honest client can come near 256. What the number buys is a bound on the server's
# own output: a refusal can no longer be made to echo a 3000-digit integer.
ANSWER_PAYLOAD_BUDGET_BYTES: Final[int] = 256

# The JSON skeleton of an answer body with every value removed, measured rather than
# counted by hand so a future field cannot silently invalidate the budget arithmetic.
_ANSWER_JSON_SKELETON_BYTES: Final[int] = len('{"mode":"","bit_width":,"is_correct":}')


class AnswerRequest(BaseModel):
    """Body of ``POST /api/game/answer`` (US-33), validated per US-32.

    Exactly three fields: the client declares *what* it answered and *whether*
    it was right — every number the economy derives from that is the server's
    (D-7: "server never trusts client-computed XP/points/streak"). The one thing
    the server does take on trust is the verdict: ``is_correct`` has no
    server-side oracle, which is the accepted risk T-01 of the Wave 2a security
    review (PO decision 2026-10-27, ``docs/sprint4-plan.md``) and not an
    oversight of this schema.
    """

    model_config = ConfigDict(extra="forbid")

    mode: GameMode = Field(
        description=(
            "Play mode (direction-agnostic; the frontend owns the direction). "
            "Determines base XP, mode multiplier, valid widths and unlock group."
        )
    )
    bit_width: int = Field(
        strict=True,
        ge=1,
        description=(
            "Question width IN BITS, for every mode (OQ-7): 4/8/16/32 for "
            "hex_to_bin / hex_to_dec / bin_to_dec, and 8/16/24/32 for unicode "
            "(= 1..4 bytes). Validated per mode — see "
            "`game_config.VALID_WIDTHS_BY_MODE`; a width that is legal in "
            "another mode is rejected with 422, never reinterpreted."
        ),
    )
    is_correct: bool = Field(
        strict=True,
        description=(
            "Whether the player's answer was correct. Required, and strictly a "
            "JSON boolean: no coercion of 0/1/'true'/'yes' on the XP write path. "
            "This is the CLIENT'S VERDICT and the server has no way to check it "
            "(no challenge nonce, no answer history) — the XP figures below are "
            "derived faithfully from it, so an honest client is the whole trust "
            "model. Accepted risk T-01, PO decision 2026-10-27: hardening is a "
            "challenge-nonce/HMAC scheme parked for US-40 to file in "
            "`docs/backlog.md`; the 120 answers / 60 s rate limit is the only "
            "mitigation shipped, and it bounds request rate, not XP."
        ),
    )

    @model_validator(mode="after")
    def _validate_payload_budget(self) -> AnswerRequest:
        """S-5 (Wave 2a review): bound what a refusal may say, whatever was sent.

        ``ANSWER_BODY_LIMIT_BYTES`` bounds the request; this bounds the answer's own
        text. The width rule below interpolates ``bit_width`` into its message, so a
        legal-under-4-KiB body can carry a 3000-digit integer and make the server
        print 3000 digits — a refusal larger than the request the client bothered to
        send, and free outbound bandwidth for anyone ping-ponging it.

        The estimate is the field values plus the fixed JSON skeleton, never the raw
        body: whitespace padding is the middleware's business, and a body that is
        legal-but-bulky must keep scoring (that is what the exactly-4096-bytes test
        pins). Decimal digits never exceed an integer's bit length, so
        :meth:`int.bit_length` bounds ``len(str(bit_width))`` without materialising a
        3000-character string — exact enough to reject what it must, and integer-only
        so the economy's D-8 rule has no float in sight here either.

        Runs **before** :meth:`_validate_width_for_mode` because pydantic applies
        ``mode="after"`` validators in definition order, and the budget is worthless
        if the message it exists to suppress has already been built.
        """
        estimated = (
            _ANSWER_JSON_SKELETON_BYTES
            + len(self.mode)
            + self.bit_width.bit_length()
            + len("false")  # the longest rendering of the boolean
        )
        if estimated > ANSWER_PAYLOAD_BUDGET_BYTES:
            # Deliberately names no field and reflects no value: the client already
            # knows what it sent, and this message's job is to be small.
            raise ValueError(
                "answer payload exceeds the "
                f"{ANSWER_PAYLOAD_BUDGET_BYTES}-byte budget for this endpoint"
            )
        return self

    @model_validator(mode="after")
    def _validate_width_for_mode(self) -> AnswerRequest:
        """Per-mode width whitelist (US-32 AC-1, OQ-7, UN-06)."""
        allowed = VALID_WIDTHS_BY_MODE[self.mode]
        if self.bit_width not in allowed:
            raise ValueError(
                f"bit_width {self.bit_width} is not a valid width for mode "
                f"'{self.mode}'; allowed (bits): {', '.join(str(w) for w in allowed)}"
            )
        return self


# --------------------------------------------------------------------------- #
# Shared unlock element (D-2 / R-9)
# --------------------------------------------------------------------------- #


class UnlockEntry(BaseModel):
    """One ``(mode, bit_width)`` available from ``min_level`` (D-2 seed, §4.9).

    The single element type behind ``AnswerResponse.newly_unlocked`` (US-33) and
    ``ProfileResponse.unlocks`` (US-35), so US-31's producer and US-38's toast
    consume one shape (pinned in Rev 3, R-9). ``bit_width`` is bits in every mode
    (OQ-7), and ``min_level`` is the level from which the combo is playable.
    """

    model_config = ConfigDict(extra="forbid")

    mode: GameMode
    bit_width: int = Field(
        ge=1, description="Width in BITS (OQ-7, unicode 8/16/24/32)."
    )
    min_level: int = Field(
        ge=1, description="First level the combo is playable at (D-2)."
    )


# --------------------------------------------------------------------------- #
# Responses (US-33 / US-35 / US-36)
# --------------------------------------------------------------------------- #


class AnswerResponse(BaseModel):
    """200 body of ``POST /api/game/answer`` — the US-33 contract, frozen.

    Field names and the OQ-8 wire name ``xp_required_for_next_level`` are part of
    the contract; the plan's golden fixture (HB-8 at streak 3, ``total_xp`` 1320 →
    L6, ``xp_in_level`` 0, ``req(6)`` 760, ``newly_unlocked: []`) is reproduced
    byte-for-byte by ``tests/test_game_profile_models.py``.

    Sign convention (D-8/R-9): ``xp_delta`` is the **signed, post-clamp** delta
    ``total_xp_new - total_xp_old`` — positive on a correct answer, negative on a
    penalty, and ``0`` when the OQ-4 floor at ``cum(level)`` blocks it (LV-09b).
    """

    model_config = ConfigDict(extra="forbid")

    xp_delta: int = Field(description="Signed, post-clamp change to total_xp (4.1).")
    total_xp: int = Field(
        ge=0, description="Lifetime XP after this answer, multiple of 4."
    )
    level: int = Field(ge=1, description="Level recomputed from total_xp (D-1).")
    xp_in_level: int = Field(
        ge=0, description="total_xp - cum(level); never negative (LV-10)."
    )
    xp_required_for_next_level: int = Field(
        ge=1,
        description=(
            "XP span from this level to the next, i.e. req(level) (D-1/OQ-8). "
            "NOT a cumulative threshold — the wire name says so."
        ),
    )
    current_streak: int = Field(
        ge=0, description="Post-increment streak on correct (OQ-5)."
    )
    best_streak: int = Field(ge=0, description="High-water mark, never reset (D-5).")
    correct_count: int = Field(ge=0)
    incorrect_count: int = Field(ge=0)
    newly_unlocked: list[UnlockEntry] = Field(
        default_factory=list,
        description=(
            "Delta set of unlocks fired by THIS answer (US-31). Empty on almost "
            "every answer, including a level-up with no unlock row (R-9)."
        ),
    )


class ProfileResponse(BaseModel):
    """200 body of ``GET /api/game/profile`` (US-35).

    Same economy numbers as :class:`AnswerResponse` minus the per-answer delta,
    plus the FULL unlock set for the caller's level (``unlocks``), exported so
    the client never hardcodes the D-2 seed. Element shape shared via
    :class:`UnlockEntry`.
    """

    model_config = ConfigDict(extra="forbid")

    total_xp: int = Field(ge=0)
    level: int = Field(ge=1)
    xp_in_level: int = Field(ge=0)
    xp_required_for_next_level: int = Field(ge=1)
    current_streak: int = Field(ge=0)
    best_streak: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    incorrect_count: int = Field(ge=0)
    unlocks: list[UnlockEntry] = Field(
        description="All (mode, bit_width) combos playable at the caller's level (D-2)."
    )


class LeaderboardEntryV2(BaseModel):
    """One row of the public leaderboard (US-36).

    Deliberately **no** ``points`` — the legacy field is dropped, not aliased
    (Rev 3: no deprecated alias to remove again in US-34) — and no PII: an entry
    carries ``username`` plus game numbers only, which is what keeps the endpoint
    public without auth (PO decision R-3/OQ-9).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    total_xp: int = Field(ge=0)
    level: int = Field(ge=1)
    correct_count: int = Field(ge=0)
    incorrect_count: int = Field(
        ge=0, description="Tiebreak 1: fewer mistakes ranks higher."
    )


class LeaderboardResponseV2(BaseModel):
    """The unchanged ``{entries: [...]}`` wrapper (US-36)."""

    model_config = ConfigDict(extra="forbid")

    entries: list[LeaderboardEntryV2]
