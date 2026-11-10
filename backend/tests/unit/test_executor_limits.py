"""Executor agents (action_agent / readonly_agent) stop after executor_max_tool_rounds tool rounds."""

from typing import Any, Dict
from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import Settings, settings
from app.graph.nodes.action_agent import action_agent_node
from app.graph.nodes.readonly_agent import readonly_agent_node
from tests.unit.test_action_multitool import ScriptedChatModel

USER = "user_default_01"


class RepeatingToolCallModel(BaseChatModel):
    """A model that never finishes: every turn it emits the same tool call (with a fresh id, like a real model)."""

    tool_call: Dict[str, Any]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "repeating"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.cursor += 1
        call = {**self.tool_call, "id": f"call_{self.cursor}"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


def _state(target_node, description):
    return {
        "session_id": "sess_limits",
        "user_id": USER,
        "execution_plan": [
            {"step_id": 1, "title": "t", "target_node": target_node, "description": description, "result": None}
        ],
    }


def test_default_limit_is_small_and_must_be_positive():
    assert Settings(_env_file=None).executor_max_tool_rounds == 4
    with pytest.raises(ValueError):
        Settings(executor_max_tool_rounds=0, _env_file=None)


@pytest.mark.parametrize("rounds", [1, 3])
def test_action_agent_stops_a_model_that_repeats_an_invalid_tool_call(monkeypatch, rounds):
    """The model never fixes its call (only user_id): the loop is cut and nothing reaches the PEP."""
    monkeypatch.setattr(settings, "executor_max_tool_rounds", rounds)
    model = RepeatingToolCallModel(tool_call={"name": "register_recipient_tool", "args": {"user_id": USER}})

    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=model):
        result = action_agent_node(_state("action_agent", "Registrar al destinatario [CBU_BANCARIO_3]"))

    assert model.cursor == rounds + 1  # `rounds` tool rounds, then the model turn that would start another one
    assert result["status"] == "COMPLETED"
    assert "pending_tool_calls" not in result and "pending_transfer" not in result
    assert f"{rounds} rondas" in result["final_response"] and "No se realizó ninguna acción" in result["final_response"]
    assert (
        result["execution_plan"][0]["result"] == result["final_response"]
    )  # the step is closed, the router can replan


def test_action_agent_that_finishes_within_the_limit_is_unaffected(monkeypatch):
    monkeypatch.setattr(settings, "executor_max_tool_rounds", 2)
    good = {
        "name": "transfer_funds_tool",
        "args": {"user_id": USER, "destination_account": "[CBU_BANCARIO_1]", "amount": 100.0},
        "id": "call_ok",
    }
    model = ScriptedChatModel(script=[AIMessage(content="", tool_calls=[good]), AIMessage(content="Listo.")])

    with patch("app.graph.nodes.action_agent.get_chat_model", return_value=model):
        result = action_agent_node(_state("action_agent", "Transferir 100 ARS a [CBU_BANCARIO_1]"))

    assert result["status"] == "PENDING_PEP" and [c["id"] for c in result["pending_tool_calls"]] == ["call_ok"]


def test_readonly_agent_is_bounded_too(monkeypatch):
    monkeypatch.setattr(settings, "executor_max_tool_rounds", 2)
    model = RepeatingToolCallModel(tool_call={"name": "get_account_balances", "args": {"user_id": USER}})

    with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=model):
        result = readonly_agent_node(_state("readonly_agent", "Consultar saldos"))

    assert model.cursor == 3
    assert result["status"] == "COMPLETED" and "2 rondas" in result["final_response"]
    assert result["execution_plan"][0]["result"] == result["final_response"]
