"""
Tests for FastAPI REST Endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "security_mode" in data


def test_chat_balance_query():
    payload = {
        "message": "Hola, ¿podrías indicarme el saldo de mis cuentas?",
        "session_id": "test_api_sess_01"
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert "Saldos Disponibles" in data["response"]
    assert data["security_audit"]["defense_action"] == "PASSED"


def test_chat_pii_sanitization():
    payload = {
        "message": "Mi número de tarjeta es 4545-9876-1234-5678, ¿qué saldo tengo?",
        "session_id": "test_api_sess_02"
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["security_audit"]["defense_action"] == "DEIDENTIFIED"
    assert len(data["security_audit"]["pii_detected"]) >= 1
    # Check that raw card number is NOT present in the sanitized prompt that went to the agents
    assert "4545-9876-1234-5678" not in data["security_audit"]["sanitized_input"]


def test_chat_transfer_hitl_and_approve_cycle():
    session_id = "test_api_transfer_cycle"

    # 1. Request transfer
    payload = {
        "message": "Quiero transferir $15000 al CBU 0170099900000012345678",
        "session_id": session_id
    }
    chat_resp = client.post("/api/chat", json=payload)
    assert chat_resp.status_code == 200
    data = chat_resp.json()
    assert data["status"] == "PENDING_APPROVAL"
    assert data["pending_transfer"] is not None
    tx_id = data["pending_transfer"]["transaction_id"]

    # 2. Check pending list
    pending_resp = client.get("/api/pending-transfers")
    assert pending_resp.status_code == 200
    pending_items = pending_resp.json()
    assert any(item["transaction_id"] == tx_id for item in pending_items)

    # 3. Approve transfer via /api/approve
    approve_payload = {
        "session_id": session_id,
        "transaction_id": tx_id,
        "decision": "APPROVED",
        "admin_user": "compliance_tester"
    }
    approve_resp = client.post("/api/approve", json=approve_payload)
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["status"] == "EXECUTED"
    assert "Transferencia Ejecutada con Éxito" in approve_data["message"]
