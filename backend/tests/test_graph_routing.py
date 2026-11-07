"""Offline tests for the Orchestrator dispatch logic in graph.py."""

from langchain_core.messages import AIMessage
from langgraph.graph import END

from app.graph.graph import route_after_router


def _step(target, result=None, step_id=1):
    return {"step_id": step_id, "title": "t", "target_node": target, "description": "d", "result": result}


def test_routes_pending_step_to_executor_via_registry():
    assert route_after_router({"execution_plan": [_step("read_only_agent")]}) == "readonly_agent"
    assert route_after_router({"execution_plan": [_step("action_agent")]}) == "action_agent"


def test_routes_first_pending_step_only():
    plan = [_step("read_only_agent", result="ok", step_id=1), _step("action_agent", step_id=2)]
    assert route_after_router({"execution_plan": plan}) == "action_agent"


def test_ends_when_no_pending_step():
    assert route_after_router({"execution_plan": []}) == END
    assert route_after_router({"execution_plan": [_step("action_agent", result="done")]}) == END


def test_ends_on_unknown_target_or_threat():
    assert route_after_router({"execution_plan": [_step("nope")]}) == END
    assert route_after_router({"is_threat": True, "execution_plan": [_step("action_agent")]}) == END
    assert route_after_router({"status": "FAILED", "execution_plan": [_step("action_agent")]}) == END


def test_pending_document_tool_call_goes_to_tool_node():
    call = AIMessage(content="", tool_calls=[{"name": "read_document_tool", "args": {"filename": "x"}, "id": "1"}])
    assert route_after_router({"orchestrator_scratchpad": [call], "execution_plan": []}) == "orchestrator_tools"
