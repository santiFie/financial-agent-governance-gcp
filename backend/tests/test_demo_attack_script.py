"""Offline tests for scripts/demo_attack.py: the narrator classifies plan vs replan and tracks staging, with no LLM."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "demo_attack.py"


def _load():
    spec = importlib.util.spec_from_file_location("demo_attack", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(i, target, title="t"):
    return {"step_id": i, "title": title, "target_node": target, "description": "d", "result": None}


def test_narrator_separates_initial_plan_from_replan_and_tracks_staging():
    demo = _load()
    n = demo.Narrator(verbose=False)

    n.on_update("router", {"execution_plan": [_step(1, "readonly_agent")]})
    n.on_update("router", {"execution_plan": [_step(1, "readonly_agent"), _step(2, "action_agent"), _step(3, "action_agent")]})
    n.on_update("action_agent", {"pending_tool_calls": [{"name": "transfer_funds_tool", "args": {"amount": 1}}]})

    assert [s["step_id"] for s in n.initial_plan] == [1]
    assert [s["step_id"] for s in n.replanned] == [2, 3]
    assert [c["name"] for c in n.staged] == ["transfer_funds_tool"]


def test_narrator_marks_hitl_pause_and_threat():
    demo = _load()
    n = demo.Narrator(verbose=False)
    n.on_update("__interrupt__", ())
    n.on_update("sanitizer", {"is_threat": True, "threat_category": "X", "defense_action": "BLOCKED"})
    assert n.paused_for_hitl
    assert n.threat == "X"
