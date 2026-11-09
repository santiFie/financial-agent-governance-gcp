"""
Unit and integration tests for Action Agent Multi-Tool Execution and FastMCP Address Book Registration.
Validates Plan 3: Multi-tool chaining within a single turn (Confused Deputy PoC).
"""

from typing import List
from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp_financial_action_server.storage import financial_storage
from pydantic import ValidationError

from app.core.mcp_action_client import mcp_action_client
from app.core.security_context import token_vault
from app.graph.nodes.action_agent import action_agent_node
from app.graph.tools.banking_write import execute_register_recipient, register_recipient_tool, transfer_funds_tool


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


def test_register_recipient_tool_only_stages_the_request():
    """The tool does not touch the address book: the PEP executes it once the PDP allows it."""
    staged = register_recipient_tool.invoke(
        {
            "user_id": "user_test_00",
            "recipient_name": "Proveedor",
            "cbu": "0170099900000012345678",
        }
    )

    assert staged["status"] == "PENDING_PEP"
    assert not financial_storage.address_book.get("user_test_00")


def test_execute_register_recipient_persists_into_the_address_book():
    """Validates that the real mutation persists the recipient into FastMCP address book."""
    user_id = "user_test_01"
    cbu = "0170099900000012345678"
    recipient_name = "Fideicomiso Recaudador Fiscal"

    receipt = execute_register_recipient(
        user_id=user_id, recipient_name=recipient_name, cbu=cbu, alias="fideicomiso.afip"
    )

    assert receipt["status"] == "REGISTERED"
    assert receipt["holder_name"] == recipient_name
    assert receipt["cbu_or_cvu"] == cbu

    # Verify presence in FastMCP storage
    recipients = financial_storage.address_book.get(user_id, [])
    assert len(recipients) == 1
    assert recipients[0]["holder_name"] == recipient_name
    assert recipients[0]["cbu_or_cvu"] == cbu

    # Verify is_trusted_recipient
    assert mcp_action_client.is_trusted_recipient(user_id, cbu)
    assert mcp_action_client.is_trusted_recipient(user_id, "fideicomiso.afip")
    assert not mcp_action_client.is_trusted_recipient(user_id, "0000000000000000000000")


def test_execute_register_recipient_with_token_resolution():
    """Validates that the registration resolves tokenized surrogate CBUs via TokenVault."""
    session_id = "sess_token_reg_01"
    user_id = "user_test_02"
    token = "[CBU_BANCARIO_1]"
    raw_cbu = "0170099900000088888888"

    token_vault.store_token(session_id, token, raw_cbu)

    receipt = execute_register_recipient(
        user_id=user_id, recipient_name="Fideicomiso Recaudador Fiscal", cbu=token, session_id=session_id
    )

    assert receipt["status"] == "REGISTERED"
    assert receipt["cbu_or_cvu"] == raw_cbu

    # Both raw CBU and token must be trusted
    assert mcp_action_client.is_trusted_recipient(user_id, raw_cbu, session_id=session_id)
    assert mcp_action_client.is_trusted_recipient(user_id, token, session_id=session_id)


def test_transfer_funds_tool_emits_pending_payload():
    """Validates that transfer_funds_tool outputs structured payload without debiting."""
    user_id = "user_test_03"
    result = transfer_funds_tool.invoke(
        {
            "user_id": user_id,
            "source_account": "acc_001",
            "destination_account": "[CBU_BANCARIO_1]",
            "amount": 35000.0,
            "concept": "Ajuste Fiscal",
        }
    )

    assert result["status"] == "PENDING_PEP"
    assert result["user_id"] == user_id
    assert result["amount"] == 35000.0
    assert result["destination"] == "[CBU_BANCARIO_1]"
    assert result["source_account"] == "acc_001"


class ScriptedChatModel(BaseChatModel):
    """Chat model that replays scripted AIMessages (supports bind_tools for the ReAct loop)."""

    script: List[AIMessage]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _run_action_agent(script, state):
    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=ScriptedChatModel(script=script)):
        return action_agent_node(state)


