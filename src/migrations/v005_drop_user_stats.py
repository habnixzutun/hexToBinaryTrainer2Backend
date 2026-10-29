"""US-34: drop the legacy Sprint-2 statistics table (``user_stats``).

Sprint 4, plan ``docs/sprint4-plan.md`` D-6 (Rev 3, R-1). The schema change was
split across TWO migrations on purpose so that no commit in the cascade left the
suite red:

* **v004 (US-27) — CREATE only.** Added ``user_game_profile`` and left the legacy
  table in place, so that era's two legacy score writers kept working through the
  whole co-existence window.
* **v005 (this file, US-34) — DROP.** Legal only once every reader and writer of
  the legacy counters is gone, which is what US-32/US-33 (write side) and
  US-35/US-36/US-37 (read side) delivered before sprint close.

Both halves of that precondition hold at this commit: ``src/models/game.py`` now
declares :class:`src.models.game.UserGameProfile` alone, and the game router
serves ``/answer``, ``/profile`` and ``/leaderboard`` only.

``DROP TABLE IF EXISTS`` makes the statement a native no-op on a database that
never ran v002 (a fresh install, or a test schema built from the ORM), so the end
state does not depend on the migration history — the same independence v004
claims from v002 (US-27 AC-5). The table is a LEAF: its only foreign key runs
outward to ``users``, and no other relation references it, so no ``CASCADE`` is
needed and no dependent object can be dropped by surprise.

**No backfill, no data migration, no compatibility view** (D-6, BE-11): the
co-existence window wrote XP to ``user_game_profile`` exclusively, so the dropped
rows carry only the legacy ``+bits / -4*bits`` counters, and the DB was empty
anyway (PO clarification #2).

Idempotent via the shared ``migration_already_applied`` / ``schema_migrations``
pattern: the app lifespan re-runs every migration on every boot, and the second
run returns before it reaches the DDL.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_ID = "v005_drop_user_stats"

#: The table v002 created and this migration drops.
#:
#: Declared here because US-34's grep acceptance criterion is meant to prove that
#: NOTHING in the application or the suite still depends on the legacy table — so the
#: tests that verify the DROP ask this module which table it drops instead of
#: repeating a literal of their own, and the only spellings left in the repo are the
#: two migrations that own the table's lifecycle (v002 creates it, this one drops it).
#:
#: The value is a module constant and never input, so interpolating it into the DDL
#: carries no injection surface.
LEGACY_TABLE: Final[str] = "user_stats"


async def migration_already_applied(session: AsyncSession) -> bool:
    """Check if migration v005 has already been applied.

    Args:
        session: AsyncSession for database operations

    Returns:
        True if migration already applied, False otherwise
    """
    try:
        result = await session.execute(
            text("SELECT 1 FROM schema_migrations WHERE version = :v"),
            {"v": MIGRATION_ID},
        )
        return result.scalar() is not None
    except Exception:
        # schema_migrations does not exist yet (first boot)
        return False


async def mark_migration_applied(session: AsyncSession) -> None:
    """Record that migration v005 has been applied.

    Args:
        session: AsyncSession for database operations
    """
    await session.execute(
        text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:v, CURRENT_TIMESTAMP)"
        ),
        {"v": MIGRATION_ID},
    )


async def run_migration(session: AsyncSession) -> None:
    """Apply migration v005: drop the legacy statistics table.

    Depends on nothing — the table it removes is a leaf, so a database that never
    ran v002 reaches the same end state as one that did (the ``IF EXISTS`` guard).

    Args:
        session: AsyncSession for database operations

    Raises:
        Exception: If migration fails, the transaction is rolled back by the
            caller — nothing is committed half-way.
    """
    if await migration_already_applied(session):
        return

    await session.execute(text(f"DROP TABLE IF EXISTS {LEGACY_TABLE}"))

    await mark_migration_applied(session)
    await session.commit()
