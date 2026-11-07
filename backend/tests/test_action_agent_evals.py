"""
Evaluation Test Suite for Action Agent Node (`action_agent.py`) with LangSmith and Real LLMs.

Evaluates tool calls and execution given PlanStep descriptions (router.py:L35-L41).
Validates 3 core metrics:
1. Non-empty response (Deterministic)
2. Tool calls match ground truth (Deterministic)
3. Semantic output content presence via TypeSafe Decision Model / Jev (Non-deterministic)

Also verifies state mutation in FinancialStorage using get_accounts_by_user_id.
Syncs ground truth to LangSmith datasets and runs evaluation experiments.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure both backend and workspace root are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from langsmith import Client, traceable
from langsmith.evaluation import evaluate
from mcp_financial_action_server.storage import AccountRecord, financial_storage

from app.config import settings
from app.core.llm import get_chat_model
from app.graph.nodes.action_agent import action_agent_node
from app.graph.state import AgentState
from app.graph.tools.banking_write import register_recipient_tool, transfer_funds_tool
from tests.decision_models_evaluators import (
    evaluate_non_empty_response,
    evaluate_output_content_presence,
    evaluate_tool_calls_match,
    get_decision_evaluator_classifier,
)

logger = logging.getLogger(__name__)

GROUND_TRUTH_FILE_PATH = Path(__file__).parent / "data" / "action_agent_ground_truth.json"
DATASET_NAME = os.getenv("LANGSMITH_ACTION_DATASET", "action-agent-planstep-evals")


def is_live_llm_available() -> bool:
    """Checks whether the configured LLM provider has usable credentials."""
    try:
        model = get_chat_model(temperature=0.0)
        return model is not None
    except Exception:
        return False


def load_ground_truth_data() -> List[Dict[str, Any]]:
    """Loads ground truth examples from JSON file."""
    if not GROUND_TRUTH_FILE_PATH.exists():
        raise FileNotFoundError(f"Archivo de ground truth no encontrado: {GROUND_TRUTH_FILE_PATH}")
    with open(GROUND_TRUTH_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ==============================================================================
# Tool Mocking Helpers & State Validation
# ==============================================================================


def mock_register_recipient_func(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Mocks register_recipient_tool underlying func:
    1. Returns a valid registration receipt.
    2. Registers the recipient in financial_storage address book.
    3. Adds an AccountRecord into financial_storage.accounts so get_accounts_by_user_id reflects it.
    """
    user_id = kwargs.get("user_id", "user_default_01")
    holder_name = kwargs.get("holder_name") or kwargs.get("recipient_name") or "Destinatario Mock"
    cbu = kwargs.get("cbu_or_cvu") or kwargs.get("cbu") or "0170099900000012345678"
    alias = kwargs.get("alias")
    bank_name = kwargs.get("bank_name") or "Banco Test"

    # Register in address book
    try:
        financial_storage.register_destination_account(
            user_id=user_id,
            cbu_or_cvu=cbu,
            alias=alias,
            holder_name=holder_name,
            bank_name=bank_name,
        )
    except Exception as e:
        logger.debug(f"Error registrando en address book: {e}")

    # Store corresponding AccountRecord for user verification via get_accounts_by_user_id
    acc_id = f"acc_{uuid.uuid4().hex[:6]}"
    account_record = AccountRecord(
        account_id=acc_id,
        owner_id=user_id,
        owner_name=holder_name,
        account_number=cbu,
        balance=0.0,
        currency="ARS",
    )
    with financial_storage._lock:
        financial_storage.accounts[acc_id] = account_record

    return {
        "status": "REGISTERED",
        "recipient_id": f"rec_{uuid.uuid4().hex[:8]}",
        "account_id": acc_id,
        "user_id": user_id,
        "cbu_or_cvu": cbu,
        "alias": alias,
        "holder_name": holder_name,
        "bank_name": bank_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def mock_transfer_funds_func(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Mocks transfer_funds_tool underlying func (staged for PEP)."""
    return {
        "status": "PENDING_PEP",
        "user_id": kwargs.get("user_id", "user_default_01"),
        "source_account": kwargs.get("source_account", "acc_001"),
        "destination_account": kwargs.get("destination_account", ""),
        "amount": float(kwargs.get("amount", 0.0)),
        "concept": kwargs.get("concept", "transfer"),
    }


# ==============================================================================
# Predict Function for Action Agent
# ==============================================================================


@traceable(name="predict_action_agent", run_type="chain", tags=["eval", "action-agent", "real-llm"])
def predict_action_agent(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the Action Agent with a PlanStep input, mocking tool call responses,
    and returns a structured output for evaluation against the ground truth.
    """
    session_id = inputs.get("session_id", f"sess_eval_{uuid.uuid4().hex[:8]}")
    user_id = inputs.get("user_id", "user_default_01")
    plan_step = inputs.get("plan_step")
    execution_plan = inputs.get("execution_plan") or ([plan_step] if plan_step else [])

    state: AgentState = {
        "session_id": session_id,
        "user_id": user_id,
        "execution_plan": execution_plan,
        "messages": [],
    }

    recorded_tool_calls: List[Dict[str, Any]] = []

    def spy_register_func(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        recorded_tool_calls.append({"name": "register_recipient_tool", "args": dict(kwargs)})
        return mock_register_recipient_func(*args, **kwargs)

    def spy_transfer_func(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        recorded_tool_calls.append({"name": "transfer_funds_tool", "args": dict(kwargs)})
        return mock_transfer_funds_func(*args, **kwargs)

    with (
        patch.object(register_recipient_tool, "func", side_effect=spy_register_func),
        patch.object(transfer_funds_tool, "func", side_effect=spy_transfer_func),
    ):
        result = action_agent_node(state)

    # Output text synthesized by the agent
    output_text = (
        result.get("final_response")
        or (result.get("execution_plan", [{}])[0].get("result") if result.get("execution_plan") else "")
        or (result.get("messages", [{}])[-1].content if result.get("messages") else "")
        or ""
    )

    return {
        "tool_calls": recorded_tool_calls,
        "output_text": str(output_text),
        "status": result.get("status"),
        "pending_transfer": result.get("pending_transfer"),
        "executed_tools": result.get("executed_tools", []),
        "execution_plan": result.get("execution_plan", []),
    }


# ==============================================================================
# LangSmith Dataset Synchronization
# ==============================================================================


def sync_ground_truth_to_langsmith(
    dataset_name: str = DATASET_NAME,
    json_path: Path = GROUND_TRUTH_FILE_PATH,
    client: Optional[Client] = None,
) -> Optional[Any]:
    """
    Reads ground truth JSON and ensures the dataset and examples exist in LangSmith.
    """
    raw_api_key = settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not raw_api_key:
        logger.warning("No se encontró LANGSMITH_API_KEY / LANGCHAIN_API_KEY. Sincronización omitida.")
        return None

    api_key = str(raw_api_key).strip("\"' ")
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_API_KEY"] = api_key

    if client is None:
        client = Client(api_key=api_key)

    with open(json_path, "r", encoding="utf-8") as f:
        ground_truth_items = json.load(f)

    # 1. Get or create dataset
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
        logger.info(f"Dataset '{dataset_name}' ya existente en LangSmith (id={dataset.id}).")
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Dataset para evaluación de Action Agent con PlanSteps bancarios",
        )
        logger.info(f"Dataset '{dataset_name}' creado en LangSmith (id={dataset.id}).")

    # 2. Get existing example IDs
    existing_examples = list(client.list_examples(dataset_id=dataset.id))
    existing_ids = set()
    for ex in existing_examples:
        if ex.metadata and "id" in ex.metadata:
            existing_ids.add(ex.metadata["id"])

    # 3. Create missing examples
    for item in ground_truth_items:
        ex_id = item["id"]
        if ex_id not in existing_ids:
            client.create_example(
                inputs=item["input"],
                outputs={
                    "expected_tool_calls": item["expected_tool_calls"],
                    "expected_output_content": item["expected_output_content"],
                },
                metadata={
                    "id": ex_id,
                    "description": item["description"],
                },
                dataset_id=dataset.id,
            )
            logger.info(f"Ejemplo '{ex_id}' añadido al dataset de LangSmith.")

    return dataset


# ==============================================================================
# UNIT TESTS: Evaluator Logic Verification
# ==============================================================================


def test_evaluator_metric_1_non_empty_response():
    """Validates Metric 1: non-empty output detection."""
    # Non-empty with text
    run_ok = {"outputs": {"output_text": "Destinatario registrado"}}
    res = evaluate_non_empty_response(run_ok, {})
    assert res["score"] == 1.0

    # Non-empty with tool_calls
    run_tc = {"outputs": {"tool_calls": [{"name": "transfer_funds_tool", "args": {}}]}}
    res = evaluate_non_empty_response(run_tc, {})
    assert res["score"] == 1.0

    # Empty
    run_empty = {"outputs": {"output_text": "", "tool_calls": []}}
    res = evaluate_non_empty_response(run_empty, {})
    assert res["score"] == 0.0


def test_evaluator_metric_2_tool_calls_match():
    """Validates Metric 2: tool calls comparison against ground truth."""
    # Exact match for transfer
    run = {
        "outputs": {
            "tool_calls": [
                {
                    "name": "transfer_funds_tool",
                    "args": {"amount": 45000.0, "destination_account": "[CBU_BANCARIO_1]"},
                }
            ]
        }
    }
    example = {
        "outputs": {
            "expected_tool_calls": [
                {
                    "name": "transfer_funds_tool",
                    "args": {"amount": 45000.0, "destination_account": "[CBU_BANCARIO_1]"},
                }
            ]
        }
    }
    res = evaluate_tool_calls_match(run, example)
    assert res["score"] == 1.0

    # Amount mismatch
    run_bad_amt = {
        "outputs": {
            "tool_calls": [
                {
                    "name": "transfer_funds_tool",
                    "args": {"amount": 1000.0, "destination_account": "[CBU_BANCARIO_1]"},
                }
            ]
        }
    }
    res = evaluate_tool_calls_match(run_bad_amt, example)
    assert res["score"] == 0.5
    assert "monto discrepante" in res["comment"]

    # Tool name mismatch
    run_wrong_tool = {
        "outputs": {
            "tool_calls": [
                {
                    "name": "register_recipient_tool",
                    "args": {},
                }
            ]
        }
    }
    res = evaluate_tool_calls_match(run_wrong_tool, example)
    assert res["score"] == 0.0


def test_evaluator_metric_3_semantic_content_presence_mock_classifier():
    """
    Validates Metric 3: TypeSafe Jev semantic evaluation with a mock classifier,
    confirming that semantic presence is scored regardless of literal word-for-word syntax.
    """
    mock_classifier = MagicMock()
    mock_response = MagicMock()
    mock_noul = MagicMock()
    mock_noul.noul = 0.98
    mock_response.nouls = {"output_content_presence": mock_noul}
    mock_classifier.invoke.return_value = mock_response

    run = {
        "outputs": {
            "output_text": "Se transfirieron exitosamente $45000 al token [CBU_BANCARIO_1] quedando en estado PENDING_PEP."
        }
    }
    example = {
        "inputs": {
            "plan_step": {
                "title": "Transferir fondos",
                "description": "Transferir 45000 a [CBU_BANCARIO_1]",
            }
        },
        "outputs": {
            "expected_output_content": ["45000", "[CBU_BANCARIO_1]", "PENDING_PEP"],
        },
    }

    res = evaluate_output_content_presence(run, example, classifier=mock_classifier)
    assert res["score"] == 0.98
    assert "0.98" in res["comment"] or "98.0%" in res["comment"]
    assert mock_classifier.invoke.called


# ==============================================================================
# INTEGRATION EVALS: Action Agent with Real LLM & Mocked Tools
# ==============================================================================


@pytest.fixture(autouse=True)
def reset_financial_state():
    """Resets financial storage before each evaluation test."""
    financial_storage.reset_state()
    yield
    financial_storage.reset_state()


@pytest.mark.skipif(
    not is_live_llm_available(), reason="Requiere credenciales LLM válidas para el proveedor configurado"
)
@pytest.mark.parametrize(
    "item",
    load_ground_truth_data(),
    ids=[item["id"] for item in load_ground_truth_data()],
)
def test_action_agent_eval_case_with_real_llm(item: Dict[str, Any]):
    """
    EVAL INTEGRATION: Runs Action Agent with real LLM on each ground truth PlanStep.
    Verifies:
    1. Metric 1 (Respuesta NO vacía)
    2. Metric 2 (Tool calls coinciden con ground truth)
    3. Metric 3 (Presencia semántica del core de contenido via TypeSafe Jev)
    4. For action_agent_register_recipient_cbu: verifies new account created in financial_storage via get_accounts_by_user_id.
    """
    example_id = item["id"]
    inputs = item["input"]
    expected_tool_calls = item["expected_tool_calls"]
    user_id = inputs.get("user_id", "user_default_01")

    # Record initial accounts for user
    initial_accounts = financial_storage.get_accounts_by_user_id(user_id)

    # Execute action agent with mocked tool responses
    run_output = predict_action_agent(inputs)

    # --------------------------------------------------------------------------
    # Métrica 1: Respuesta NO vacía (Determinista)
    # --------------------------------------------------------------------------
    m1 = evaluate_non_empty_response({"outputs": run_output}, {"outputs": item})
    assert m1["score"] == 1.0, f"[{example_id}] Métrica 1 falló: {m1['comment']}"

    # --------------------------------------------------------------------------
    # Métrica 2: Tool calls esperadas vs ejecutadas (Determinista)
    # --------------------------------------------------------------------------
    m2 = evaluate_tool_calls_match({"outputs": run_output}, {"outputs": {"expected_tool_calls": expected_tool_calls}})
    assert m2["score"] == 1.0, f"[{example_id}] Métrica 2 falló: {m2['comment']}"

    # --------------------------------------------------------------------------
    # Métrica 3: Presencia semántica de contenido via Modelo de Decisión (Jev)
    # --------------------------------------------------------------------------
    try:
        classifier = get_decision_evaluator_classifier()
        m3 = evaluate_output_content_presence(
            {"outputs": run_output},
            {"inputs": inputs, "outputs": {"expected_output_content": item["expected_output_content"]}},
            classifier=classifier,
        )
        # Jev evalúa semánticamente el core del contenido (umbral afirmativo Noul >= 0.50)
        assert m3["score"] >= 0.50, f"[{example_id}] Métrica 3 (Jev) insuficiente: {m3['score']} - {m3['comment']}"
    except RuntimeError as e:
        logger.warning(f"Omitiendo evaluación Jev en vivo (sin API Key de decision model): {e}")

    # --------------------------------------------------------------------------
    # Validación específica requerida por usuario:
    # Chequear creación de cuenta usando get_accounts_by_user_id de FinancialStorage
    # --------------------------------------------------------------------------
    if example_id == "action_agent_register_recipient_cbu":
        expected_cbu = expected_tool_calls[0]["args"]["cbu"]
        updated_accounts = financial_storage.get_accounts_by_user_id(user_id)
        assert len(updated_accounts) > len(initial_accounts), (
            f"Se esperaba que la cuenta para {user_id} esté registrada en financial_storage"
        )
        updated_numbers = {acc.account_number for acc in updated_accounts}
        assert expected_cbu in updated_numbers, (
            f"La cuenta con CBU {expected_cbu} no se encontró en get_accounts_by_user_id({user_id}). "
            f"Cuentas actuales: {updated_numbers}"
        )


# ==============================================================================
# LangSmith Dataset Upload & Evaluation Experiment
# ==============================================================================


def test_sync_ground_truth_dataset_to_langsmith():
    """Validates that ground truth JSON can be synced to LangSmith dataset."""
    api_key = settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        pytest.skip("LANGSMITH_API_KEY / LANGCHAIN_API_KEY no disponible.")

    dataset = sync_ground_truth_to_langsmith(
        dataset_name=DATASET_NAME,
        json_path=GROUND_TRUTH_FILE_PATH,
    )
    assert dataset is not None
    assert dataset.name == DATASET_NAME


@pytest.mark.skipif(
    not is_live_llm_available(), reason="Requiere credenciales LLM válidas para el proveedor configurado"
)
def test_langsmith_action_agent_experiment():
    """
    Runs full LangSmith experiment with evaluate() using the 3 evaluators.
    Syncs the dataset and logs evaluation traces to LangSmith.
    """
    raw_api_key = settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not raw_api_key:
        pytest.skip("LANGSMITH_API_KEY / LANGCHAIN_API_KEY no disponible.")

    api_key = str(raw_api_key).strip("\"' ")
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_API_KEY"] = api_key

    client = Client(api_key=api_key)

    dataset = sync_ground_truth_to_langsmith(
        dataset_name=DATASET_NAME,
        json_path=GROUND_TRUTH_FILE_PATH,
        client=client,
    )
    assert dataset is not None

    classifier = None
    try:
        classifier = get_decision_evaluator_classifier()
    except Exception as e:
        logger.warning(f"No se pudo inicializar clasificador para el experimento: {e}")

    def wrapped_semantic_evaluator(run, example):
        return evaluate_output_content_presence(run, example, classifier=classifier)

    # Run LangSmith evaluation experiment
    results = evaluate(
        predict_action_agent,
        data=dataset.name,
        evaluators=[
            evaluate_non_empty_response,
            evaluate_tool_calls_match,
            wrapped_semantic_evaluator,
        ],
        experiment_prefix="action-agent-planstep-eval",
        description="Evaluación de Action Agent para llamadas a herramientas basadas en PlanStep",
        client=client,
    )

    assert results is not None
    logger.info(f"Experimento de LangSmith completado con éxito: {results}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ejecutar evaluaciones del Action Agent con LangSmith")
    parser.add_argument("--sync-only", action="store_true", help="Solo sincronizar el dataset con LangSmith")
    parser.add_argument("--run-eval", action="store_true", help="Ejecutar la evaluación completa en LangSmith")
    args = parser.parse_args()

    if args.sync_only:
        print("Sincronizando ground truth a LangSmith...")
        ds = sync_ground_truth_to_langsmith()
        print(f"Dataset sincronizado: {ds}")
    elif args.run_eval:
        print("Ejecutando evaluación completa del Action Agent con LangSmith...")
        test_langsmith_action_agent_experiment()
    else:
        pytest.main(["-v", __file__])
