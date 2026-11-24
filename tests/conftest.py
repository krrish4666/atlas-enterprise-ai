"""tests/conftest.py — Shared pytest fixtures for Atlas Enterprise AI."""

import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Synchronous test client for FastAPI (no live services required)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
