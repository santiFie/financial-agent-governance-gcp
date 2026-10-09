"""
Unit tests for Prompt Management and Decoupled Prompt Registry.
Validates template loading, required variable validation and rendering.
"""
import pytest
from app.core.prompt_manager import prompt_manager, PromptManager


def test_all_prompts_registered():
    """Verify that all core prompt templates are present and loaded."""
    prompts = prompt_manager.list_prompts()
    assert "router" in prompts
    assert "readonly_agent" in prompts
    assert "action_agent" in prompts
    assert "sanitizer_responses" in prompts


def test_render_router_prompt():
    """Verify router prompt rendering."""
    prompt_str = prompt_manager.render("router")
    assert "clasificador de intenciones" in prompt_str
    assert len(prompt_str) > 10


def test_render_readonly_agent_prompt():
    """Verify readonly_agent prompt rendering with valid variables."""
    prompt_str = prompt_manager.render("readonly_agent", user_id="user_test_99")
    assert "user_test_99" in prompt_str
    assert "Estado de Cuentas y Saldos Disponibles" in prompt_str


def test_render_readonly_agent_missing_variable():
    """Verify ValueError is raised when required variable is missing."""
    with pytest.raises(ValueError) as excinfo:
        prompt_manager.render("readonly_agent")
    assert "user_id" in str(excinfo.value)


def test_render_action_agent_prompt():
    """Verify action_agent prompt rendering with all required parameters."""
    prompt_str = prompt_manager.render(
        "action_agent",
        session_id="sess_123",
        user_id="user_456",
        source_acc="acc_source"
    )
    assert "sess_123" in prompt_str
    assert "user_456" in prompt_str
    assert "acc_source" in prompt_str
    assert "stage_transfer_request" in prompt_str


def test_render_action_agent_missing_parameters():
    """Verify ValueError is raised if any parameter is missing."""
    with pytest.raises(ValueError) as excinfo:
        prompt_manager.render("action_agent", session_id="sess_123")
    assert "user_id" in str(excinfo.value) or "source_acc" in str(excinfo.value)


def test_render_sanitizer_response():
    """Verify sanitizer response message rendering."""
    msg = prompt_manager.render("sanitizer_responses", threat_cat="PROMPT_INJECTION")
    assert "PROMPT_INJECTION" in msg
    assert "Model Armor" in msg


def test_unknown_prompt_raises_key_error():
    """Verify KeyError is raised for non-existent prompts."""
    with pytest.raises(KeyError):
        prompt_manager.render("non_existent_prompt_xyz")
