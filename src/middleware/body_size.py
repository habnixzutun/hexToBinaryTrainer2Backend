"""US-32 AC-5: a hard ceiling on the answer endpoint's raw request body.

Why this is a middleware and not a validator
--------------------------------------------

FastAPI reads and parses a JSON body **before** it resolves dependencies, so by the
time any handler code — or any ``Depends`` — runs, the whole payload has already
been turned into Python objects. The answer endpoint's real body is three fields
(``{mode, bit_width, is_correct}``, about 70 bytes), so the payloads worth refusing
are precisely the ones whose *cost* is the parse itself: a 4300-digit integer
literal (CPython's ``int()`` cap turns that into a ``ValueError``, which FastAPI
renders as a 400 — after the conversion has been attempted), a megabyte of
whitespace, or a body that keeps streaming in chunks. Enforcing the ceiling in the
ASGI layer is the only way to make the refusal cost nothing: no JSON parser is
invoked, no pooled connection is checked out, and — unlike a handler check — no
rate-limit row or row lock is spent on a request that cannot be answered.

How the ceiling is held
-----------------------

The body is read here into a buffer that may never grow past ``limit + 1`` bytes:
as soon as the running total crosses the ceiling the request is answered with a 413
and the rest of the stream is left unread. Reading it *here* is deliberate — the
alternative of raising out of ``receive`` does not work, because FastAPI wraps its
own body read in ``except Exception`` and would translate a size error into a 400
"error parsing the body" (measured, not theorised). The buffered body is then
replayed to the application as a single complete ``http.request`` message, so
nothing downstream can tell that a middleware sat in front of it.

Two independent triggers, because they catch different clients:

* a declared ``Content-Length`` over the ceiling is refused **without reading a
  single body byte** — the cheapest possible refusal;
* a chunked or lying client that declares little and streams a lot is refused on
  the byte count it actually produced.

Scope
-----

Path- and method-scoped on purpose: ``POST /api/game/answer`` is the only endpoint
whose body is economy-critical. Applying a 4 KB cap to, say, a future avatar upload
would be a bug, so both are constructor arguments with the answer route as their
default. ``tests/test_answer_endpoint.py`` pins the path literal to the router's own
prefix, so the cap cannot be left pointing at a route that no longer exists.
"""

from __future__ import annotations

import logging
from typing import Final

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

__all__ = [
    "ANSWER_BODY_LIMIT_BYTES",
    "ANSWER_BODY_SIZE_PATH",
    "AnswerBodySizeLimitMiddleware",
]

# US-32 AC-5 — the ceiling, in bytes of raw request body. 4 KiB is generous against
# the largest legal body (three fields; even the widest ``bit_width`` spelling is a
# handful of bytes) and small enough that no JSON parser can be made to work hard
# for it. A module constant rather than a ``Settings`` field: no deployment tunes
# this per request shape, and the story pins the number.
ANSWER_BODY_LIMIT_BYTES: Final[int] = 4096

# The route the cap protects (``router.prefix`` + the route path in
# ``src/routers/game.py``).
ANSWER_BODY_SIZE_PATH: Final[str] = "/api/game/answer"


def _declared_length(scope: Scope) -> int | None:
    """The caller's own ``Content-Length``, or ``None`` when it cannot be believed.

    Read off the RAW ASGI scope rather than through ``Headers(scope=...)``,
    deliberately: Starlette's ``Headers`` applies its own normalization on top of the
    scope, and the ceiling has to hold for whatever actually reaches the middleware —
    including a scope assembled without an HTTP client in between (a proxy adapter, a
    direct ASGI caller, a test). Case is handled here for the same reason: the ASGI
    spec lowercases header names, so a compliant server never presents the mixed-case
    form, but a hand-built scope can, and a header the cap fails to see is a header
    the cap silently does not enforce.

    ``None`` means "the running byte count decides". Every unruly declaration lands
    there, because the count cannot lie:

    * absent — a chunked request declares no length at all;
    * unparsable — inventing a status code for an unreadable header would open a
      refusal path of its own, and the ceiling still holds (Wave 2a review M-3);
    * negative (M-3) — ``Content-Length: -1`` would otherwise satisfy
      ``declared > self.limit`` against every positive limit, handing a request that
      never sent a body a 413 for a size it never had. A negative length is not a
      size, so it declares nothing;
    * duplicated (RFC 9112 §6.3) — two headers that disagree have no winner; the
      stream does.
    """
    declared_values = [
        value
        for key, value in scope.get("headers", [])
        if key.lower() == b"content-length"
    ]
    if len(declared_values) != 1:
        return None  # absent, or ambiguous — neither can be believed

    try:
        declared = int(declared_values[0])
    except ValueError:
        return None
    return declared if declared >= 0 else None


class AnswerBodySizeLimitMiddleware:
    """Refuse an oversized answer body before FastAPI ever sees the request."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str = ANSWER_BODY_SIZE_PATH,
        limit: int = ANSWER_BODY_LIMIT_BYTES,
    ) -> None:
        self.app = app
        self.path = path
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.limit:
            # The refusal costs no body bytes at all: the client is told before the
            # first chunk is even awaited.
            await self._reject(scope, receive, send, observed_size=declared)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                # The client vanished mid-body, so there is nobody left to answer.
                # Reachable, not defensive padding (Wave 2a review M-3): Starlette
                # never SYNTHESIZES a disconnect to abort a handler, but the server
                # delivers one whenever the peer goes away while this loop is still
                # awaiting the next chunk — a cancelled upload, a flaky proxy, a
                # port-scan that opens the socket and drops it. Writing a response
                # there would raise on a closed transport, and the ceiling has
                # already been enforced by the bytes that did arrive.
                return
            body += message.get("body", b"")
            # Wave 2a review F3: ``more_body`` is OPTIONAL in the ASGI HTTP spec, and
            # ``body`` is too (hence the ``get`` two lines up). Reading it with ``[]``
            # would turn a spec-conformant single-message server into a ``KeyError``
            # inside ``receive`` — i.e. the 400 "error parsing the body" this class
            # buffers specifically to avoid.
            more_body = message.get("more_body", False)
            if len(body) > self.limit:
                await self._reject(scope, receive, send, observed_size=len(body))
                return

        async def replay() -> Message:
            """The buffered body, handed to the app as one complete message."""
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

    def _applies(self, scope: Scope) -> bool:
        return (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == self.path
        )

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        observed_size: int,
    ) -> None:
        """Send 413 with the same ``{detail}`` envelope the rest of the API uses.

        The response names the limit rather than the caller's size: a client that
        sent 40 MiB does not need it echoed back, and the number that matters for
        fixing the request is the ceiling. The observed size goes to the log at
        ``debug`` instead — a 413 flood must not become a log-flood amplifier.
        """
        logger.debug(
            "rejected %s %s body of %d bytes (limit %d)",
            scope.get("method"),
            scope.get("path"),
            observed_size,
            self.limit,
        )
        response = JSONResponse(
            status_code=413,
            content={
                "detail": (
                    f"Request body exceeds the {self.limit}-byte limit for this "
                    "endpoint."
                )
            },
        )
        await response(scope, receive, send)
