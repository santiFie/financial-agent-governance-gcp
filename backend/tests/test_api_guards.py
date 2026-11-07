"""Offline tests for /api/chat error propagation and /api/approve transaction validation."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.mock_banking import mock_banking_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_queue():
    mock_banking_db.pending_transfers.clear()
    yield
    mock_banking_db.pending_transfers.clear()


def _approve(session_id, tx_id):
    return client.post("/api/approve", json={
        "session_id": session_id, "transaction_id": tx_id, "decision": "APPROVED", "admin_user": "oficial",
    })


def test_chat_returns_500_when_graph_raises():
    with patch("app.main.financial_graph") as graph:
        graph.stream.side_effect = RuntimeError("boom")
        resp = client.post("/api/chat", json={"message": "hola", "session_id": "s_err"})
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


@pytest.mark.parametrize("tx_session, request_tx", [
    ("s_other", "tx_pending_ok"),    # staged in another session
    ("s_paused", "tx_pending_nope"),  # unknown transaction id
])
def test_approve_rejects_transaction_not_pending_in_session(tx_session, request_tx):
    staged = mock_banking_db.stage_transfer(tx_session, "user_x", 100.0, "acc_001", "[CBU_BANCARIO_1]")
    mock_banking_db.pending_transfers["tx_pending_ok"] = {**staged, "transaction_id": "tx_pending_ok"}
    with patch("app.main.financial_graph") as graph:
        graph.get_state.return_value = MagicMock(next=("pep_node",))
        resp = _approve("s_paused", request_tx)
    assert resp.status_code == 404
    graph.invoke.assert_not_called()


def test_approve_reads_balance_of_staged_transfer_user():
    staged = mock_banking_db.stage_transfer("s_paused", "user_x", 100.0, "acc_001", "[CBU_BANCARIO_1]")
    with patch("app.main.financial_graph") as graph, patch("app.main.mcp_action_client") as mcp:
        graph.get_state.return_value = MagicMock(next=("pep_node",))
        graph.invoke.return_value = {"final_response": "ok"}
        mcp.read_account_balances.return_value = [{"balance": 123.0}]
        resp = _approve("s_paused", staged["transaction_id"])
    assert resp.status_code == 200
    mcp.read_account_balances.assert_called_once_with("user_x")
    assert resp.json()["final_balance"] == 123.0
