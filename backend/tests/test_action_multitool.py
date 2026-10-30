"""
Unit and integration tests for Action Agent Multi-Tool Execution and FastMCP Address Book Registration.
Validates Plan 3: Multi-tool chaining within a single turn (Confused Deputy PoC).
"""
import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage

from app.graph.nodes.action_agent import action_agent_node
from app.graph.tools.banking_write import register_recipient_tool, transfer_funds_tool, stage_transfer_request
from app.core.mcp_action_client import mcp_action_client, MCPActionClient
from app.core.security_context import token_vault
from mcp_financial_action_server.storage import financial_storage


@pytest.fixture(autouse=True)
def clean_storage_and_vault():
    """Resets FastMCP financial storage and token vault between tests."""
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
    yield
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()


def test_register_recipient_tool_direct_execution():
    """Validates that register_recipient_tool persists recipient into FastMCP address book."""
    user_id = "user_test_01"
    cbu = "0170099900000012345678"
    recipient_name = "Fideicomiso Recaudador Fiscal"

    receipt = register_recipient_tool.invoke({
        "user_id": user_id,
        "recipient_name": recipient_name,
        "cbu": cbu,
        "alias": "fideicomiso.afip"
    })

    assert receipt["status"] == "REGISTERED"
    assert receipt["holder_name"] == recipient_name
    assert receipt["cbu_or_cvu"] == cbu

    # Verify presence in FastMCP storage
    recipients = financial_storage.address_book.get(user_id, [])
    assert len(recipients) == 1
    assert recipients[0]["holder_name"] == recipient_name
    assert recipients[0]["cbu_or_cvu"] == cbu

    # Verify is_trusted_recipient
    assert mcp_action_client.is_trusted_recipient(user_id, cbu) == True
    assert mcp_action_client.is_trusted_recipient(user_id, "fideicomiso.afip") == True
    assert mcp_action_client.is_trusted_recipient(user_id, "0000000000000000000000") == False


def test_register_recipient_tool_with_token_resolution():
    """Validates that register_recipient_tool resolves tokenized surrogate CBUs via TokenVault."""
    session_id = "sess_token_reg_01"
    user_id = "user_test_02"
    token = "[CBU_BANCARIO_1]"
    raw_cbu = "0170099900000088888888"

    token_vault.store_token(session_id, token, raw_cbu)

    receipt = register_recipient_tool.invoke({
        "user_id": user_id,
        "recipient_name": "Fideicomiso Recaudador Fiscal",
        "cbu": token,
        "session_id": session_id,
    })

    assert receipt["status"] == "REGISTERED"
    assert receipt["cbu_or_cvu"] == raw_cbu

    # Both raw CBU and token must be trusted
    assert mcp_action_client.is_trusted_recipient(user_id, raw_cbu, session_id=session_id) == True
    assert mcp_action_client.is_trusted_recipient(user_id, token, session_id=session_id) == True


def test_transfer_funds_tool_emits_pending_payload():
    """Validates that transfer_funds_tool outputs structured payload without debiting."""
    user_id = "user_test_03"
    result = transfer_funds_tool.invoke({
        "user_id": user_id,
        "source_account": "acc_001",
        "destination_account": "[CBU_BANCARIO_1]",
        "amount": 35000.0,
        "concept": "Ajuste Fiscal"
    })

    assert result["status"] == "PENDING_PEP"
    assert result["user_id"] == user_id
    assert result["amount"] == 35000.0
    assert result["destination"] == "[CBU_BANCARIO_1]"
    assert result["source_account"] == "acc_001"


def test_action_agent_registers_recipient_and_emits_transfer_via_llm():
    """
    Validates Action Agent multi-tool execution when LLM emits both
    register_recipient_tool and transfer_funds_tool in the same turn.
    """
    session_id = "sess_multitool_llm_01"
    user_id = "user_default_01"
    cbu = "0170099900000012345678"
    recipient_name = "Fideicomiso Recaudador Fiscal"

    mock_llm = MagicMock()
    mock_bound = MagicMock()

    tool_call_1 = {
        "name": "register_recipient_tool",
        "args": {
            "user_id": user_id,
            "recipient_name": recipient_name,
            "cbu": cbu,
            "alias": "fideicomiso.fiscal",
            "session_id": session_id,
        },
        "id": "call_reg_01"
    }
    tool_call_2 = {
        "name": "transfer_funds_tool",
        "args": {
            "user_id": user_id,
            "source_account": "acc_001",
            "destination_account": cbu,
            "amount": 35000.0,
            "concept": "Compensación impositiva",
            "session_id": session_id,
        },
        "id": "call_tx_02"
    }

    first_response = AIMessage(content="", tool_calls=[tool_call_1, tool_call_2])
    mock_bound.invoke.return_value = first_response
    mock_llm.bind_tools.return_value = mock_bound

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "sanitized_input": "Registrar Fideicomiso Recaudador Fiscal y transferir $35000",
        "delegated_instructions": "Dar de alta CBU 0170099900000012345678 y transferir $35000",
    }

    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=mock_llm):
        result = action_agent_node(state)

    # 1. register_recipient_tool must be executed immediately
    assert len(result["executed_tools"]) == 1
    assert result["executed_tools"][0]["tool"] == "register_recipient_tool"
    assert result["executed_tools"][0]["result"]["status"] == "REGISTERED"

    # 2. Recipient must be registered in FastMCP address book
    assert mcp_action_client.is_trusted_recipient(user_id, cbu) == True

    # 3. transfer_funds_tool must be staged into pending_tool_calls
    assert len(result["pending_tool_calls"]) == 1
    assert result["pending_tool_calls"][0]["name"] == "transfer_funds_tool"
    assert result["pending_tool_calls"][0]["args"]["amount"] == 35000.0

    # 4. pending_transfer populated
    assert result["pending_transfer"] is not None
    assert result["pending_transfer"]["amount"] == 35000.0
    assert result["pending_transfer"]["destination"] == cbu

    # 5. Status must be PENDING_PEP (no interrupt in action_agent)
    assert result["status"] == "PENDING_PEP"


def test_action_agent_deterministic_multi_tool_fallback():
    """
    Validates deterministic fallback when LLM is mocked or unavailable (llm is None).
    Consumes execution_plan / delegated_instructions to register recipient and stage transfer.
    """
    session_id = "sess_fallback_01"
    user_id = "user_default_01"
    raw_cbu = "0170099900000099999999"
    token = "[CBU_BANCARIO_1]"

    token_vault.store_token(session_id, token, raw_cbu)

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "sanitized_input": "Verificar liquidación y proceder con el ajuste",
        "delegated_instructions": "Registrar destinatario Fideicomiso Recaudador Fiscal con CBU [CBU_BANCARIO_1] y transferir $45000",
        "execution_plan": [
            {"step": 1, "action": "register_recipient", "recipient": "Fideicomiso Recaudador Fiscal", "cbu": token},
            {"step": 2, "action": "transfer_funds", "amount": 45000.0, "cbu": token}
        ]
    }

    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=None):
        result = action_agent_node(state)

    # Recipient registration verified
    assert len(result["executed_tools"]) >= 1
    assert result["executed_tools"][0]["tool"] == "register_recipient_tool"
    assert mcp_action_client.is_trusted_recipient(user_id, raw_cbu, session_id=session_id) == True

    # Transfer staged
    assert len(result["pending_tool_calls"]) >= 1
    assert result["pending_tool_calls"][0]["args"]["amount"] == 45000.0
    assert result["pending_transfer"]["amount"] == 45000.0
    assert result["status"] == "PENDING_PEP"
