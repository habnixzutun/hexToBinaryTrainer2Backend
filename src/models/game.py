"""Game statistics models.

One generation of game state lives here now:

* :class:`UserGameProfile` — the Sprint-4 XP-economy row, table
  ``user_game_profile`` (US-26). One row per user, lazily created by the first
  ``POST /api/game/answer`` (US-28 AC-3). Holds XP, level and streak state;
  the wire shapes live in :mod:`src.models.game_schemas`.

The Sprint-2 legacy row (the ``+bits / -4*bits`` counters) was deleted together
with its table by **US-34**: migration v005 drops the table, and no reader or
writer of it survived the XP-economy endpoints (``/answer``, ``/profile``,
``/leaderboard``, ``/api/user/me``). The plan froze that ordering — D-6 / Rev 3
R-1 splits the schema change into v004 (additive) and v005 (destructive), and
US-34 is the only story allowed to delete the class.
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


class UserGameProfile(Base):
    """Sprint-4 XP economy profile (US-26, plan D-1..D-8).

    ``user_id`` is both primary key and FK to ``users.id`` with
    ``ON DELETE CASCADE`` — one economy row per user, removed with the user and
    never created eagerly: the row appears at the first answer that needs it
    (``src/services/game_service.py``, US-28 AC-3), not at registration.

    Column semantics:

    * ``total_xp`` — lifetime XP, always a multiple of 4 (D-8); starts at 0
      (D-1/OQ-3).
    * ``level`` — pure function of ``total_xp`` (D-1), never below 1. Stored by the
      answer transaction (US-30) as a **denormalization** for raw-SQL readers; every
      report path — ``/answer``, ``/profile``, ``/me`` and the leaderboard — derives
      it from ``total_xp`` with :func:`~src.services.level_service.level_from_xp` and
      reads this column nowhere (Phase 2b H1) — see
      ``src/services/game_service.py`` ("One level, one source").
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
