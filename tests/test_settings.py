"""Tests for Settings factory function (US-05).

Verifies that Settings uses factory pattern with lru_cache instead of module-level instantiation.
"""

import pytest
from unittest.mock import patch
import os

from src.config import get_settings, Settings


class TestGetSettings:
    """Test get_settings factory function."""

    def test_get_settings_returns_settings_instance(self):
        """Verify get_settings returns a Settings instance."""
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_caches_result(self):
        """Verify get_settings returns cached instance on multiple calls."""
        settings1 = get_settings()
        settings2 = get_settings()

        # Should be the same object (cached)
        assert settings1 is settings2

    def test_get_settings_uses_env_variables(self):
        """Verify get_settings reads from environment variables."""
        with patch.dict(os.environ, {"DB_HOST": "test-host", "DB_PORT": "9999"}, clear=True):
            # Clear cache to force re-read
            get_settings.cache_clear()
            settings = get_settings()

            assert settings.db_host == "test-host"
            assert settings.db_port == 9999

            # Clean up cache
            get_settings.cache_clear()

    def test_settings_has_default_values(self):
        """Verify Settings class has sensible defaults."""
        # In test environment, conftest.py sets DB_NAME=test_db and DB_PORT=5434
        # This test verifies the Settings class structure and defaults work
        settings = Settings()
        assert settings.db_host == "localhost"  # Default or from conftest
        assert settings.db_port == 5434  # From conftest (test isolation)
        assert settings.db_name == "test_db"  # From conftest (test isolation)

    def test_get_settings_async_url_construction(self):
        """Verify get_settings constructs async_database_url correctly."""
        with patch.dict(os.environ, {
            "DB_HOST": "myhost",
            "DB_PORT": "5433",
            "DB_NAME": "testdb",
            "DB_USER": "testuser",
            "DB_PW": "testpass"
        }, clear=True):
            get_settings.cache_clear()
            settings = get_settings()

            expected = "postgresql+asyncpg://testuser:testpass@myhost:5433/testdb"
            assert settings.async_database_url == expected

            # Clean up
            get_settings.cache_clear()

    def test_get_settings_sync_url_construction(self):
        """Verify get_settings constructs sync_database_url correctly."""
        with patch.dict(os.environ, {
            "DB_HOST": "myhost",
            "DB_PORT": "5433",
            "DB_NAME": "testdb",
            "DB_USER": "testuser",
            "DB_PW": "testpass"
        }, clear=True):
            get_settings.cache_clear()
            settings = get_settings()

            expected = "postgresql://testuser:testpass@myhost:5433/testdb"
            assert settings.sync_database_url == expected

            # Clean up
            get_settings.cache_clear()

    def test_settings_not_instantiated_at_module_level(self):
        """Verify no module-level 'settings = Settings()' exists."""
        import src.config as config_module

        # Check that 'settings' attribute doesn't exist or is not a Settings instance
        assert not hasattr(config_module, 'settings') or not isinstance(
            getattr(config_module, 'settings', None), Settings
        )
