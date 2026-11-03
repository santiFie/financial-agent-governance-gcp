"""
Tests for LLM Factory, Node Tool Calling, and Provider Fallbacks.
"""
from unittest.mock import MagicMock, patch
import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.config import settings
from app.core.llm import get_chat_model, LLMConfigurationError
from app.graph.nodes.router import router_node, PlanUpdate, NewPlanStep
from app.graph.agent_registry import TargetNode
from app.graph.nodes.readonly_agent import readonly_agent_node


def test_get_chat_model_unknown_provider_raises():
    """When LLM provider is unknown, factory raises LLMConfigurationError."""
    with patch.object(settings, "llm_provider", "unknown"):
        with pytest.raises(LLMConfigurationError):
            get_chat_model()


def test_get_chat_model_no_credentials_raises():
    """When no LLM provider has valid credentials, factory raises LLMConfigurationError."""
    from google.auth.exceptions import DefaultCredentialsError
    with patch.object(settings, "groq_api_key", None), \
         patch.object(settings, "nvidia_api_key", None), \
         patch.object(settings, "openrouter_api_key", None), \
         patch.object(settings, "gemini_api_key", None), \
         patch.object(settings, "gcp_project_id", None), \
         patch("google.auth.default", side_effect=DefaultCredentialsError("No ADC")), \
         patch.dict("os.environ", {}, clear=True):
        with pytest.raises(LLMConfigurationError):
            get_chat_model()


def test_get_chat_model_fallbacks_without_openrouter():
    """
    When Groq, NVIDIA and Vertex AI have credentials but OpenRouter does not,
    the fallback chain is: groq -> nvidia -> vertexai.
    """
    with patch.object(settings, "groq_api_key", "gsk_test"), \
         patch.object(settings, "nvidia_api_key", "nvapi_test"), \
         patch.object(settings, "openrouter_api_key", None), \
         patch.object(settings, "gcp_project_id", "test-proj"), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
        model = get_chat_model()
        assert model.model_name == "groq"
        assert [m["model_name"] for m in model.router.model_list] == ["groq", "nvidia", "vertexai"]
        assert model.router.fallbacks == [{"groq": ["nvidia", "vertexai"]}]


def test_get_chat_model_fallbacks_all_four_providers():
    """
    When Groq, NVIDIA, Vertex AI and OpenRouter all have credentials,
    the fallback chain is: groq -> nvidia -> vertexai -> openrouter.
    """
    with patch.object(settings, "groq_api_key", "gsk_test"), \
         patch.object(settings, "nvidia_api_key", "nvapi_test"), \
         patch.object(settings, "openrouter_api_key", "sk-or-test"), \
         patch.object(settings, "gcp_project_id", "test-proj"):
        model = get_chat_model()
        assert model.model_name == "groq"
        assert [m["model_name"] for m in model.router.model_list] == ["groq", "nvidia", "vertexai", "openrouter"]
        assert model.router.fallbacks == [{"groq": ["nvidia", "vertexai", "openrouter"]}]


def test_get_chat_model_fallbacks_skipping_groq():
    """When Groq is absent, primary is nvidia and fallback is vertexai."""
    with patch.object(settings, "groq_api_key", None), \
         patch.object(settings, "nvidia_api_key", "nvapi_test"), \
         patch.object(settings, "openrouter_api_key", None), \
         patch.object(settings, "gcp_project_id", "test-proj"), \
         patch.dict("os.environ", {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}):
        model = get_chat_model()
        assert model.model_name == "nvidia"
        assert [m["model_name"] for m in model.router.model_list] == ["nvidia", "vertexai"]
        assert model.router.fallbacks == [{"nvidia": ["vertexai"]}]


def test_get_chat_model_supports_bind_tools():
    """ChatLiteLLMRouter supports LangChain bind_tools for agent nodes."""
    from pydantic import BaseModel, Field

    class SampleTool(BaseModel):
        field: str = Field(description="Sample")

    with patch.object(settings, "groq_api_key", "gsk_test"):
        model = get_chat_model()
        bound = model.bind_tools([SampleTool])
        assert hasattr(bound, "invoke")



def test_router_node_uses_llm_plan_update():
    """Router node uses LLM tool calling to produce PlanUpdate."""
    mock_llm = MagicMock()
    mock_bound = MagicMock()
    
    plan_args = {
        "new_steps": [
            {
                "title": "Transferir fondos",
                "target_node": "action_agent",
                "description": "Transferir 5000 a destinatario"
            }
        ],
        "final_answer": None
    }
    tool_call = {
        "name": "PlanUpdate",
        "args": plan_args,
        "id": "call_plan_01"
    }
    mock_bound.invoke.return_value = AIMessage(content="", tool_calls=[tool_call])
    mock_llm.bind_tools.return_value = mock_bound

    with patch("app.graph.nodes.router.get_chat_model", return_value=mock_llm):
        state = {
            "sanitized_input": "Quisiera mandar dinero a un familiar",
            "is_threat": False
        }
        res = router_node(state)
        assert len(res["execution_plan"]) == 1
        assert res["execution_plan"][0]["target_node"] == "action_agent"


def test_readonly_agent_invokes_tools_via_llm():
    """Readonly agent node executes active plan step via ReAct executor."""
    mock_executor = MagicMock()
    mock_executor.invoke.return_value = {
        "messages": [
            AIMessage(content="Estado de Cuentas y Saldos Disponibles: Tienes $150,000 en tu cuenta.")
        ]
    }

    with patch("app.graph.nodes.readonly_agent._react_executor", mock_executor):
        state = {
            "user_id": "user_default_01",
            "execution_plan": [
                {
                    "step_id": 1,
                    "title": "Consultar saldos",
                    "target_node": "readonly_agent",
                    "description": "Consultar saldos de cuentas",
                    "result": None,
                }
            ],
        }
        res = readonly_agent_node(state)
        assert res["status"] == "COMPLETED"
        assert "Saldos Disponibles" in res["final_response"]
        assert res["execution_plan"][0]["result"] is not None
        mock_executor.invoke.assert_called_once()
