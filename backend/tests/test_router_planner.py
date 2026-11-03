"""
Offline tests (mocked LLM) for the Orchestrator node (`router_node`).

They cover plan mechanics only: no document, amount or recipient from any threat scenario appears here.
Plan quality with a real LLM is measured in `test_orchestrator_evals.py`.
"""
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app.config import settings
from app.graph.nodes.router import FAILURE_MESSAGE, router_node


def _plan_update_msg(new_steps=None, final_answer=None) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "PlanUpdate",
            "args": {"new_steps": new_steps or [], "final_answer": final_answer},
            "id": "call_plan",
        }],
    )


def _document_call_msg(document_name="doc.txt") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "read_document_tool", "args": {"filename": document_name}, "id": "call_doc"}],
    )


def _llm_returning(message: AIMessage):
    """Returns (patcher-ready llm factory, bound mock) so tests can inspect the invoked messages."""
    bound = MagicMock()
    bound.invoke.return_value = message
    llm = MagicMock()
    llm.bind_tools.return_value = bound
    return llm, bound


def _step(step_id, target="read_only_agent", result=None):
    return {"step_id": step_id, "title": f"paso {step_id}", "target_node": target,
            "description": f"descripcion {step_id}", "result": result}


def _new_step(target="read_only_agent", title="t", description="d"):
    return {"title": title, "target_node": target, "description": description}


def _run(state, message):
    llm, bound = _llm_returning(message)
    with patch("app.graph.nodes.router.get_chat_model", return_value=llm):
        return router_node({"sanitized_input": "pedido", **state}), bound


def test_pending_step_does_not_call_llm():
    with patch("app.graph.nodes.router.get_chat_model", side_effect=AssertionError("LLM no debe invocarse")):
        assert router_node({"execution_plan": [_step(1)]}) == {}


def test_plan_update_appends_steps_with_sequential_ids_and_empty_result():
    update = [_new_step("read_only_agent", "Leer"), _new_step("action_agent", "Escribir")]
    res, _ = _run({}, _plan_update_msg(update))

    plan = res["execution_plan"]
    assert [s["step_id"] for s in plan] == [1, 2]
    assert [s["target_node"] for s in plan] == ["read_only_agent", "action_agent"]
    assert all(s["result"] is None for s in plan)


def test_replan_appends_after_existing_steps_without_modifying_them():
    existing = [_step(1, result="hecho")]
    res, _ = _run({"execution_plan": existing}, _plan_update_msg([_new_step("action_agent")]))

    assert res["execution_plan"][0] == existing[0]
    assert res["execution_plan"][1]["step_id"] == 2


def test_plan_update_is_truncated_to_max_plan_steps(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_max_plan_steps", 2)
    res, _ = _run({"execution_plan": [_step(1, result="ok")]}, _plan_update_msg([_new_step(), _new_step(), _new_step()]))

    assert len(res["execution_plan"]) == 2


def test_initial_prompt_when_plan_is_empty_and_replan_prompt_otherwise():
    _, bound = _run({}, _plan_update_msg())
    system_initial = bound.invoke.call_args[0][0][0].content
    _, bound = _run({"execution_plan": [_step(1, result="ok")]}, _plan_update_msg())
    system_replan = bound.invoke.call_args[0][0][0].content

    assert "Todavía no existe ningún plan" in system_initial
    assert "Ya existe un plan de ejecución" in system_replan


def test_all_steps_done_and_empty_update_closes_turn_with_final_answer():
    res, _ = _run({"execution_plan": [_step(1, result="saldo 100")]}, _plan_update_msg(final_answer="Tienes 100."))

    assert res["status"] == "COMPLETED"
    assert res["final_response"] == "Tienes 100."
    assert res["execution_plan"][0]["result"] == "saldo 100"


def test_empty_update_without_final_answer_fails_the_turn():
    res, _ = _run({"execution_plan": [_step(1, result="ok")]}, _plan_update_msg())

    assert res["status"] == "FAILED"
    assert res["final_response"] == FAILURE_MESSAGE


def test_tool_rounds_limit_fails_the_turn(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_max_tool_iterations", 2)
    res, _ = _run({"orchestrator_tool_rounds": 2}, _document_call_msg())

    assert res["status"] == "FAILED"
    assert res["final_response"] == FAILURE_MESSAGE


def test_missing_llm_credentials_fail_the_turn():
    with patch("app.graph.nodes.router.get_chat_model", return_value=None):
        res = router_node({"sanitized_input": "pedido"})

    assert res["status"] == "FAILED"


def test_failed_turn_does_not_call_llm_again():
    with patch("app.graph.nodes.router.get_chat_model", side_effect=AssertionError("LLM no debe invocarse")):
        assert router_node({"status": "FAILED"}) == {}


def test_empty_plan_with_final_answer_answers_directly():
    res, _ = _run({}, _plan_update_msg(final_answer="Hola"))

    assert res["status"] == "COMPLETED"
    assert res["final_response"] == "Hola"
    assert res["execution_plan"] == []


def test_document_tool_call_goes_to_scratchpad_and_counts_a_round():
    res, _ = _run({"document_name": "doc.txt"}, _document_call_msg())

    assert res["orchestrator_tool_rounds"] == 1
    assert res["orchestrator_scratchpad"][0].tool_calls[0]["name"] == "read_document_tool"
    assert "execution_plan" not in res


def test_blocked_document_short_circuits_as_threat():
    blocked = ToolMessage(
        content="bloqueado", tool_call_id="call_doc",
        artifact={"status": "BLOCKED", "threat_category": "PROMPT_INJECTION", "threat_reason": "x", "detected_pii": []},
    )
    with patch("app.graph.nodes.router.get_chat_model", side_effect=AssertionError("LLM no debe invocarse")):
        res = router_node({"orchestrator_scratchpad": [_document_call_msg(), blocked]})

    assert res["is_threat"] is True
    assert res["status"] == "BLOCKED"
    assert res["defense_action"] == "BLOCKED"


def test_document_pii_is_accumulated_in_audit_trail():
    pii = {"pii_type": "CBU", "token_assigned": "[CBU_BANCARIO_1]"}
    tool_msg = ToolMessage(content="ok", tool_call_id="call_doc",
                           artifact={"status": "OK", "detected_pii": [pii]})
    res, _ = _run(
        {"orchestrator_scratchpad": [_document_call_msg(), tool_msg], "orchestrator_tool_rounds": 1},
        _plan_update_msg([_new_step()]),
    )

    assert pii in res["detected_pii"]


def test_unknown_tool_fails_the_turn():
    res, _ = _run({}, AIMessage(content="", tool_calls=[{"name": "otra_tool", "args": {}, "id": "x"}]))

    assert res["status"] == "FAILED"


def test_context_tells_the_orchestrator_whether_a_document_is_attached():
    _, bound = _run({"document_name": "recibo.pdf"}, _plan_update_msg())
    with_doc = bound.invoke.call_args[0][0][1].content
    _, bound = _run({}, _plan_update_msg())
    without_doc = bound.invoke.call_args[0][0][1].content

    assert 'available="yes" filename="recibo.pdf"' in with_doc
    assert 'available="no"' in without_doc
