"""tests/conftest.py — Shared pytest fixtures for Atlas Enterprise AI."""

import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.rate_limit import chat_rate_limiter  # noqa: E402
from app.api.routes_ops import clear_s3_health_cache  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset in-memory IP rate limiter and S3 health cache before and after each test."""
    chat_rate_limiter.reset()
    clear_s3_health_cache()
    yield
    chat_rate_limiter.reset()
    clear_s3_health_cache()


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Synchronous test client for FastAPI (no live services required)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
