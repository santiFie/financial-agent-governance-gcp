"""
Tests for LLM Factory, Node Tool Calling, and Provider Fallbacks.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.config import settings
from app.core.llm import LLMConfigurationError, get_chat_model
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.nodes.router import router_node


def test_get_chat_model_unknown_provider_raises():
    """When LLM provider is unknown, factory raises LLMConfigurationError."""
    with patch.object(settings, "llm_provider", "unknown"):
        with pytest.raises(LLMConfigurationError):
            get_chat_model()


def test_get_chat_model_no_credentials_raises():
    """When no LLM provider has valid credentials, factory raises LLMConfigurationError."""
    from google.auth.exceptions import DefaultCredentialsError

    with (
        patch.object(settings, "groq_api_key", None),
        patch.object(settings, "nvidia_api_key", None),
        patch.object(settings, "openrouter_api_key", None),
        patch.object(settings, "gemini_api_key", None),
        patch.object(settings, "gcp_project_id", None),
        patch("google.auth.default", side_effect=DefaultCredentialsError("No ADC")),
        patch.dict("os.environ", {}, clear=True),
    ):
        with pytest.raises(LLMConfigurationError):
            get_chat_model()


@pytest.fixture
def fake_adc():
    """Simulates Application Default Credentials so Vertex AI counts as configured on any machine."""
    with patch("google.auth.default", return_value=(MagicMock(), "test-proj")):
        yield


def test_get_chat_model_fallbacks_without_openrouter(fake_adc):
    """
    When Groq, NVIDIA and Vertex AI have credentials but OpenRouter does not,
    the fallback chain is: groq -> nvidia -> vertexai.
    """
    with (
        patch.object(settings, "groq_api_key", "gsk_test"),
        patch.object(settings, "nvidia_api_key", "nvapi_test"),
        patch.object(settings, "openrouter_api_key", None),
        patch.object(settings, "gcp_project_id", "test-proj"),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}),
    ):
        model = get_chat_model()
        assert model.model_name == "groq"
        assert [m["model_name"] for m in model.router.model_list] == ["groq", "nvidia", "vertexai"]
        assert model.router.fallbacks == [{"groq": ["nvidia", "vertexai"]}]


def test_get_chat_model_fallbacks_all_four_providers(fake_adc):
    """
    When Groq, NVIDIA, Vertex AI and OpenRouter all have credentials,
    the fallback chain is: groq -> nvidia -> vertexai -> openrouter.
    """
    with (
        patch.object(settings, "groq_api_key", "gsk_test"),
        patch.object(settings, "nvidia_api_key", "nvapi_test"),
        patch.object(settings, "openrouter_api_key", "sk-or-test"),
        patch.object(settings, "gcp_project_id", "test-proj"),
    ):
        model = get_chat_model()
        assert model.model_name == "groq"
        assert [m["model_name"] for m in model.router.model_list] == ["groq", "nvidia", "vertexai", "openrouter"]
        assert model.router.fallbacks == [{"groq": ["nvidia", "vertexai", "openrouter"]}]


def test_get_chat_model_fallbacks_skipping_groq(fake_adc):
    """When Groq is absent, primary is nvidia and fallback is vertexai."""
    with (
        patch.object(settings, "groq_api_key", None),
        patch.object(settings, "nvidia_api_key", "nvapi_test"),
        patch.object(settings, "openrouter_api_key", None),
        patch.object(settings, "gcp_project_id", "test-proj"),
        patch.dict("os.environ", {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}),
    ):
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
                "description": "Transferir 5000 a destinatario",
            }
        ],
        "final_answer": None,
    }
    tool_call = {"name": "PlanUpdate", "args": plan_args, "id": "call_plan_01"}
    mock_bound.invoke.return_value = AIMessage(content="", tool_calls=[tool_call])
    mock_llm.bind_tools.return_value = mock_bound

    with patch("app.graph.nodes.router.get_chat_model", return_value=mock_llm):
        state = {"sanitized_input": "Quisiera mandar dinero a un familiar", "is_threat": False}
        res = router_node(state)
        assert len(res["execution_plan"]) == 1
        assert res["execution_plan"][0]["target_node"] == "action_agent"


def test_readonly_agent_invokes_tools_via_llm():
    """Readonly agent node executes active plan step via ReAct executor."""
    mock_executor = MagicMock()
    mock_executor.invoke.return_value = {
        "messages": [AIMessage(content="Estado de Cuentas y Saldos Disponibles: Tienes $150,000 en tu cuenta.")]
    }

    with patch("app.graph.nodes.readonly_agent._build_executor", return_value=mock_executor):
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


def test_orchestrator_model_settings_reach_get_chat_model():
    """ORCHESTRATOR_PROVIDER / ORCHESTRATOR_MODEL are forwarded to the LLM factory (no hardcoded model)."""
    mock_llm = MagicMock()
    mock_bound = MagicMock()
    mock_bound.invoke.return_value = AIMessage(
        content="", tool_calls=[{"name": "PlanUpdate", "args": {"new_steps": []}, "id": "call_plan_01"}]
    )
    mock_llm.bind_tools.return_value = mock_bound

    with (
        patch("app.graph.nodes.router.settings.orchestrator_provider", "openrouter"),
        patch("app.graph.nodes.router.settings.orchestrator_model", "some/model:free"),
        patch("app.graph.nodes.router.get_chat_model", return_value=mock_llm) as factory,
    ):
        router_node({"sanitized_input": "Consultar saldo", "is_threat": False})

    factory.assert_called_once_with(temperature=0.0, provider="openrouter", model="some/model:free")


# --- Vertex-only mode (LLM_PROVIDER=vertexai) ---------------------------------------------------------------------


def _all_external_keys():
    return (
        patch.object(settings, "groq_api_key", "gsk_test"),
        patch.object(settings, "nvidia_api_key", "nvapi_test"),
        patch.object(settings, "openrouter_api_key", "sk-or-test"),
        patch.object(settings, "gcp_project_id", "test-proj"),
    )


def test_llm_provider_vertexai_builds_a_vertex_only_chain_without_fallbacks(fake_adc):
    """LLM_PROVIDER=vertexai must ignore Groq/NVIDIA/OpenRouter keys even when they are configured."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _all_external_keys():
            stack.enter_context(p)
        stack.enter_context(patch.object(settings, "llm_provider", "vertexai"))
        stack.enter_context(patch.object(settings, "vertex_model_name", "gemini-test-model"))
        model = get_chat_model()

    assert model.model_name == "vertexai"
    assert [m["model_name"] for m in model.router.model_list] == ["vertexai"]
    assert not model.router.fallbacks
    assert model.router.model_list[0]["litellm_params"]["model"] == "vertex_ai/gemini-test-model"


