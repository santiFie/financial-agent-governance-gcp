"""
Unit tests for Prompt Management and Decoupled Prompt Registry.
Validates template loading, required variable validation and rendering.
"""

import pytest

from app.core.prompt_manager import prompt_manager


def test_all_prompts_registered():
    """Verify that all core prompt templates are present and loaded."""
    prompts = prompt_manager.list_prompts()
    for name in (
        "orchestrator_initial",
        "orchestrator_replan",
        "readonly_agent",
        "action_agent",
        "sanitizer_responses",
    ):
        assert name in prompts


@pytest.mark.parametrize("name", ["orchestrator_initial", "orchestrator_replan"])
def test_render_orchestrator_prompts(name):
    """Orchestrator prompts render the agent catalog and the step limit."""
    prompt_str = prompt_manager.render(name, agents_catalog="- 'read_only_agent': lectura", max_steps=4)
    assert "read_only_agent" in prompt_str
    assert "PlanUpdate" in prompt_str


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
