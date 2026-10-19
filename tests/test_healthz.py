"""Tests for health check endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app


@pytest.fixture
async def client():
    """Create test client for FastAPI app.

    The app's lifespan context manager handles migration automatically.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_healthz_endpoint(client):
    """Test health check endpoint returns ok status."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


async def test_healthz_database_check(client):
    """Test health check verifies database connectivity."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert "database" in data
    assert data["database"] in ["connected", "disconnected"]
