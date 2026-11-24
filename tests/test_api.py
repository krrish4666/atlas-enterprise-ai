"""tests/test_api.py — Phase 0 smoke tests: health endpoint contract."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_health_returns_ok_when_services_up(client: TestClient) -> None:
    """GET /api/health returns status='ok' when all services are healthy."""
    with (
        patch("app.main.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.main.check_rustfs") as mock_rustfs,
        patch("app.main.check_ollama", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_pg.return_value = True
        mock_rustfs.return_value = True
        mock_ollama.return_value = True

        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["services"]["postgres"] == "up"
    assert body["services"]["rustfs"] == "up"
    assert body["services"]["ollama"] == "up"
    assert "langfuse_enabled" in body["services"]


def test_health_returns_degraded_when_services_down(client: TestClient) -> None:
    """GET /api/health returns status='degraded' (never 500) when any service fails."""
    with (
        patch("app.main.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.main.check_rustfs") as mock_rustfs,
        patch("app.main.check_ollama", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_pg.return_value = False
        mock_rustfs.return_value = False
        mock_ollama.return_value = False

        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["services"]["postgres"] == "down"
    assert body["services"]["rustfs"] == "down"
    assert body["services"]["ollama"] == "down"


def test_health_response_schema_types(client: TestClient) -> None:
    """Validate JSON response schema types."""
    with (
        patch("app.main.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.main.check_rustfs") as mock_rustfs,
        patch("app.main.check_ollama", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_pg.return_value = True
        mock_rustfs.return_value = True
        mock_ollama.return_value = False

        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["status"], str)
    assert isinstance(body["services"], dict)
    assert body["status"] == "degraded"
    assert body["services"]["postgres"] == "up"
    assert body["services"]["ollama"] == "down"