def test_action_agent_stages_transfer_for_pep_and_leaves_step_open():
    """A transfer is only staged: pending_tool_calls/pending_transfer are set and the PEP completes the step."""
    user_id = "user_default_01"
    transfer_call = {
        "name": "transfer_funds_tool",
        "args": {
            "user_id": user_id,
            "source_account": "acc_001",
            "destination_account": "[CBU_BANCARIO_1]",
            "amount": 35000.0,
            "concept": "pago",
        },
        "id": "call_tx_1",
    }
    state = {
        "session_id": "sess_stage_01",
        "user_id": user_id,
        "execution_plan": [
            {
                "step_id": 1,
                "title": "t",
                "target_node": "action_agent",
                "description": "Transferir 35000 ARS a [CBU_BANCARIO_1]",
                "result": None,
            }
        ],
    }
    result = _run_action_agent(
        [AIMessage(content="", tool_calls=[transfer_call]), AIMessage(content="Transferencia solicitada.")],
        state,
    )

    assert result["status"] == "PENDING_PEP"
    assert [c["name"] for c in result["pending_tool_calls"]] == ["transfer_funds_tool"]
    assert result["pending_transfer"]["amount"] == 35000.0
    assert result["pending_transfer"]["destination"] == "[CBU_BANCARIO_1]"
    assert "execution_plan" not in result  # step completion is delegated to pep_node


def test_action_agent_stages_recipient_registration_for_pep():
    """A registration is only staged (like a transfer): nothing is registered and the PEP completes the step."""
    user_id = "user_default_01"
    cbu = "0170099900000012345678"
    register_call = {
        "name": "register_recipient_tool",
        "args": {"user_id": user_id, "recipient_name": "Destinatario Test", "cbu": cbu, "session_id": "sess_reg_01"},
        "id": "call_reg_1",
    }
    state = {
        "session_id": "sess_reg_01",
        "user_id": user_id,
        "execution_plan": [
            {
                "step_id": 1,
                "title": "t",
                "target_node": "action_agent",
                "description": f"Registrar a Destinatario Test con CBU {cbu}",
                "result": None,
            }
        ],
    }
    result = _run_action_agent(
        [AIMessage(content="", tool_calls=[register_call]), AIMessage(content="Destinatario registrado.")],
        state,
    )

    assert not mcp_action_client.is_trusted_recipient(user_id, cbu)
    assert result["status"] == "PENDING_PEP"
    assert [c["name"] for c in result["pending_tool_calls"]] == ["register_recipient_tool"]
    assert "pending_transfer" not in result
    assert "execution_plan" not in result  # step completion is delegated to pep_node


def test_action_agent_tool_error_is_returned_to_the_model_and_loop_continues():
    """An invalid tool call (e.g. malformed CBU) does not crash the node: the model sees the error and answers."""
    bad_call = {
        "name": "register_recipient_tool",
        "args": {"user_id": "user_default_01", "recipient_name": "X", "cbu": "123"},
        "id": "call_bad",
    }
    state = {
        "session_id": "sess_tool_error",
        "user_id": "user_default_01",
        "execution_plan": [
            {"step_id": 1, "title": "t", "target_node": "action_agent", "description": "d", "result": None}
        ],
    }
    result = _run_action_agent(
        [AIMessage(content="", tool_calls=[bad_call]), AIMessage(content="No pude registrar: el CBU es inválido.")],
        state,
    )

    assert result["status"] == "COMPLETED"  # the invalid call is not staged
    assert "pending_tool_calls" not in result
    assert "inválido" in result["execution_plan"][0]["result"]


# --- transfer_funds_tool: form validation at staging --------------------------------------------------------------


def _transfer_call(call_id, **args):
    return {"name": "transfer_funds_tool", "args": {"user_id": "user_default_01", **args}, "id": call_id}


