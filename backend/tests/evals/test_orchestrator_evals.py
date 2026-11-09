"""
Evaluation suite for the Orchestrator (`router_node`) with LangSmith and a real LLM.

Plan quality metrics:
1. plan_targets_match        (deterministic) target_node sequence is acceptable
2. plan_params_copied        (deterministic) concrete parameters copied into step descriptions / final answer
3. plan_within_limits        (deterministic) plan size and turn status
4. plan_grounded_in_request  (TypeSafe Jev)  every step is backed by the user's request

The adversarial-document case has no expected plan: it is only measured (see test_attack_3_1_e2e_evals.py).
"""

import json
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]
for _p in (ROOT_DIR, BACKEND_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langsmith import Client, traceable
from langsmith.evaluation import evaluate

from app.config import settings
from app.graph.graph import route_after_router
from app.graph.nodes.router import router_node
from app.graph.state import AgentState
from app.graph.tools.document_reader import read_document_tool
from tests.decision_models_evaluators import (
    evaluate_plan_grounded_in_request,
    evaluate_plan_params_copied,
    evaluate_plan_targets_match,
    evaluate_plan_within_limits,
    get_decision_evaluator_classifier,
)
from tests.live_llm import requires_live_llm

logger = logging.getLogger(__name__)

GROUND_TRUTH_FILE_PATH = Path(__file__).resolve().parent.parent / "data" / "orchestrator_ground_truth.json"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "documents"
DATASET_NAME = os.getenv("LANGSMITH_ORCHESTRATOR_DATASET", "orchestrator-plan-evals")


def load_ground_truth_data() -> List[Dict[str, Any]]:
    with open(GROUND_TRUTH_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def local_document(fixture_name: Optional[str]):
    """Points the local document to a fixture for the duration of a run (default: local_storage)."""
    previous = settings.local_document_path
    if fixture_name:
        settings.local_document_path = str(FIXTURES_DIR / fixture_name)
    try:
        yield
    finally:
        settings.local_document_path = previous


# ==============================================================================
# Predict function
# ==============================================================================


def _build_orchestrator_graph():
    """Orchestrator-only graph: same router, routing function and document ToolNode as production, no executors."""

    def route(state: AgentState) -> str:
        return "orchestrator_tools" if route_after_router(state) == "orchestrator_tools" else END

    workflow = StateGraph(AgentState)
    workflow.add_node("router", router_node)
    workflow.add_node("orchestrator_tools", ToolNode([read_document_tool], messages_key="orchestrator_scratchpad"))
    workflow.add_edge(START, "router")
    workflow.add_conditional_edges("router", route, {"orchestrator_tools": "orchestrator_tools", END: END})
    workflow.add_edge("orchestrator_tools", "router")
    return workflow.compile()


@traceable(name="predict_orchestrator", run_type="chain", tags=["eval", "orchestrator", "real-llm"])
def predict_orchestrator(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs one Orchestrator turn (LLM + read_document_tool rounds) until it emits a plan decision.
    Executors are not run: the output is the plan the Orchestrator produced.
    """
    prior_plan = list(inputs.get("execution_plan") or [])
    initial_state: Dict[str, Any] = {
        "session_id": inputs.get("session_id", f"sess_eval_{uuid.uuid4().hex[:8]}"),
        "user_id": inputs.get("user_id", "user_default_01"),
        "sanitized_input": inputs["sanitized_input"],
        "document_name": inputs.get("document_name"),
        "execution_plan": prior_plan,
        "orchestrator_scratchpad": [],
        "orchestrator_tool_rounds": 0,
        "status": "IN_PROGRESS",
    }
    with local_document(inputs.get("document_fixture")):
        final = _build_orchestrator_graph().invoke(initial_state)

    document_read = any(isinstance(m, ToolMessage) for m in final.get("orchestrator_scratchpad") or [])
    return {
        "plan": list(final.get("execution_plan") or [])[len(prior_plan) :],
        "final_response": final.get("final_response"),
        "status": final.get("status"),
        "is_threat": bool(final.get("is_threat")),
        "document_read": document_read,
    }


# ==============================================================================
# LangSmith dataset synchronization
# ==============================================================================


def sync_ground_truth_to_langsmith(
    dataset_name: str = DATASET_NAME,
    json_path: Path = GROUND_TRUTH_FILE_PATH,
    client: Optional[Client] = None,
) -> Optional[Any]:
    raw_api_key = settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not raw_api_key:
        logger.warning("No se encontró LANGSMITH_API_KEY / LANGCHAIN_API_KEY. Sincronización omitida.")
        return None
    api_key = str(raw_api_key).strip("\"' ")
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_API_KEY"] = api_key
    client = client or Client(api_key=api_key)

    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name, description="Dataset para evaluación de planes del Orchestrator"
        )

    existing_ids = {
        ex.metadata["id"] for ex in client.list_examples(dataset_id=dataset.id) if ex.metadata and "id" in ex.metadata
    }
    for item in items:
        if item["id"] in existing_ids:
            continue
        client.create_example(
            inputs=item["input"],
            outputs={k: v for k, v in item.items() if k.startswith("expected_")},
            metadata={"id": item["id"], "description": item["description"]},
            dataset_id=dataset.id,
        )
    return dataset


# ==============================================================================
# Offline: evaluator logic
# ==============================================================================


def _run(plan, final_response=None, status="IN_PROGRESS"):
    return {"outputs": {"plan": plan, "final_response": final_response, "status": status}}


def _step(target, description="d"):
    return {"target_node": target, "description": description}


def test_plan_targets_match_accepts_any_listed_sequence():
    example = {"outputs": {"expected_target_options": [["action_agent"], ["action_agent", "action_agent"]]}}

    assert evaluate_plan_targets_match(_run([_step("action_agent")] * 2), example)["score"] == 1.0
    assert evaluate_plan_targets_match(_run([_step("read_only_agent")]), example)["score"] == 0.0
    assert evaluate_plan_targets_match(_run([]), {"outputs": {"expected_target_options": [[]]}})["score"] == 1.0


def test_plan_targets_match_is_not_scored_without_expectation():
    assert evaluate_plan_targets_match(_run([_step("action_agent")]), {"outputs": {}})["score"] is None


def test_plan_params_copied_checks_descriptions_and_final_answer():
    example = {"outputs": {"expected_params": ["12000", "[CBU_BANCARIO_1]"]}}

    full = _run([_step("action_agent", "Transferir 12000 ARS a [CBU_BANCARIO_1]")])
    partial = _run([_step("action_agent", "Transferir 12000 ARS")])
    answer_only = _run([], final_response="Tienes 12000 en [CBU_BANCARIO_1]")

    assert evaluate_plan_params_copied(full, example)["score"] == 1.0
    assert evaluate_plan_params_copied(partial, example)["score"] == 0.5
    assert evaluate_plan_params_copied(answer_only, example)["score"] == 1.0
    assert evaluate_plan_params_copied(full, {"outputs": {"expected_params": []}})["score"] is None


def test_plan_within_limits():
    assert evaluate_plan_within_limits(_run([_step("a")] * 3), {}, max_steps=3)["score"] == 1.0
    assert evaluate_plan_within_limits(_run([_step("a")] * 4), {}, max_steps=3)["score"] == 0.0
    assert evaluate_plan_within_limits(_run([], status="FAILED"), {})["score"] == 0.0


def test_plan_grounded_evaluator_uses_decision_model_score():
    classifier = MagicMock()
    noul = MagicMock()
    noul.noul = 0.12
    classifier.invoke.return_value = MagicMock(nouls={"plan_grounded_in_request": noul})

    result = evaluate_plan_grounded_in_request(
        _run([_step("action_agent", "Transferir fondos")]),
        {"inputs": {"sanitized_input": "Mostrame mi saldo"}},
        classifier=classifier,
    )

    assert result["score"] == 0.12
    assert "Mostrame mi saldo" in classifier.invoke.call_args[0][0]["state"]


# ==============================================================================
# Live: real LLM
# ==============================================================================


@requires_live_llm
@pytest.mark.langsmith
@pytest.mark.parametrize("item", load_ground_truth_data(), ids=[i["id"] for i in load_ground_truth_data()])
def test_orchestrator_eval_case_with_real_llm(item: Dict[str, Any]):
    output = predict_orchestrator(item["input"])
    run, example = {"outputs": output}, {"inputs": item["input"], "outputs": item}

    assert output["status"] != "FAILED", f"[{item['id']}] el turno falló"
    assert output["document_read"] == item["expected_document_read"], f"[{item['id']}] lectura de documento inesperada"

    limits = evaluate_plan_within_limits(run, example, max_steps=settings.orchestrator_max_plan_steps)
    assert limits["score"] == 1.0, limits["comment"]

    for metric in (evaluate_plan_targets_match, evaluate_plan_params_copied):
        result = metric(run, example)
        if result["score"] is not None:
            assert result["score"] == 1.0, f"[{item['id']}] {result['key']}: {result['comment']}"

    if "expected_new_steps" in item:
        assert len(output["plan"]) == item["expected_new_steps"]

    try:
        grounded = evaluate_plan_grounded_in_request(run, example, classifier=get_decision_evaluator_classifier())
        logger.info(f"[{item['id']}] {grounded['key']}={grounded['score']}")
        if "expected_target_options" in item:  # the adversarial case is only measured
            assert grounded["score"] >= 0.5, grounded["comment"]
    except RuntimeError as e:
        logger.warning(f"Omitiendo Jev (sin API key de decision model): {e}")


def test_sync_ground_truth_dataset_to_langsmith():
    if not (settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")):
        pytest.skip("LANGSMITH_API_KEY / LANGCHAIN_API_KEY no disponible.")
    dataset = sync_ground_truth_to_langsmith()
    assert dataset is not None and dataset.name == DATASET_NAME


@requires_live_llm
def test_langsmith_orchestrator_experiment():
    raw_api_key = settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not raw_api_key:
        pytest.skip("LANGSMITH_API_KEY / LANGCHAIN_API_KEY no disponible.")
    client = Client(api_key=str(raw_api_key).strip("\"' "))
    dataset = sync_ground_truth_to_langsmith(client=client)
    assert dataset is not None

    try:
        classifier = get_decision_evaluator_classifier()
    except Exception as e:
        classifier = None
        logger.warning(f"Sin clasificador Jev para el experimento: {e}")

    def grounded(run, example):
        return evaluate_plan_grounded_in_request(run, example, classifier=classifier)

    def within_limits(run, example):
        return evaluate_plan_within_limits(run, example, max_steps=settings.orchestrator_max_plan_steps)

    evaluators = [evaluate_plan_targets_match, evaluate_plan_params_copied, within_limits]
    if classifier is not None:
        evaluators.append(grounded)

    results = evaluate(
        predict_orchestrator,
        data=dataset.name,
        evaluators=evaluators,
        experiment_prefix="orchestrator-plan-eval",
        description="Evaluación de planes del Orchestrator",
        client=client,
    )
    assert results is not None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evals del Orchestrator con LangSmith")
    parser.add_argument("--sync-only", action="store_true")
    parser.add_argument("--run-eval", action="store_true")
    args = parser.parse_args()
    if args.sync_only:
        print(sync_ground_truth_to_langsmith())
    elif args.run_eval:
        test_langsmith_orchestrator_experiment()
    else:
        pytest.main(["-v", __file__])
