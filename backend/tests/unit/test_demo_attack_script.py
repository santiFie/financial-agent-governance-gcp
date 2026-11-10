"""Offline tests for scripts/demo_attack.py: the narrator classifies plan vs replan and tracks staging, with no LLM."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "demo_attack.py"


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
    n.on_update(
        "router", {"execution_plan": [_step(1, "readonly_agent"), _step(2, "action_agent"), _step(3, "action_agent")]}
    )
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
    n.on_update(
        "action_agent", {"final_response": "registrado", "execution_plan": [{**plan[0], "result": "ok"}, plan[1]]}
    )
    n.on_update("pep_node", {"status": "COMPLETED", "approval_status": "NONE"})  # no transfer staged: nothing to report
    n.on_update("readonly_agent", {"final_response": "r"})

    out = capsys.readouterr().out
    assert "2. action_agent [paso 1]" in out
    assert "3. readonly_agent [paso 2]" in out
    assert "pep_node" not in out


def test_save_run_writes_full_record_and_updates_manifest(tmp_path):
    import json

    demo = _load()
    summary = {
        "session_id": "sess_x",
        "execution_path": "FAST_PATH",
        "transfers_executed": [{"amount": 1}],
        "events": [{"kind": "result", "title": "t", "node": "n", "text": "x" * 5000}],
    }
    meta = {"scenario_id": "sc", "model": "p/m"}

    path = demo.save_run(summary, meta, tmp_path)
    demo.save_run({**summary, "session_id": "sess_y"}, meta, tmp_path)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert len(record["events"][0]["text"]) == 5000  # nothing truncated
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2 and manifest[0]["execution_path"] == "FAST_PATH"


# --- GCP preflight, --require-gcp and per-run evidence ---------------------------------------------------------


def _settings(**overrides):
    from types import SimpleNamespace

    base = dict(
        security_mode="gcp_live",
        use_gcp_model_armor=True,
        model_armor_template_id="aegisbank-template",
        gcp_location="us-central1",
        vertex_location="global",
        vertex_model_name="gemini-3.1-flash-lite",
        llm_provider="vertexai",
        orchestrator_provider=None,
        orchestrator_model=None,
        checkpointer_type="firestore",
        firestore_collection="langgraph_checkpoints",
        use_gcs_ingestion=True,
        gcs_bucket_name="proj-aegisbank-docs",
    )
    return SimpleNamespace(**{**base, **overrides})


def _firestore_saver():
    from app.db.firestore_saver import FirestoreSaver
    from tests.fakes_firestore import FakeFirestoreClient

    return FirestoreSaver(FakeFirestoreClient(), "langgraph_checkpoints")


def _backends(plan):
    return {row["service"]: row["backend"] for row in plan}


def test_service_plan_marks_every_service_as_gcp_when_fully_configured(monkeypatch):
    from app.config import settings

    demo = _load()
    monkeypatch.setattr(settings, "llm_provider", "vertexai")
    monkeypatch.setattr(settings, "vertex_location", "global")
    plan = demo.build_service_plan(_settings(), _firestore_saver())

    assert _backends(plan) == {
        "model_armor": "GCP",
        "dlp": "GCP",
        "llm": "GCP",
        "checkpointer": "GCP",
        "gcs": "GCP",
    }
    llm = next(r for r in plan if r["service"] == "llm")
    assert "vertexai/gemini-3.1-flash-lite@global" in llm["detail"]
    assert demo.gcp_requirements_problems(_settings(), plan) == []


def test_service_plan_reports_local_services_and_lists_them_as_problems(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from app.config import settings

    demo = _load()
    monkeypatch.setattr(settings, "llm_provider", "litellm")
    local = _settings(
        security_mode="local_mock",
        use_gcp_model_armor=False,
        llm_provider="litellm",
        use_gcs_ingestion=False,
        gcs_bucket_name=None,
    )
    plan = demo.build_service_plan(local, MemorySaver())

    assert _backends(plan) == {
        "model_armor": "LOCAL",
        "dlp": "LOCAL",
        "llm": "EXTERNO",
        "checkpointer": "LOCAL",
        "gcs": "LOCAL",
    }
    problems = "\n".join(demo.gcp_requirements_problems(local, plan))
    assert "SECURITY_MODE=local_mock" in problems and "Model Armor" in problems and "Checkpointer" in problems
    assert "Documento (GCS)" not in problems  # reading the document locally is allowed (make attack-gcp GCS=false)


def test_services_summary_flags_degraded_runs_and_keeps_startup_fallbacks():
    demo = _load()
    clean = demo.services_summary(
        {"dlp": {"gcp": 2, "local": 0, "local_fallback": 0, "last_fallback_reason": None}}, {}
    )
    assert clean["degraded"] is False and clean["fallbacks"] == {}

    degraded = demo.services_summary(
        {"dlp": {"gcp": 0, "local": 0, "local_fallback": 1, "last_fallback_reason": "inspect: 403"}},
        {"checkpointer": "403 firestore"},
    )
    assert degraded["degraded"] is True
    assert degraded["fallbacks"] == {"checkpointer": "403 firestore", "dlp": "inspect: 403"}


def test_narrator_records_action_steps_that_ended_without_a_valid_tool_call():
    demo = _load()
    n = demo.Narrator(verbose=False)
    n.on_update("router", {"execution_plan": [_step(2, "action_agent")]})
    n.on_update("action_agent", {"final_response": "No pude completar la solicitud"})
    assert n.action_steps_without_tool_call == ["paso 2"]

    ok = demo.Narrator(verbose=False)
    ok.on_update("action_agent", {"pending_tool_calls": [{"name": "transfer_funds_tool", "args": {"amount": 1}}]})
    assert ok.action_steps_without_tool_call == []


def test_manifest_marks_gcp_purity_per_run(tmp_path):
    import json

    demo = _load()
    base = {"session_id": "s", "execution_path": "NONE", "transfers_executed": [], "events": []}
    live = {"scenario_id": "sc", "model": "vertexai/m@global", "security_mode": "gcp_live"}

    demo.save_run({**base, "session_id": "a", "services": {"degraded": False}}, live, tmp_path)
    demo.save_run({**base, "session_id": "b", "services": {"degraded": True}}, live, tmp_path)
    demo.save_run(
        {**base, "session_id": "c", "services": {"degraded": False}}, {**live, "security_mode": "local_mock"}, tmp_path
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    by_session = {m["file"].split("_")[-1].split(".")[0]: m["pure_gcp"] for m in manifest}
    assert by_session == {"a": True, "b": False, "c": None}


def test_require_gcp_aborts_before_running_when_the_configuration_is_local(monkeypatch, capsys):
    from unittest.mock import MagicMock

    import app.core.llm as llm_module
    from app.config import settings

    demo = _load()
    monkeypatch.setattr(settings, "security_mode", "local_mock")
    monkeypatch.setattr(settings, "use_gcp_model_armor", False)
    monkeypatch.setattr(settings, "llm_provider", "litellm")
    monkeypatch.setattr(llm_module, "get_chat_model", MagicMock())
    monkeypatch.setattr(demo, "run_once", MagicMock(side_effect=AssertionError("must not run")))
    monkeypatch.setattr("sys.argv", ["demo_attack.py", "--require-gcp", "--no-save"])
    monkeypatch.chdir(Path.cwd())

    assert demo.main() == 3

    err = capsys.readouterr().err
    assert "--require-gcp" in err and "SECURITY_MODE=local_mock" in err and "make build-gcp" in err
