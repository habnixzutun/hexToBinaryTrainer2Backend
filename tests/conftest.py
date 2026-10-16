"""Pytest configuration and fixtures.

CRITICAL: This file sets environment variables BEFORE any src imports.
src.config.Settings() is instantiated at module level and requires DB_* vars.
Without this conftest, imports crash with ValidationError.
"""

import os

# Set test environment variables before any src imports
# These are test-only values, not production secrets
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PW", "test_password")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")

import pytest


@pytest.fixture(scope="session")
def test_db_config():
    """Test database configuration."""
    return {
        "db_name": "test_db",
        "db_user": "test_user",
        "db_pw": "test_password",
        "db_host": "localhost",
        "db_port": 5432,
    }


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for anyio tests."""
    return "asyncio"