def _transfer_state(session_id):
    return {
        "session_id": session_id,
        "user_id": "user_default_01",
        "execution_plan": [
            {
                "step_id": 1,
                "title": "t",
                "target_node": "action_agent",
                "description": "Transferir 35000 ARS a [CBU_BANCARIO_1]",
                "result": None,
            }
        ],
    }


def test_transfer_schema_requires_destination_and_amount():
    """The schema sent to the model must not present the essential fields as optional."""
    schema = transfer_funds_tool.get_input_schema().model_json_schema()

    assert {"user_id", "destination_account", "amount"} <= set(schema["required"])


@pytest.mark.parametrize(
    "args",
    [
        {},  # only user_id: what a model with broken tool calling sends
        {"destination_account": "[CBU_BANCARIO_1]"},  # no amount
        {"amount": 100.0},  # no destination
    ],
)
def test_transfer_tool_rejects_calls_missing_required_fields(args):
    with pytest.raises(ValidationError):
        transfer_funds_tool.invoke({"user_id": "user_default_01", **args})


@pytest.mark.parametrize(
    "args, message",
    [
        ({"destination_account": "", "amount": 100.0}, "destino"),
        ({"destination_account": "   ", "amount": 100.0}, "destino"),
        ({"destination_account": "[CBU_BANCARIO_1]", "amount": 0.0}, "mayor a 0"),
        ({"destination_account": "[CBU_BANCARIO_1]", "amount": -50.0}, "mayor a 0"),
        ({"destination_account": "[CBU_BANCARIO_1]", "amount": float("nan")}, "mayor a 0"),
        ({"destination_account": "[CBU_BANCARIO_1]", "amount": float("inf")}, "mayor a 0"),
    ],
)
def test_transfer_tool_rejects_empty_destination_and_non_positive_amount(args, message):
    with pytest.raises(ValueError, match=message):
        transfer_funds_tool.invoke({"user_id": "user_default_01", **args})


def test_transfer_tool_still_accepts_the_destination_alias_and_numeric_strings():
    staged = transfer_funds_tool.invoke(
        {"user_id": "user_default_01", "destination_account": "", "destination": "CBU_BANCARIO_1", "amount": "49999"}
    )

    assert staged["destination"] == "[CBU_BANCARIO_1]" and staged["amount"] == 49999.0


def test_action_agent_does_not_stage_a_malformed_transfer_and_stages_the_corrected_retry():
    """The malformed call goes back to the model as a tool error; only the valid retry reaches the PEP."""
    bad = _transfer_call("call_bad")  # only user_id
    good = _transfer_call("call_good", destination_account="[CBU_BANCARIO_1]", amount=35000.0)

    result = _run_action_agent(
        [
            AIMessage(content="", tool_calls=[bad]),
            AIMessage(content="", tool_calls=[good]),
            AIMessage(content="Transferencia solicitada."),
        ],
        _transfer_state("sess_validation_retry"),
    )

    assert [c["id"] for c in result["pending_tool_calls"]] == ["call_good"]
    assert result["pending_transfer"]["amount"] == 35000.0
    assert result["pending_transfer"]["destination"] == "[CBU_BANCARIO_1]"
    assert result["status"] == "PENDING_PEP"


def test_action_agent_without_a_valid_call_stages_nothing_for_the_pep():
    """If the model never produces a well-formed transfer, no zero-amount request is sent to the PEP/PDP."""
    result = _run_action_agent(
        [
            AIMessage(content="", tool_calls=[_transfer_call("call_bad_1")]),
            AIMessage(content="", tool_calls=[_transfer_call("call_bad_2", destination_account="", amount=0.0)]),
            AIMessage(content="No pude emitir la transferencia."),
        ],
        _transfer_state("sess_validation_giveup"),
    )

    assert "pending_tool_calls" not in result and "pending_transfer" not in result
    assert result["status"] == "COMPLETED"
