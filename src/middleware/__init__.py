"""ASGI middlewares (US-32 and onwards).

Kept in their own package because a middleware is neither a route nor a service:
it runs outside FastAPI's dependency graph, so it cannot be expressed as a
``Depends`` and does not belong in ``src/routers``.
"""

from src.middleware.body_size import (
    ANSWER_BODY_LIMIT_BYTES,
    AnswerBodySizeLimitMiddleware,
)

__all__ = ["ANSWER_BODY_LIMIT_BYTES", "AnswerBodySizeLimitMiddleware"]
