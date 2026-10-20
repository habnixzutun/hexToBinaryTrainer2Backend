"""Smoke test: verifies test infrastructure works.

This test must pass after Task 1 is complete.
Before Task 1: fails because src.config.Settings() crashes on import without DB_*
env vars.
After Task 1: passes because conftest.py sets env vars before any src import.
US-05: Settings now uses factory pattern (get_settings()) instead of module-level
instantiation.
"""


def test_import_config():
    """Verify we can import src.config without ValidationError."""
    from src.config import Settings, get_settings

    # If we get here, the import succeeded
    assert Settings is not None
    assert get_settings is not None


def test_import_main():
    """Verify we can import src.main (which imports config)."""
    from src import main

    assert main is not None


def test_settings_has_database_urls():
    """Verify Settings has async_database_url and sync_database_url properties."""
    from src.config import Settings

    settings = Settings()
    assert hasattr(settings, "async_database_url")
    assert hasattr(settings, "sync_database_url")
    assert "postgresql+asyncpg://" in settings.async_database_url
    assert "postgresql://" in settings.sync_database_url
