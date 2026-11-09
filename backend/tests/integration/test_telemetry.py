"""
Unit and integration tests for Telemetry, Tracing and Observability.
Verifies config generation, metadata enrichment, trace ID propagation and health endpoints.
"""

import uuid

from fastapi.testclient import TestClient

from app.core.telemetry import build_trace_config, get_telemetry_status
from app.main import app
from tests.live_llm import requires_live_llm

client = TestClient(app)


def test_build_trace_config_structure():
    """Validates that build_trace_config formats the config dictionary properly."""
    session_id = "sess_telemetry_001"
    user_id = "user_auditor"
    endpoint = "/api/chat"

    config, trace_id = build_trace_config(
        session_id=session_id,
        user_id=user_id,
        endpoint=endpoint,
        run_name="custom_run_name",
        extra_metadata={"custom_key": "custom_val"},
        extra_tags=["custom-tag"],
    )

    # Validate trace_id is valid UUID
    assert uuid.UUID(trace_id)

    # Validate config keys
    assert config["configurable"]["thread_id"] == session_id
    assert config["run_name"] == "custom_run_name"
    assert config["run_id"] == uuid.UUID(trace_id)

    # Validate tags
    assert "financial-assistant" in config["tags"]
    assert "custom-tag" in config["tags"]

    # Validate metadata
    metadata = config["metadata"]
    assert metadata["session_id"] == session_id
    assert metadata["user_id"] == user_id
    assert metadata["endpoint"] == endpoint
    assert metadata["trace_id"] == trace_id
    assert metadata["custom_key"] == "custom_val"


def test_telemetry_status():
    """Validates telemetry status structure."""
    status = get_telemetry_status()
    assert "tracing_enabled" in status
    assert "provider" in status
    assert status["provider"] == "LangSmith"
    assert "project" in status


def test_health_endpoint_exposes_telemetry():
    """Validates /api/health returns telemetry information."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "telemetry" in data
    assert "tracing_enabled" in data["telemetry"]
    assert data["telemetry"]["provider"] == "LangSmith"


@requires_live_llm
def test_chat_endpoint_populates_trace_id_in_audit():
    """Validates /api/chat returns trace_id in SecurityMetadata."""
    response = client.post(
        "/api/chat",
        json={"message": "Hola, ¿cómo estás?", "session_id": "test_telemetry_session", "user_id": "user_test_trace"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "security_audit" in data
    trace_id = data["security_audit"].get("trace_id")
    assert trace_id is not None
    assert len(trace_id) > 10
    assert uuid.UUID(trace_id)
