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


def test_events_are_numbered_chronologically_and_pep_noop_is_skipped(capsys):
    demo = _load()
    n = demo.Narrator(verbose=True)
    plan = [_step(1, "action_agent"), _step(2, "readonly_agent")]
    n.on_update("router", {"execution_plan": plan})
    n.on_update("action_agent", {"final_response": "registrado",
                                 "execution_plan": [{**plan[0], "result": "ok"}, plan[1]]})
    n.on_update("pep_node", {"status": "COMPLETED", "approval_status": "NONE"})  # no transfer staged: nothing to report
    n.on_update("readonly_agent", {"final_response": "r"})

    out = capsys.readouterr().out
    assert "2. action_agent [paso 1]" in out
    assert "3. readonly_agent [paso 2]" in out
    assert "pep_node" not in out


def test_save_run_writes_full_record_and_updates_manifest(tmp_path):
    import json

    demo = _load()
    summary = {"session_id": "sess_x", "execution_path": "FAST_PATH", "transfers_executed": [{"amount": 1}],
               "events": [{"kind": "result", "title": "t", "node": "n", "text": "x" * 5000}]}
    meta = {"scenario_id": "sc", "model": "p/m"}

    path = demo.save_run(summary, meta, tmp_path)
    demo.save_run({**summary, "session_id": "sess_y"}, meta, tmp_path)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert len(record["events"][0]["text"]) == 5000  # nothing truncated
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2 and manifest[0]["execution_path"] == "FAST_PATH"
