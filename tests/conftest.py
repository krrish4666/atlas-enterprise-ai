"""tests/conftest.py — Shared pytest fixtures for Atlas Enterprise AI."""

import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.rate_limit import chat_rate_limiter  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset in-memory IP rate limiter state before and after each test."""
    chat_rate_limiter.reset()
    yield
    chat_rate_limiter.reset()


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Synchronous test client for FastAPI (no live services required)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