def test_vertexai_mode_does_not_accept_a_gemini_api_key_as_credentials():
    from google.auth.exceptions import DefaultCredentialsError

    with (
        patch.object(settings, "llm_provider", "vertexai"),
        patch.object(settings, "gemini_api_key", "AIza-test"),
        patch.object(settings, "gcp_project_id", "test-proj"),
        patch("google.auth.default", side_effect=DefaultCredentialsError("No ADC")),
        patch.dict("os.environ", {}, clear=True),
    ):
        with pytest.raises(LLMConfigurationError, match="ADC"):
            get_chat_model()


def test_vertexai_mode_uses_vertex_location_when_set(fake_adc):
    with (
        patch.object(settings, "llm_provider", "vertexai"),
        patch.object(settings, "gcp_project_id", "test-proj"),
        patch.object(settings, "gcp_location", "us-central1"),
        patch.object(settings, "vertex_location", "global"),
    ):
        model = get_chat_model()

    assert model.router.model_list[0]["litellm_params"]["vertex_location"] == "global"


def test_every_agent_resolves_vertex_only_through_the_same_factory(fake_adc):
    """readonly/action agents call get_chat_model() with no provider; the router passes orchestrator_provider=None."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _all_external_keys():
            stack.enter_context(p)
        stack.enter_context(patch.object(settings, "llm_provider", "vertexai"))
        stack.enter_context(patch.object(settings, "orchestrator_provider", None))
        stack.enter_context(patch.object(settings, "orchestrator_model", None))
        executor_model = get_chat_model(temperature=0.0)
        orchestrator_model = get_chat_model(
            temperature=0.0, provider=settings.orchestrator_provider, model=settings.orchestrator_model
        )

    for model in (executor_model, orchestrator_model):
        assert [m["model_name"] for m in model.router.model_list] == ["vertexai"]


def test_litellm_mode_keeps_the_multi_provider_chain(fake_adc):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _all_external_keys():
            stack.enter_context(p)
        stack.enter_context(patch.object(settings, "llm_provider", "litellm"))
        model = get_chat_model()

    assert [m["model_name"] for m in model.router.model_list] == ["groq", "nvidia", "vertexai", "openrouter"]


def test_llm_backend_is_recorded_in_service_status(fake_adc):
    from app.core.service_status import service_status

    service_status.reset()
    with (
        patch.object(settings, "llm_provider", "vertexai"),
        patch.object(settings, "gcp_project_id", "test-proj"),
    ):
        get_chat_model()

    assert service_status.snapshot()["llm"]["gcp"] == 1
    service_status.reset()
