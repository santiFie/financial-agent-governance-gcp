"""
Unit and integration tests for Policy Enforcement Point (PEP) Node and PDP Integration.
Validates Plan 4: Fast-Path execution for trusted recipients < $50.000 ARS and HITL interrupt governance.
"""
import pytest
from unittest.mock import MagicMock, patch
import httpx

from app.graph.nodes.pep import pep_node
from app.db.mock_banking import mock_banking_db
from app.core.mcp_action_client import mcp_action_client
from app.core.security_context import token_vault
from mcp_financial_action_server.storage import financial_storage


@pytest.fixture(autouse=True)
def clean_state():
    """Resets core banking accounts and FastMCP address book before each test."""
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
    # Reset mock banking accounts
    mock_banking_db.accounts["acc_001"].balance = 485250.75
    mock_banking_db.pending_transfers.clear()
    yield
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
    mock_banking_db.accounts["acc_001"].balance = 485250.75
    mock_banking_db.pending_transfers.clear()


def test_pep_fast_path_when_trusted_and_under_threshold():
    """
    Validates Fast-Path execution:
    When recipient is registered in FastMCP address book and amount < $50.000 ARS,
    PDP grants Fast-Path, funds are debited immediately, and NO interrupt() is called.
    """
    user_id = "user_default_01"
    session_id = "sess_fastpath_01"
    cbu = "0170099900000012345678"
    amount = 35000.0

    # 1. Register recipient in address book
    mcp_action_client.register_account(
        user_id=user_id,
        cbu_or_cvu=cbu,
        holder_name="Fideicomiso Recaudador Fiscal",
        bank_name="Banco Nación"
    )
    assert mcp_action_client.is_trusted_recipient(user_id, cbu) == True

    # 2. Prepare state with pending transfer call
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": amount,
                    "concept": "Ajuste Fiscal 11222"
                }
            }
        ],
        "pending_transfer": {
            "session_id": session_id,
            "user_id": user_id,
            "amount": amount,
            "source_account": "acc_001",
            "destination": cbu,
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "allow": True,
        "require_hitl": False,
        "reason": "Fast-Path aprobado: destinatario registrado y monto inferior a $50.000 ARS"
    }

    initial_balance = financial_storage.get_account("acc_001").balance

    with patch("httpx.Client.post", return_value=mock_resp), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt:

        result = pep_node(state)

        # 3. Assert Fast-Path: interrupt was NOT called
        mock_interrupt.assert_not_called()

        # 4. Assert direct execution and status
        assert result["status"] == "COMPLETED"
        assert result["approval_status"] == "APPROVED"
        assert "Fast-Path PEP" in result["final_response"]
        assert len(result["pending_tool_calls"]) == 0

        # 5. Assert atomic balance debit executed by the MCP action server
        expected_balance = round(initial_balance - amount, 2)
        assert financial_storage.get_account("acc_001").balance == expected_balance


def test_pep_hitl_when_amount_exceeds_threshold():
    """
    Validates HITL pause when amount >= $50.000 ARS even if recipient is trusted.
    Must call interrupt(), stage transfer, and finalize upon resumption.
    """
    user_id = "user_default_01"
    session_id = "sess_hitl_high_amt"
    cbu = "0170099900000012345678"
    amount = 75000.0  # Exceeds $50.000 ARS

    mcp_action_client.register_account(
        user_id=user_id,
        cbu_or_cvu=cbu,
        holder_name="Proveedor Mayorista",
        bank_name="Banco Macro"
    )

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": amount,
                    "concept": "Pago Factura"
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "allow": True,
        "require_hitl": True,
        "reason": "Monto supera el umbral de Fast-Path ($50.000 ARS)"
    }

    initial_balance = financial_storage.get_account("acc_001").balance

    with patch("httpx.Client.post", return_value=mock_resp), \
         patch("app.graph.nodes.pep.interrupt", return_value={"decision": "APPROVED", "admin_user": "oficial_riesgo"}) as mock_interrupt:

        result = pep_node(state)

        # 1. interrupt was called
        mock_interrupt.assert_called_once()
        interrupt_arg = mock_interrupt.call_args[0][0]
        assert interrupt_arg["action"] == "APPROVAL_REQUIRED"
        assert interrupt_arg["amount"] == amount

        # 2. Resumed with approval
        assert result["status"] == "COMPLETED"
        assert result["approval_status"] == "APPROVED"
        assert "Transferencia Ejecutada con Éxito" in result["final_response"]
        assert financial_storage.get_account("acc_001").balance == round(initial_balance - amount, 2)


def test_pep_hitl_when_untrusted_recipient():
    """
    Validates HITL requirement when recipient is NOT in address book,
    even if amount is low ($10.000 < $50.000).
    """
    user_id = "user_default_01"
    session_id = "sess_hitl_untrusted"
    cbu = "0170099900000099999999"  # Not registered
    amount = 10000.0

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": amount,
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "allow": True,
        "require_hitl": True,
        "reason": "Destinatario no registrado en agenda de confianza"
    }

    with patch("httpx.Client.post", return_value=mock_resp), \
         patch("app.graph.nodes.pep.interrupt", return_value={"decision": "APPROVED", "admin_user": "oficial"}) as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_called_once()
        assert result["status"] == "COMPLETED"
        assert result["approval_status"] == "APPROVED"


