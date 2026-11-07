"""Offline tests for the observability timeline (no LLM): plan vs replan, staging, PDP decision, and /api/chat wiring."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from app.core.timeline import TimelineBuilder, pdp_decision_observer
from app.main import app

client = TestClient(app)


def _step(i, target, title="t"):
    return {"step_id": i, "title": title, "target_node": target, "description": "d", "result": None}


def _stream(builder_hook=None):
    def stream(*args, **kwargs):
        yield {"sanitizer": {"defense_action": "PASSED", "detected_pii": []}}
        yield {
            "router": {
                "execution_plan": [_step(1, "readonly_agent")],
                "orchestrator_scratchpad": [
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "read_document_tool", "args": {"filename": "x.txt"}, "id": "1"}],
                    )
                ],
            }
        }
        yield {"orchestrator_tools": {"orchestrator_scratchpad": [ToolMessage(content="contenido", tool_call_id="1")]}}
        yield {"router": {"execution_plan": [_step(1, "readonly_agent"), _step(2, "action_agent")]}}
        yield {"action_agent": {"pending_tool_calls": [{"name": "transfer_funds_tool", "args": {"amount": 5}}]}}
        observer = pdp_decision_observer.get()
        if observer:
            observer({"input": {"amount": 5}, "decision": {"allow": True, "reason": "Fast-Path"}})
        yield {"pep_node": {"status": "COMPLETED", "approval_status": "APPROVED", "final_response": "ok"}}

    return stream


def test_builder_marks_replanned_steps_and_records_stages():
    b = TimelineBuilder()
    for chunk in _stream()():
        for node, update in chunk.items():
            b.add_update(node, update)
    stages = [e["stage"] for e in b.events]
    assert stages == [
        "sanitizer",
        "document_requested",
        "plan",
        "document_ingested",
        "replan",
        "tool_call_staged",
        "pep_decision",
        "result",
    ]
    replan = [e for e in b.events if e["stage"] == "replan"]
    assert len(replan) == 1 and replan[0]["added_by_replan"] and replan[0]["data"]["target_node"] == "action_agent"
    assert not any(e["added_by_replan"] for e in b.events if e["stage"] == "plan")


def test_builder_records_hitl_pause():
    b = TimelineBuilder()
    b.add_update("__interrupt__", (MagicMock(value={"action": "APPROVAL_REQUIRED"}),))
    assert b.events[0]["stage"] == "hitl_pause"


def test_chat_response_includes_timeline_with_pdp_decision():
    with patch("app.main.financial_graph") as graph:
        graph.stream.side_effect = _stream()
        graph.get_state.return_value = MagicMock(values={"final_response": "listo", "execution_plan": []}, next=())
        resp = client.post("/api/chat", json={"message": "hola", "session_id": "s_tl", "document_name": "x.txt"})
    assert resp.status_code == 200
    events = resp.json()["timeline"]
    assert [e["stage"] for e in events][-2:] == ["pep_decision", "result"]
    pep = next(e for e in events if e["stage"] == "pep_decision")
    assert pep["data"]["pdp"]["decision"]["reason"] == "Fast-Path"
    assert any(e["added_by_replan"] for e in events)


def test_builder_skips_pep_node_when_no_transfer_was_staged():
    b = TimelineBuilder()
    b.add_update("pep_node", {"status": "COMPLETED", "approval_status": "NONE"})
    assert b.events == []


def test_pdp_observer_also_receives_the_decision_for_a_recipient_registration():
    from unittest.mock import MagicMock, patch

    from mcp_financial_action_server.storage import financial_storage

    from app.graph.nodes.pep import pep_node

    financial_storage.reset_state()
    seen = []
    allow = MagicMock(status_code=200)
    allow.json.return_value = {
        "result": {"allow": True, "require_hitl": False, "reason": "Herramienta sin movimiento de fondos"}
    }
    state = {
        "session_id": "sess_tl_reg",
        "user_id": "user_default_01",
        "pending_tool_calls": [
            {"name": "register_recipient_tool", "args": {"recipient_name": "X", "cbu": "0170099900000012345678"}}
        ],
    }

    token = pdp_decision_observer.set(seen.append)
    try:
        with patch("httpx.Client.post", return_value=allow):
            pep_node(state)
    finally:
        pdp_decision_observer.reset(token)
        financial_storage.reset_state()

    assert seen and seen[0]["input"]["tool_name"] == "register_recipient_tool"
