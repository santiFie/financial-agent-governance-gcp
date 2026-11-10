"""The gate for live-LLM tests must not treat gcloud ADC on the machine as permission to call Vertex AI."""

from unittest.mock import patch

from tests import live_llm

VERTEX = {"model_name": "vertexai", "litellm_params": {}}
GROQ = {"model_name": "groq", "litellm_params": {}}


def _available(provider_setting: str, deployments, raises=False):
    def fake(*args, **kwargs):
        if raises:
            raise live_llm.LLMConfigurationError("sin credenciales")
        return deployments

    with (
        patch.object(live_llm.settings, "llm_provider", provider_setting),
        patch.object(live_llm, "get_available_deployments", side_effect=fake),
    ):
        return live_llm.live_llm_available()


def test_only_vertex_from_adc_is_not_available():
    assert _available("litellm", [VERTEX]) is False


def test_a_real_provider_key_is_available_even_if_vertex_is_also_there():
    assert _available("litellm", [GROQ, VERTEX]) is True


def test_vertex_is_available_when_requested_explicitly():
    assert _available("vertexai", [VERTEX]) is True


def test_no_credentials_is_not_available():
    assert _available("litellm", [], raises=False) is False
    assert _available("vertexai", [], raises=True) is False


def test_live_tests_carry_the_deselect_marker():
    @live_llm.requires_live_llm
    def sample():
        pass

    assert "requires_live_llm" in {m.name for m in sample.pytestmark}
