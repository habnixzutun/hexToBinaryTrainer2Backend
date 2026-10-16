"""Smoke test: verifies test infrastructure works.

This test must pass after Task 1 is complete.
Before Task 1: fails because src.config.Settings() crashes on import without DB_* env vars.
After Task 1: passes because conftest.py sets env vars before any src import.
"""


def test_import_config():
    """Verify we can import src.config without ValidationError."""
    # This import triggers Settings() instantiation at module level
    from src.config import Settings

    # If we get here, the import succeeded
    assert Settings is not None


def test_import_main():
    """Verify we can import src.main (which imports config)."""
    from src import main

    assert main is not None


def test_settings_has_database_url():
    """Verify Settings has database_url property."""
    from src.config import Settings

    settings = Settings()
    assert hasattr(settings, "database_url")
    assert "postgresql+asyncpg://" in settings.database_url
