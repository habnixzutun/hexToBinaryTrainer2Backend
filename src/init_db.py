# src/init_db.py
import asyncio
import logging
import time

import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings

# Import Base and all models so SQLAlchemy registers them for create_all
from src.database.engine import Base

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def wait_for_db(timeout_seconds: int = None):
    """Wait for database to become available.

    Args:
        timeout_seconds: Maximum time to wait in seconds.
            Defaults to DB_WAIT_TIMEOUT env var or 60s.

    Raises:
        TimeoutError: If database doesn't become available within timeout.
    """
    settings = get_settings()

    # Convert asyncpg URL to sync URL for initial connection test
    sync_url = settings.async_database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    # Get timeout from parameter, env var, or default
    if timeout_seconds is None:
        import os

        timeout_seconds = int(os.getenv("DB_WAIT_TIMEOUT", "60"))

    start_time = time.time()
    attempt = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            logger.error(
                f"Datenbank nicht erreichbar nach {timeout_seconds}s - Timeout!"
            )
            raise TimeoutError(
                f"Database connection timed out after {timeout_seconds} seconds. "
                f"Ensure PostgreSQL is running and accessible at "
                f"{settings.db_host}:{settings.db_port}"
            )

        try:
            conn = await asyncpg.connect(sync_url)
            await conn.close()
            logger.info(
                f"Datenbank erreichbar nach {attempt + 1} Versuchen ({elapsed:.1f}s)"
            )
            break
        except Exception as e:
            attempt += 1
            logger.warning(
                f"Warte auf DB... (Versuch {attempt}, {elapsed:.1f}s vergangen): {e}"
            )
            await asyncio.sleep(2)


async def init_models():
    """Initialize database models and create tables."""
    settings = get_settings()

    await wait_for_db()

    engine = create_async_engine(settings.async_database_url, echo=True)

    async with engine.begin() as conn:
        logger.info("Lösche alte Tabellen (falls vorhanden)...")
        # Optional: Drop all tables for completely clean DB init
        # WARNING: This deletes all data!
        # await conn.run_sync(Base.metadata.drop_all)

        logger.info("Erstelle Tabellen...")
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Datenbank erfolgreich initialisiert!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_models())
