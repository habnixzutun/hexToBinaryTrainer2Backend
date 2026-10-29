"""Game router: the economy write path, the read side, and the leaderboard.

Three endpoints, all of them on ``user_game_profile``:

* **The Sprint-4 write path** ``POST /api/game/answer`` (US-33), which holds
  nothing but the **gate order** and its HTTP mappings::

      authenticate (401) -> validate body (422) -> rate-limit (429)
          -> unlock gate (403) -> transact (200 / 404)

* **The Sprint-4 read side** (US-35/US-36): ``GET /profile`` for the caller's own
  economy state and ``GET /leaderboard`` for the public ranking — the latter
  rewritten onto ``user_game_profile`` by US-36 while keeping its path, its
  public visibility (Rev 3 R-3/OQ-9) and its ``{entries: [...]}`` wrapper.

The Sprint-2 writers ``/correct`` and ``/incorrect`` are gone: **US-34** deleted
both handlers and, with migration v005, the table behind them. The plan froze that
ordering in D-6 / Rev 3 R-1 — the two-migration split exists precisely so the
legacy writers could keep serving until every reader had moved.

Every number in every response comes from a service module
(:mod:`src.services.answer_service`, :mod:`src.services.game_service`,
:mod:`src.services.profile_service`, :mod:`src.services.leaderboard_service`),
because arithmetic in a router is arithmetic no other caller can reuse and no unit
test can reach without HTTP.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import AsyncSessionLocal, get_db
from src.models.game_schemas import (
    AnswerRequest,
    AnswerResponse,
    LeaderboardResponseV2,
    ProfileResponse,
)
from src.routers.user import _extract_bearer_user_id
from src.services.answer_service import (
    answer_response,
    economy_seam,
    rate_limit_key,
    unlock_gate_detail,
    user_exists,
)
from src.services.game_config import (
    ANSWER_RATE_LIMIT,
    ANSWER_RATE_WINDOW_SECONDS,
)
from src.services.game_service import pre_answer_level, record_answer
from src.services.leaderboard_service import (
    LEADERBOARD_DEFAULT_LIMIT,
    LEADERBOARD_MAX_LIMIT,
    LEADERBOARD_MIN_LIMIT,
    leaderboard_entries,
)
from src.services.profile_service import load_snapshot, profile_response
from src.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/api/game", tags=["game"])


# US-36 replaced the legacy ``LeaderboardEntry`` / ``LeaderboardResponse`` pair with
# US-26's frozen ``LeaderboardEntryV2`` / ``LeaderboardResponseV2`` — the entry drops
# the old score field outright rather than aliasing it — and every wire shape this
# router answers with lives in the schema module next to the answer and profile
# contracts, not here.


# --------------------------------------------------------------------------- #
# US-33: POST /api/game/answer
# --------------------------------------------------------------------------- #

# The body cap of US-32 (``src/middleware/body_size.py``) is keyed to this exact
# path; ``tests/test_answer_endpoint.py`` asserts the two literals still agree,
# because a drift would silently leave the endpoint uncapped.
ANSWER_PATH = "/answer"


async def require_answer_identity(request: Request) -> str:
    """Gate step 1 — the caller, from the bearer token and nowhere else (401).

    An ``async def`` wrapper around ``user.py``'s parser so FastAPI keeps the work
    on the event loop instead of handing a header lookup to a threadpool. Nothing
    here touches the database: ``_extract_bearer_user_id`` raises its 401 before
    any session dependency is entered, which is what makes "no valid auth, no SQL"
    (N17/N18) a property of the boundary rather than of the handler's discipline.

    Returns:
        The ``sub`` claim — the only user identity this endpoint ever accepts (N8).
    """
    return _extract_bearer_user_id(request)


async def answer_rate_limit_session():
    """The rate limiter's OWN short-lived session (Rev 3 R-2, Option A).

    Not ``get_db``: ``RateLimitService`` commits, and a commit on the *answer*
    session would release the ``FOR UPDATE`` row lock mid-transaction (US-28
    AC-2/AC-1). Two sessions, two transactions, one request — but NOT two concurrent
    checkouts (Wave 2a review F2, which flagged the claim in the previous revision of
    this docstring as unmeasured): this session ends its transaction at its commit,
    and a SQLAlchemy session returns its connection to the pool at that point, while
    the answer transaction's first statement — the ``FOR UPDATE`` read — comes after
    it. So one answer in flight holds one pooled connection, which is what
    ``src/database/engine.py`` sizes for. Holding both at once is what an uncommitted
    limiter session would do, and it is the failure mode the pool probes against
    ``pool_size=1`` in the engine tests.
    """
    async with AsyncSessionLocal() as session:
        yield session


@router.post(
    ANSWER_PATH,
    status_code=status.HTTP_200_OK,
    response_model=AnswerResponse,
    summary="Record one answered question (Sprint 4 economy)",
)
async def submit_answer(
    body: AnswerRequest,
    user_id: str = Depends(require_answer_identity),
    rate_limit_session: AsyncSession = Depends(answer_rate_limit_session),
    session: AsyncSession = Depends(get_db),
) -> AnswerResponse:
    """Score one answer and return the caller's new economy state.

    The gates run in a fixed order, and each one is cheaper than the next:

    1. **Authenticate** (``require_answer_identity``) — 401 with zero SQL, before a
       session exists.
    2. **Validate** (``AnswerRequest``, US-32) — 422 from pydantic: per-mode width
       whitelist (D-7/OQ-7), strict ``is_correct``/``bit_width`` typing (D-8), no
       extra fields, so a client-computed ``total_xp`` or a body ``user_id`` cannot
       be smuggled in (N8). Measured order (Wave 2a review, MEDIUM): FastAPI reads
       the body and ENTERS this endpoint's dependencies before it validates, so a 422
       is raised before the handler body runs with the identity dependency already
       returned and **both session dependencies entered but inert** — no SQL, no pool
       checkout, no rate-limit row, because a session checks its connection out lazily
       at its first statement. That is the whole difference from step 1, where a 401
       short-circuits before the session dependencies are reached at all.
    3. **Rate-limit** (429) — on its own session, ahead of any row lock, so a
       throttled caller cannot hold another caller's transaction waiting. The
       counting entry point is ``check_without_cleanup`` (US-32): no ``DELETE`` runs
       on this path, and a refusal writes no partial state (the limiter commits only
       on the allowed branch, and the profile is never touched either way).
    4. **Unlock gate** (403) — inside the answer transaction, on the ``FOR UPDATE``
       locked **pre-answer** level (finding F-2), so an answer worth enough XP to
       reach the level it asks about is still refused. The locked read is
       ``game_service.pre_answer_level``, never routed through the seam.
    5. **Transact** (200) — ``record_answer`` with the composed ``economy_seam``,
       one commit at the very end (US-28 AC-2).

    Raises:
        HTTPException: 401 (no/bad/expired token), 422 (invalid body, FastAPI's own
            handling of ``AnswerRequest``), 429 (over budget, with ``Retry-After``),
            403 (combo locked at the pre-answer level), 404 (the token's user is
            gone — checked after the limiter and before the lock, so the lazy profile
            insert cannot trip the foreign key).
    """
    # (3) rate limiting — its own session, its own commit, nothing else's state.
    allowed = await RateLimitService(rate_limit_session).check_without_cleanup(
        rate_limit_key(user_id),
        ANSWER_RATE_LIMIT,
        ANSWER_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many answers for this user.",
            headers={"Retry-After": str(ANSWER_RATE_WINDOW_SECONDS)},
        )

    # (4a) the identity has to still exist before anything can be scored for it.
    # Checked here — after the limiter, before the lock — because the profile row is
    # created lazily by the first answer (US-28 AC-3) and the foreign key would
    # answer a deleted user with a 500.
    if not await user_exists(session, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # (4b) the unlock gate, on the locked PRE-answer level (finding F-2). This read
    # and the transaction below share `session`, so the row lock taken here is the
    # one the answer runs under — a 403 therefore rolls back a transaction that
    # never wrote anything.
    pre_level = await pre_answer_level(session, user_id)
    gate_detail = unlock_gate_detail(body.mode, body.bit_width, pre_level)
    if gate_detail is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=gate_detail)

    # (5) the answer itself: streak lifecycle plus the composed economy seam.
    outcome = await record_answer(
        session,
        user_id,
        is_correct=body.is_correct,
        economy=economy_seam(body.mode, body.bit_width),
    )
    return answer_response(outcome)


# --------------------------------------------------------------------------- #
# US-35: GET /api/game/profile
# --------------------------------------------------------------------------- #


@router.get(
    "/profile",
    status_code=status.HTTP_200_OK,
    response_model=ProfileResponse,
    summary="The caller's own economy state (Sprint 4 economy)",
)
async def get_profile(
    user_id: str = Depends(require_answer_identity),
    session: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Return the authenticated player's full economy state.

    Three steps, in the order US-33 froze for this router (Rev 3 R-10: the three
    authenticated reads answer the same identity questions the same way):

    1. **Authenticate** — ``require_answer_identity``, 401 with zero SQL, before a
       session exists.
    2. **Resolve** — 404 when the token names a user that is gone.
    3. **Read** — :func:`~src.services.profile_service.load_snapshot` and project
       it through :func:`~src.services.profile_service.profile_response`.

    A missing ``user_game_profile`` row is not an error and not a write: the row is
    created lazily by the first ``POST /api/game/answer`` (US-28 AC-3), so this read
    answers the D-1/OQ-3 zero state (level 1, 0 XP, the full L1 ladder) and inserts
    nothing — the test ``test_profile_does_not_create_the_row_it_reports_on`` pins
    exactly that, because a GET that wrote would put a lock on the hottest read path.

    ``unlocks`` is the FULL ladder for the derived level (§4.9 UN-02), built from
    US-26's :class:`UnlockEntry` — the same element type as ``AnswerResponse.
    newly_unlocked`` (R-9), so US-31's producer and US-38's toast parse one shape.

    Raises:
        HTTPException: 401 (no/bad/expired/refresh token), 404 (the token's user is
            gone).
    """
    if not await user_exists(session, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return profile_response(await load_snapshot(session, user_id))


# --------------------------------------------------------------------------- #
# US-36: GET /api/game/leaderboard
# --------------------------------------------------------------------------- #


@router.get(
    "/leaderboard",
    status_code=status.HTTP_200_OK,
    response_model=LeaderboardResponseV2,
    summary="Public top-XP ranking (Sprint 4 economy)",
)
async def get_leaderboard(
    limit: int = Query(
        default=LEADERBOARD_DEFAULT_LIMIT,
        ge=LEADERBOARD_MIN_LIMIT,
        le=LEADERBOARD_MAX_LIMIT,
        description="How many entries to return, 1..100.",
    ),
    session: AsyncSession = Depends(get_db),
) -> LeaderboardResponseV2:
    """Return the top ``limit`` players by total XP.

    **This endpoint is public — no authentication at all** (PO decision 2026-10-27,
    Rev 3 R-3 / OQ-9). Rev 2 had made it auth-required "for parity with the other
    game endpoints"; that would have broken a documented public contract and left the
    frontend's un-try/caught 30-second poll spinning forever. The abuse control here
    is the bounded ``limit`` rather than auth, which is why the same 1..100 bound is
    enforced twice: at this boundary (a 422 from FastAPI's ``Query`` validation) and
    again inside :func:`~src.services.leaderboard_service.leaderboard_entries`, so a
    future non-HTTP caller cannot ask for the whole table. The entry shape carries no
    PII — ``username`` plus game numbers, no email — which is what makes public-ness
    defensible in the first place.

    The path, the ``{entries: [...]}`` wrapper and the public visibility are all
    unchanged from the Sprint-2 board; what changed underneath is the source
    (``user_game_profile``, not the legacy counters table), the total order
    (``total_xp DESC``, ``incorrect_count ASC``, ``user_id ASC``) and the entry
    fields — the legacy score field is gone outright, with no deprecated alias,
    because an alias would only have been removed again by US-34.

    Raises:
        HTTPException: 422 (``limit`` outside 1..100, or not an integer).
    """
    return LeaderboardResponseV2(entries=await leaderboard_entries(session, limit))
