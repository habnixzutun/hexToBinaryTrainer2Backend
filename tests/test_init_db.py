"""Tests for database initialization (US-07).

Verifies wait_for_db timeout, logging, and Settings usage.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.init_db import init_models, wait_for_db


class TestWaitForDb:
    """Test wait_for_db function."""

    @pytest.mark.asyncio
    async def test_wait_for_db_success_on_first_try(self):
        """Verify wait_for_db succeeds when DB is immediately available."""
        with patch(
            'src.init_db.asyncpg.connect', new_callable=AsyncMock
        ) as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value = mock_conn

            await wait_for_db()

            mock_connect.assert_called_once()
            mock_conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_for_db_retries_on_failure(self):
        """Verify wait_for_db retries on PostgresError."""
        with patch(
            'src.init_db.asyncpg.connect', new_callable=AsyncMock
        ) as mock_connect:
            # First two attempts fail, third succeeds
            mock_connect.side_effect = [
                Exception("Connection refused"),
                Exception("Connection refused"),
                AsyncMock()
            ]

            with patch('src.init_db.asyncio.sleep', new_callable=AsyncMock):
                await wait_for_db()

                assert mock_connect.call_count == 3

    @pytest.mark.asyncio
    async def test_wait_for_db_timeout(self):
        """Verify wait_for_db times out after configured duration."""
        with patch(
            'src.init_db.asyncpg.connect', new_callable=AsyncMock
        ) as mock_connect:
            # Always fail
            mock_connect.side_effect = Exception("Connection refused")

            with patch('src.init_db.asyncio.sleep', new_callable=AsyncMock):
                with patch.dict('os.environ', {'DB_WAIT_TIMEOUT': '1'}):
                    with pytest.raises(Exception) as exc_info:
                        await wait_for_db()

                    assert (
                        "timeout" in str(exc_info.value).lower()
                        or "timed out" in str(exc_info.value).lower()
                    )

    @pytest.mark.asyncio
    async def test_wait_for_db_uses_logging(self, caplog):
        """Verify wait_for_db uses logging instead of print."""
        with patch(
            'src.init_db.asyncpg.connect', new_callable=AsyncMock
        ) as mock_connect:
            # Fail once, then succeed
            mock_connect.side_effect = [
                Exception("Connection refused"),
                AsyncMock()
            ]

            with patch('src.init_db.asyncio.sleep', new_callable=AsyncMock):
                with caplog.at_level(logging.INFO):
                    await wait_for_db()

                    # Should log retry attempts
                    assert any(
                        "Warte" in record.message or "Waiting" in record.message
                        for record in caplog.records
                    )


class TestInitModels:
    """Test init_models function."""

    @pytest.mark.asyncio
    async def test_init_models_creates_tables(self):
        """Verify init_models creates database tables."""
        with patch('src.init_db.wait_for_db', new_callable=AsyncMock):
            with patch('src.init_db.create_async_engine') as mock_engine:
                mock_conn = AsyncMock()
                begin_cm = mock_engine.return_value.begin.return_value
                begin_cm.__aenter__.return_value = mock_conn
                mock_engine.return_value.dispose = AsyncMock()

                await init_models()

                mock_conn.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_models_uses_logging(self, caplog):
        """Verify init_models uses logging instead of print."""
        with patch('src.init_db.wait_for_db', new_callable=AsyncMock):
            with patch('src.init_db.create_async_engine') as mock_engine:
                mock_conn = AsyncMock()
                begin_cm = mock_engine.return_value.begin.return_value
                begin_cm.__aenter__.return_value = mock_conn
                mock_engine.return_value.dispose = AsyncMock()

                with caplog.at_level(logging.INFO):
                    await init_models()

                    # Should log table creation
                    assert any(
                        "Erstelle" in record.message
                        or "Creating" in record.message
                        or "initialisiert" in record.message.lower()
                        for record in caplog.records
                    )

    @pytest.mark.asyncio
    async def test_init_models_uses_get_settings(self):
        """Verify init_models uses get_settings() instead of manual env vars."""
        with patch('src.init_db.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.async_database_url = "postgresql+asyncpg://test:test@localhost:5432/test"
            mock_get_settings.return_value = mock_settings

            with patch('src.init_db.wait_for_db', new_callable=AsyncMock):
                with patch('src.init_db.create_async_engine') as mock_engine:
                    mock_conn = AsyncMock()
                    begin_cm = mock_engine.return_value.begin.return_value
                    begin_cm.__aenter__.return_value = mock_conn
                    mock_engine.return_value.dispose = AsyncMock()

                    await init_models()

                    mock_get_settings.assert_called()
                    # Engine should be created with URL from settings
                    mock_engine.assert_called_once_with(
                        "postgresql+asyncpg://test:test@localhost:5432/test",
                        echo=True
                    )
