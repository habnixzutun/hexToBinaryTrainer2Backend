"""Game statistics models.

Two generations of game state live here side by side on purpose
(``docs/sprint4-plan.md`` D-6, Rev 3 R-1):

* :class:`UserStats` — the Sprint-2 legacy ``+bits / -4*bits`` row, table
  ``user_stats``. **Kept until US-34**: ``src/routers/game.py`` and
  ``src/routers/user.py`` still read and write it, and migration v004 (US-27) is
  ADDITIVE ONLY — ``DROP TABLE user_stats`` belongs to v005, shipped with US-34
  at sprint close. Deleting this class before then stops the app from booting
  (``src/main.py`` imports the game router) and breaks pytest collection.
* :class:`UserGameProfile` — the Sprint-4 XP-economy row, table
  ``user_game_profile`` (US-26). One row per user, lazily created by the first
  ``POST /api/game/answer`` (US-28 AC-3). Holds XP, level and streak state;
  the wire shapes live in :mod:`src.models.game_schemas`.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import relationship

from src.database.engine import Base


class UserStats(Base):
    """User game statistics (legacy — survives until US-34, see module docstring)."""

    __tablename__ = "user_stats"

    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    correct_count = Column(Integer, default=0, nullable=False)
    incorrect_count = Column(Integer, default=0, nullable=False)
    points = Column(Integer, default=0, nullable=False)

    user = relationship("User")


class UserGameProfile(Base):
    """Sprint-4 XP economy profile (US-26, plan D-1..D-8).

    ``user_id`` is both primary key and FK to ``users.id`` with
    ``ON DELETE CASCADE`` — one economy row per user, removed with the user and
    never created eagerly (the legacy lazy pattern at ``src/routers/game.py:72-79``
    is mirrored by ``src/services/game_service.py`` for US-28 AC-3).

    Column semantics:

    * ``total_xp`` — lifetime XP, always a multiple of 4 (D-8); starts at 0
      (D-1/OQ-3).
    * ``level`` — pure function of ``total_xp`` (D-1), recomputed inside the
      answer transaction (US-30), never below 1.
    * ``current_streak`` — consecutive correct answers, reset to 0 by any
      incorrect answer; no negative streak (D-5).
    * ``best_streak`` — high-water mark, never reset, always ``>= current_streak``.
    * ``correct_count`` / ``incorrect_count`` — lifetime answer counters (the
      leaderboard tiebreak of US-36).
    * ``updated_at`` — maintenance timestamp, refreshed on every UPDATE.

    Python-side (``default``) *and* server-side (``server_default``) defaults are
    declared: the ORM needs attributes populated without a refresh
    (``expire_on_commit=False`` throughout this project), while raw-SQL inserts —
    the migration path and US-41's fixtures — must land in the same state.
    """

    __tablename__ = "user_game_profile"

    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    total_xp = Column(Integer, nullable=False, default=0, server_default=text("0"))
    level = Column(Integer, nullable=False, default=1, server_default=text("1"))
    current_streak = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    best_streak = Column(Integer, nullable=False, default=0, server_default=text("0"))
    correct_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    incorrect_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    user = relationship("User")

    __table_args__ = (
        # D-5 / OQ-4: ``total_xp >= 0`` is a BACKSTOP ONLY. The real floor under
        # OQ-4 is ``cum(level)`` — the cumulative XP the current level requires
        # (plan D-1) — which is what makes demotion impossible. It is a geometric
        # sum and therefore NOT expressible as a SQL CHECK; the clamp lives in the
        # economy layer (US-30 ``clamped_total_xp``, applied by US-33) and is
        # pinned by LV-08/LV-09/US-41. This constraint can never fire before that
        # floor does; it only catches a writer outside the economy path.
        CheckConstraint("total_xp >= 0", name="ck_user_game_profile_total_xp"),
        # D-1/OQ-3: a player starts at level 1 with 0 XP; level 0 does not exist.
        CheckConstraint("level >= 1", name="ck_user_game_profile_level"),
        # D-5: an incorrect answer resets the streak to 0 and there is no
        # negative-streak concept, so 0 is the true floor for both counters.
        CheckConstraint(
            "current_streak >= 0", name="ck_user_game_profile_current_streak"
        ),
        CheckConstraint("best_streak >= 0", name="ck_user_game_profile_best_streak"),
        # US-36: the leaderboard orders by ``total_xp DESC`` (tiebreak
        # ``incorrect_count ASC``, then ``user_id ASC``); this index backs it.
        Index("ix_user_game_profile_total_xp", text("total_xp DESC")),
    )
