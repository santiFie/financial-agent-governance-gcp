"""
Tests for LLM Factory, Node Tool Calling, and Provider Fallbacks.
"""
from unittest.mock import MagicMock, patch
import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.config import settings
from app.core.llm import get_chat_model
from app.graph.nodes.router import router_node, IntentClassification
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.nodes.action_agent import action_agent_node


def test_get_chat_model_mock_returns_none():
    """When LLM provider is mock, factory returns None."""
    with patch.object(settings, "llm_provider", "mock"):
        model = get_chat_model()
        assert model is None


def test_get_chat_model_missing_groq_key_falls_back():
    """When Groq is selected without API key, returns None gracefully."""
    with patch.object(settings, "llm_provider", "groq"):
        with patch.object(settings, "groq_api_key", None):
            with patch.dict("os.environ", {}, clear=True):
                model = get_chat_model()
                assert model is None


def test_router_node_uses_llm_structured_output():
    """Router node uses LLM structured output when available."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = IntentClassification(intent="transfer")
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("app.graph.nodes.router.get_chat_model", return_value=mock_llm):
        state = {
            "sanitized_input": "Quisiera mandar dinero a un familiar",
            "is_threat": False
        }
        res = router_node(state)
        assert res["current_intent"] == "transfer"
        mock_structured.invoke.assert_called_once()


def test_readonly_agent_invokes_tools_via_llm():
    """Readonly agent node executes tool calls requested by LLM."""
    mock_llm = MagicMock()
    mock_bound = MagicMock()
    
    # LLM requests calling get_account_balances
    tool_call = {
        "name": "get_account_balances",
        "args": {"user_id": "user_default_01"},
        "id": "call_123"
    }
    first_response = AIMessage(content="", tool_calls=[tool_call])
    second_response = AIMessage(content="Estado de Cuentas y Saldos Disponibles: Tienes $150,000 en tu cuenta.")
    
    mock_bound.invoke.return_value = first_response
    mock_llm.bind_tools.return_value = mock_bound
    mock_llm.invoke.return_value = second_response

    with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=mock_llm):
        state = {
            "current_intent": "balance",
            "user_id": "user_default_01",
            "sanitized_input": "¿Cuánto dinero tengo?"
        }
        res = readonly_agent_node(state)
        assert res["status"] == "COMPLETED"
        assert "Saldos Disponibles" in res["final_response"]
        mock_bound.invoke.assert_called_once()
        mock_llm.invoke.assert_called_once()


def test_action_agent_invokes_stage_transfer_tool():
    """Action agent node calls stage_transfer_request tool and triggers HITL interrupt."""
    mock_llm = MagicMock()
    mock_bound = MagicMock()

    tool_call = {
        "name": "stage_transfer_request",
        "args": {
            "session_id": "test_llm_action_sess",
            "user_id": "user_default_01",
            "amount": 50000.0,
            "source_account_id": "acc_001",
            "target_token": "[CBU_BANCARIO_1]"
        },
        "id": "call_tx_001"
    }
    first_response = AIMessage(content="", tool_calls=[tool_call])
    mock_bound.invoke.return_value = first_response
    mock_llm.bind_tools.return_value = mock_bound

    # Mock interrupt to simulate immediate human approval
    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=mock_llm):
        with patch("app.graph.nodes.action_agent.interrupt", return_value={"decision": "APPROVED", "admin_user": "compliance_bot"}):
            state = {
                "session_id": "test_llm_action_sess",
                "user_id": "user_default_01",
                "sanitized_input": "Transferir $50000 a [CBU_BANCARIO_1]"
            }
            res = action_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert res["approval_status"] == "APPROVED"
            assert "Transferencia Ejecutada con Éxito" in res["final_response"]
            assert res["pending_transfer"]["amount"] == 50000.0