def test_pep_hitl_rejected_by_human():
    """Validates transfer rejection flow when human operator rejects operation."""
    user_id = "user_default_01"
    session_id = "sess_hitl_rejected"
    cbu = "0170099900000012345678"
    amount = 60000.0

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": amount,
                }
            }
        ]
    }

    initial_balance = financial_storage.get_account("acc_001").balance

    with patch("httpx.Client.post", side_effect=httpx.ConnectError("PDP offline")), \
         patch("app.graph.nodes.pep.interrupt", return_value={"decision": "REJECTED", "admin_user": "auditor", "reason": "Patrón anómalo"}) as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_called_once()
        assert result["approval_status"] == "REJECTED"
        assert "Transferencia Rechazada" in result["final_response"]
        # Balance untouched
        assert financial_storage.get_account("acc_001").balance == initial_balance


def test_pep_resilient_fallback_when_pdp_offline():
    """
    Validates offline resiliency:
    When PDP server cannot be reached, fallback policy evaluates:
    - Trusted recipient & amount < 50k -> Fast-Path granted
    - Untrusted or amount >= 50k -> HITL required
    """
    user_id = "user_default_01"
    session_id = "sess_fallback_fast"
    cbu = "0170099900000012345678"
    amount = 20000.0

    mcp_action_client.register_account(
        user_id=user_id,
        cbu_or_cvu=cbu,
        holder_name="Fideicomiso Recaudador Fiscal",
        bank_name="Banco Nación"
    )

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": amount,
                }
            }
        ]
    }

    # Simulate network failure / connection timeout to PDP
    with patch("httpx.Client.post", side_effect=httpx.ConnectTimeout("PDP connection timed out")), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt:

        result = pep_node(state)

        # Fast-Path granted by offline fallback policy
        mock_interrupt.assert_not_called()
        assert result["status"] == "COMPLETED"
        assert result["approval_status"] == "APPROVED"


def test_pep_blocks_when_pdp_denies():
    """Validates that PEP blocks transaction when PDP returns allow=False."""
    user_id = "user_default_01"
    session_id = "sess_pdp_deny"
    cbu = "0170099900000012345678"

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": cbu,
                    "amount": 10000.0,
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "allow": False,
        "require_hitl": False,
        "reason": "Destinatario bloqueado en lista negra por prevención de lavado"
    }

    with patch("httpx.Client.post", return_value=mock_resp), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_not_called()
        assert result["status"] == "BLOCKED"
        assert result["is_threat"] is True
        assert "Bloqueada por Gobernanza PEP" in result["final_response"]


def test_pep_resolves_surrogate_token_for_trusted_check():
    """Validates that PEP resolves [CBU_BANCARIO_1] via TokenVault before PDP evaluation."""
    user_id = "user_default_01"
    session_id = "sess_pep_token"
    token = "[CBU_BANCARIO_1]"
    real_cbu = "0170099900000077777777"
    amount = 45000.0

    token_vault.store_token(session_id, token, real_cbu)
    mcp_action_client.register_account(
        user_id=user_id,
        cbu_or_cvu=real_cbu,
        holder_name="Fideicomiso Recaudador Fiscal",
        bank_name="Banco Nación"
    )

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "user_id": user_id,
                    "source_account": "acc_001",
                    "destination_account": token,
                    "amount": amount,
                }
            }
        ]
    }

    captured_payload = None

    def fake_post(url, json=None, **kwargs):
        nonlocal captured_payload
        captured_payload = json
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {"allow": True, "require_hitl": False, "reason": "Fast-Path approved"}
        return mock_r

    with patch("httpx.Client.post", side_effect=fake_post), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_not_called()
        assert result["status"] == "COMPLETED"
        # Verify real CBU was evaluated and is_trusted was True
        assert captured_payload is not None
        assert captured_payload["destination"] == real_cbu
        assert captured_payload["is_trusted_recipient"] is True


def test_pep_reports_failure_when_mcp_rejects_the_transfer():
    """MCP validation errors (e.g. unresolved token as destination) end the step as FAILED without debiting."""
    state = {
        "session_id": "sess_mcp_fail",
        "user_id": "user_default_01",
        "pending_tool_calls": [{"name": "transfer_funds_tool", "args": {
            "source_account": "acc_001", "destination_account": "[CBU_NO_RESUELTO_1]", "amount": 1000.0}}],
    }
    initial_balance = financial_storage.get_account("acc_001").balance
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"allow": True, "require_hitl": False, "reason": "fast"}

    with patch("httpx.Client.post", return_value=mock_resp), patch("app.graph.nodes.pep.interrupt"):
        result = pep_node(state)

    assert result["status"] == "FAILED"
    assert "No se pudo ejecutar" in result["final_response"]
    assert financial_storage.get_account("acc_001").balance == initial_balance
