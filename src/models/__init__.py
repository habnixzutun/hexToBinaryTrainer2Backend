# Auth models (US-09)
from .auth import (
    PasswordResetToken,
    RateLimitAttempt,
    User,
    UserAuditLog,
    UserIdentity,
    UserRefreshToken,
)

# Game models. UserStats is the legacy Sprint-2 row and stays exported until
# US-34 (plan D-6 / Rev 3 R-1: v004 is additive, the table and the ORM class
# survive the whole co-existence window). UserGameProfile is the Sprint-4
# XP-economy row (US-26). The Pydantic wire schemas live in .game_schemas and —
# like .auth_schemas — are imported by path, not re-exported here.
from .game import UserGameProfile, UserStats

__all__ = [
    "User",
    "UserIdentity",
    "UserRefreshToken",
    "PasswordResetToken",
    "UserAuditLog",
    "RateLimitAttempt",
    "UserStats",
    "UserGameProfile",
]
