"""
Unit and integration tests for Policy Enforcement Point (PEP) Node and PDP Integration.
Validates Plan 4: Fast-Path execution for trusted recipients < $50.000 ARS and HITL interrupt governance.
"""
import pytest
from unittest.mock import MagicMock, patch
import httpx

from app.config import settings
from app.graph.nodes.pep import pep_node
from app.db.mock_banking import mock_banking_db
from app.core.mcp_action_client import mcp_action_client
from app.core.security_context import token_vault
from mcp_financial_action_server.storage import financial_storage


def _pdp_response(decision, status_code=200):
    """Builds a mocked OPA data-API response: {"result": <decision>}."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"result": decision}
    return resp


@pytest.fixture(autouse=True)
def clean_state():
    """Resets core banking accounts and FastMCP address book before each test."""
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
    # Reset mock banking accounts
    mock_banking_db.pending_transfers.clear()
    yield
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
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

    mock_resp = _pdp_response({
        "allow": True,
        "require_hitl": False,
        "reason": "Fast-Path aprobado: destinatario registrado y monto inferior a $50.000 ARS"
    })

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

    mock_resp = _pdp_response({
        "allow": True,
        "require_hitl": True,
        "reason": "Monto supera el umbral de Fast-Path ($50.000 ARS)"
    })

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

    mock_resp = _pdp_response({
        "allow": True,
        "require_hitl": True,
        "reason": "Destinatario no registrado en agenda de confianza"
    })

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

    hitl_resp = _pdp_response({"allow": True, "require_hitl": True, "reason": "HITL requerido"})

    with patch("httpx.Client.post", return_value=hitl_resp), \
         patch("app.graph.nodes.pep.interrupt", return_value={"decision": "REJECTED", "admin_user": "auditor", "reason": "Patrón anómalo"}) as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_called_once()
        assert result["approval_status"] == "REJECTED"
        assert "Transferencia Rechazada" in result["final_response"]
        # Balance untouched
        assert financial_storage.get_account("acc_001").balance == initial_balance


def _fallback_state(session_id, cbu, amount):
    return {
        "session_id": session_id,
        "user_id": "user_default_01",
        "pending_tool_calls": [{"name": "transfer_funds_tool", "args": {
            "source_account": "acc_001", "destination_account": cbu, "amount": amount}}],
    }


@pytest.mark.parametrize("pdp_failure", [
    {"side_effect": httpx.ConnectTimeout("PDP connection timed out")},
    {"side_effect": httpx.ConnectError("PDP offline")},
    {"return_value": _pdp_response({"allow": True}, status_code=500)},
    {"return_value": _pdp_response({"allow": True}, status_code=404)},
    {"return_value": MagicMock(status_code=200, json=MagicMock(return_value={}))},
    {"return_value": MagicMock(status_code=200, json=MagicMock(return_value={"result": {"reason": "sin allow"}}))},
])
def test_pep_fails_closed_when_pdp_unavailable(pdp_failure, caplog):
    """
    PDP unreachable, non-200 or without a usable decision -> fallback mode 'deny':
    the step ends FAILED (not BLOCKED, not flagged as threat), no debit and no interrupt, with a warning logged.
    """
    cbu = "0170099900000012345678"
    mcp_action_client.register_account(
        user_id="user_default_01", cbu_or_cvu=cbu, holder_name="Fideicomiso", bank_name="Banco Nación"
    )
    initial_balance = financial_storage.get_account("acc_001").balance

    with patch("httpx.Client.post", **pdp_failure), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt, \
         patch.object(settings, "pdp_fallback_mode", "deny"), \
         caplog.at_level("WARNING", logger="app.graph.nodes.pep"):
        result = pep_node(_fallback_state("sess_fallback_deny", cbu, 20000.0))

    mock_interrupt.assert_not_called()
    assert result["status"] == "FAILED"
    assert not result.get("is_threat")
    assert result["pending_tool_calls"] == []
    assert "PDP no disponible" in result["final_response"]
    assert financial_storage.get_account("acc_001").balance == initial_balance
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_pep_fallback_mode_hitl_degrades_to_human_approval():
    """With pdp_fallback_mode='hitl' an unavailable PDP routes the transfer to HITL instead of failing."""
    cbu = "0170099900000012345678"
    mcp_action_client.register_account(
        user_id="user_default_01", cbu_or_cvu=cbu, holder_name="Fideicomiso", bank_name="Banco Nación"
    )
    initial_balance = financial_storage.get_account("acc_001").balance

    with patch("httpx.Client.post", side_effect=httpx.ConnectError("PDP offline")), \
         patch.object(settings, "pdp_fallback_mode", "hitl"), \
         patch("app.graph.nodes.pep.interrupt", return_value={"decision": "REJECTED", "admin_user": "auditor"}) as mock_interrupt:
        result = pep_node(_fallback_state("sess_fallback_hitl", cbu, 20000.0))

    mock_interrupt.assert_called_once()
    assert result["approval_status"] == "REJECTED"
    assert financial_storage.get_account("acc_001").balance == initial_balance


def test_pep_sends_opa_input_envelope_to_configured_url():
    """PEP posts {"input": {...}} to settings.pdp_url, as the OPA data API expects."""
    cbu = "0170099900000012345678"
    mcp_action_client.register_account(
        user_id="user_default_01", cbu_or_cvu=cbu, holder_name="Fideicomiso", bank_name="Banco Nación"
    )
    ok = _pdp_response({"allow": True, "require_hitl": False, "reason": "Fast-Path"})

    with patch("httpx.Client.post", return_value=ok) as mock_post, patch("app.graph.nodes.pep.interrupt"):
        pep_node(_fallback_state("sess_envelope", cbu, 1000.0))

    args, kwargs = mock_post.call_args
    assert args[0] == settings.pdp_url
    assert kwargs["json"] == {"input": {"user_id": "user_default_01", "tool_name": "transfer_funds_tool", "amount": 1000.0,
                                 "is_trusted_recipient": True}}


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

    mock_resp = _pdp_response({
        "allow": False,
        "require_hitl": False,
        "reason": "Destinatario bloqueado en lista negra por prevención de lavado"
    })

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
        return _pdp_response({"allow": True, "require_hitl": False, "reason": "Fast-Path approved"})

    with patch("httpx.Client.post", side_effect=fake_post), \
         patch("app.graph.nodes.pep.interrupt") as mock_interrupt:

        result = pep_node(state)
        mock_interrupt.assert_not_called()
        assert result["status"] == "COMPLETED"
        # Verify real CBU was evaluated and is_trusted was True
        assert captured_payload is not None
        assert captured_payload["input"]["is_trusted_recipient"] is True
        assert captured_payload["input"]["amount"] == amount


def test_pep_reports_failure_when_mcp_rejects_the_transfer():
    """MCP validation errors (e.g. unresolved token as destination) end the step as FAILED without debiting."""
    state = {
        "session_id": "sess_mcp_fail",
        "user_id": "user_default_01",
        "pending_tool_calls": [{"name": "transfer_funds_tool", "args": {
            "source_account": "acc_001", "destination_account": "[CBU_NO_RESUELTO_1]", "amount": 1000.0}}],
    }
    initial_balance = financial_storage.get_account("acc_001").balance
    mock_resp = _pdp_response({"allow": True, "require_hitl": False, "reason": "fast"})

    with patch("httpx.Client.post", return_value=mock_resp), patch("app.graph.nodes.pep.interrupt"):
        result = pep_node(state)

    assert result["status"] == "FAILED"
    assert "No se pudo ejecutar" in result["final_response"]
    assert financial_storage.get_account("acc_001").balance == initial_balance


def _register_state(session_id: str, cbu: str) -> dict:
    return {
        "session_id": session_id,
        "user_id": "user_default_01",
        "execution_plan": [{"step_id": 1, "title": "t", "target_node": "action_agent", "description": "d", "result": None}],
        "pending_tool_calls": [{
            "name": "register_recipient_tool",
            "args": {"user_id": "user_default_01", "recipient_name": "Proveedor", "cbu": cbu},
        }],
    }


def test_pep_sends_registration_to_the_pdp_with_the_tool_name_and_registers_when_allowed():
    cbu = "0170099900000012345678"
    allow = _pdp_response({"allow": True, "require_hitl": False, "reason": "Herramienta sin movimiento de fondos"})

    with patch("httpx.Client.post", return_value=allow) as mock_post, patch("app.graph.nodes.pep.interrupt") as interrupt:
        result = pep_node(_register_state("sess_reg_allow", cbu))

    assert mock_post.call_args.kwargs["json"] == {"input": {"user_id": "user_default_01", "tool_name": "register_recipient_tool"}}
    interrupt.assert_not_called()
    assert mcp_action_client.is_trusted_recipient("user_default_01", cbu)
    assert result["status"] == "COMPLETED"
    assert result["execution_plan"][0]["result"] == result["final_response"]
    assert result["pending_tool_calls"] == []


def test_pep_does_not_register_when_the_pdp_denies():
    cbu = "0170099900000012345678"
    deny = _pdp_response({"allow": False, "require_hitl": False, "reason": "no permitido"})

    with patch("httpx.Client.post", return_value=deny):
        result = pep_node(_register_state("sess_reg_deny", cbu))

    assert not mcp_action_client.is_trusted_recipient("user_default_01", cbu)
    assert result["status"] == "FAILED"
    assert "no permitido" in result["final_response"]


def test_pep_does_not_register_when_the_pdp_is_unavailable():
    cbu = "0170099900000012345678"

    with patch("httpx.Client.post", side_effect=httpx.ConnectError("down")):
        result = pep_node(_register_state("sess_reg_down", cbu))

    assert not mcp_action_client.is_trusted_recipient("user_default_01", cbu)
    assert result["status"] == "FAILED"
