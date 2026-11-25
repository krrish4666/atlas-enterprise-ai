"""tests/test_api.py — End-to-end API contract, rate-limiting, role enforcement, and error tests."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.api.rate_limit import chat_rate_limiter
from app.config import settings
from app.db.models import AuditLog
from app.db.session import get_db
from app.main import app


def test_health_returns_ok_when_services_up(client: TestClient) -> None:
    """GET /api/health returns status='ok' when all services are healthy."""
    with (
        patch("app.api.routes_ops.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.api.routes_ops.check_rustfs") as mock_rustfs,
        patch("app.api.routes_ops.check_ollama", new_callable=AsyncMock) as mock_ollama,
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


def test_root_health_alias_returns_ok(client: TestClient) -> None:
    """GET /health returns 200 OK for Docker/load-balancer healthchecks."""
    with (
        patch("app.api.routes_ops.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.api.routes_ops.check_rustfs") as mock_rustfs,
        patch("app.api.routes_ops.check_ollama", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_pg.return_value = True
        mock_rustfs.return_value = True
        mock_ollama.return_value = True

        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_returns_degraded_when_services_down(client: TestClient) -> None:
    """GET /api/health returns status='degraded' (never 500) when any service fails."""
    with (
        patch("app.api.routes_ops.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.api.routes_ops.check_rustfs") as mock_rustfs,
        patch("app.api.routes_ops.check_ollama", new_callable=AsyncMock) as mock_ollama,
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
        patch("app.api.routes_ops.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("app.api.routes_ops.check_rustfs") as mock_rustfs,
        patch("app.api.routes_ops.check_ollama", new_callable=AsyncMock) as mock_ollama,
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


def test_chat_rate_limit_blocks_excess_requests_per_ip(client: TestClient) -> None:
    """POST /api/chat returns 429 with Retry-After once CHAT_RATE_LIMIT_PER_MINUTE is exceeded."""
    mock_session = AsyncMock()
    mock_state = {
        "session_id": "rate-test",
        "final_response": "Ok",
        "assistant_message_id": str(uuid.uuid4()),
        "citations": [],
        "pii_redacted": False,
        "hitl_approval_id": None,
        "trace_id": "lf-rate",
        "status": "completed",
    }

    app.dependency_overrides[get_db] = lambda: mock_session
    orig_limit = settings.chat_rate_limit_per_minute
    settings.chat_rate_limit_per_minute = 2
    chat_rate_limiter.reset()

    try:
        with patch("app.api.routes_chat.run_rag_pipeline", new_callable=AsyncMock) as mock_pipe:
            mock_pipe.return_value = mock_state

            # First 2 requests from IP 203.0.113.10 succeed
            for _ in range(2):
                resp = client.post(
                    "/api/chat",
                    json={"session_id": "rate-test", "query": "Hello?"},
                    headers={"X-Forwarded-For": "203.0.113.10"},
                )
                assert resp.status_code == 200

            # 3rd request from same IP is blocked with 429
            blocked = client.post(
                "/api/chat",
                json={"session_id": "rate-test", "query": "Hello again?"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers
            assert "Rate limit exceeded" in blocked.json()["detail"]

            # Different client IP has an independent bucket
            other_ip = client.post(
                "/api/chat",
                json={"session_id": "rate-test", "query": "Hello from another IP?"},
                headers={"X-Forwarded-For": "198.51.100.77"},
            )
            assert other_ip.status_code == 200
    finally:
        settings.chat_rate_limit_per_minute = orig_limit
        chat_rate_limiter.reset()
        app.dependency_overrides.pop(get_db, None)


def test_approvals_decision_requires_reviewer_or_admin_role(client: TestClient) -> None:
    """POST /api/approvals/{id}/decision returns 403 for employee or missing role."""
    mock_session = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_session
    approval_id = uuid.uuid4()

    try:
        resp_missing = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve"},
        )
        assert resp_missing.status_code == 403

        resp_employee = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers={"X-User-Role": "employee"},
        )
        assert resp_employee.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_approvals_decision_invalid_decision_and_not_found(client: TestClient) -> None:
    """POST /api/approvals/{id}/decision returns 400 on bad decision and 404 when absent."""
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[get_db] = lambda: mock_session
    approval_id = uuid.uuid4()

    try:
        bad_decision = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "force_override"},
            headers={"X-User-Role": "reviewer"},
        )
        assert bad_decision.status_code == 400

        not_found = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers={"X-User-Role": "admin"},
        )
        assert not_found.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_feedback_validation_and_not_found(client: TestClient) -> None:
    """POST /api/chat/feedback returns 400 for invalid rating and 404 for unknown message."""
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        bad_rating = client.post(
            "/api/chat/feedback",
            json={"message_id": str(uuid.uuid4()), "rating": 5},
        )
        assert bad_rating.status_code == 400

        not_found = client.post(
            "/api/chat/feedback",
            json={"message_id": str(uuid.uuid4()), "rating": 1},
        )
        assert not_found.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_list_audit_logs_with_filters(client: TestClient) -> None:
    """GET /api/audit-logs supports event_type, session_id, and limit query params."""
    mock_session = AsyncMock()
    audit_row = AuditLog(
        id=uuid.uuid4(),
        event_type="chat_completed",
        actor_role="employee",
        session_id="sess-ops",
        resource_id=str(uuid.uuid4()),
        details={"citations_count": 2},
        created_at=datetime.now(UTC),
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [audit_row]
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        resp = client.get("/api/audit-logs?limit=10&event_type=chat_completed&session_id=sess-ops")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["audit_logs"]) == 1
        assert body["audit_logs"][0]["event_type"] == "chat_completed"
        assert body["audit_logs"][0]["session_id"] == "sess-ops"
    finally:
        app.dependency_overrides.pop(get_db, None)
