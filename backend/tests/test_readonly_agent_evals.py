"""
Evaluation Suite for Read-Only Banking Agent Node (`readonly_agent.py`) with Real LLM.

Validates that:
1. The Real LLM (Groq / Gemini / NVIDIA / Vertex AI) correctly reasons over mocked PlanStep tasks.
2. The agent selects and invokes ONLY the intended tools (get_account_balances, get_recent_transactions, or both).
3. Tool arguments (user_id, limit) are correctly injected and extracted.
4. The active step in execution_plan is updated with the synthesized response and marked completed.
5. LangSmith observability and telemetry context (RunnableConfig, trace_id, tags, metadata) is preserved throughout execution.
"""

import logging
import os
import time
import uuid
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from langsmith import Client, traceable
from mcp_financial_action_server.storage import financial_storage

from app.config import settings
from app.core.llm import get_chat_model
from app.core.telemetry import build_trace_config
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


# Check whether real LLM provider credentials are present
def is_live_llm_available() -> bool:
    try:
        model = get_chat_model()
        return model is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_live_llm_available(),
    reason="Requiere credenciales válidas para el proveedor LLM configurado (Groq, Gemini, NVIDIA, etc.)",
)


@pytest.fixture(autouse=True)
def reset_banking_state():
    """Ensures MCP banking state is clean before each eval."""
    financial_storage.reset_state()
    yield
    financial_storage.reset_state()


@traceable(name="execute_readonly_eval_step", run_type="chain", tags=["eval", "readonly-agent", "real-llm"])
def run_readonly_eval(
    test_name: str, state: AgentState, expected_tools: List[str], user_id: str = "user_default_01"
) -> Dict[str, Any]:
    """
    Executes readonly_agent_node with LangSmith trace configuration,
    monitoring tool invocations and recording metrics.
    """
    session_id = state.get("session_id", f"sess_eval_{uuid.uuid4().hex[:8]}")

    config, trace_id = build_trace_config(
        session_id=session_id,
        user_id=user_id,
        endpoint="test_readonly_agent_evals",
        run_name=f"eval_{test_name}",
        extra_tags=["eval", "readonly-agent", "real-llm", f"test:{test_name}"],
        extra_metadata={
            "test_case": test_name,
            "expected_tools": expected_tools,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
        },
    )

    start_time = time.time()
    result = readonly_agent_node(state, config=config)
    elapsed_time = time.time() - start_time

    # Log evaluation feedback to LangSmith if API key is active
    api_key = settings.langchain_api_key or os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    if (settings.langchain_tracing_v2 or os.getenv("LANGCHAIN_TRACING_V2") == "true") and api_key:
        try:
            client = Client(api_key=api_key)
            client.create_feedback(
                run_id=trace_id,
                key="execution_time_seconds",
                score=round(elapsed_time, 3),
                comment=f"Evaluación completada en {elapsed_time:.2f}s",
            )
        except Exception as e:
            logger.debug(f"No se pudo enviar feedback a LangSmith: {e}")

    return {"node_output": result, "trace_id": trace_id, "config": config, "elapsed_seconds": elapsed_time}


# ==============================================================================
# TEST CASES: Mocked PlanStep Task Evaluations
# ==============================================================================


def test_eval_balances_task_calls_only_account_balances():
    """
    EVAL CASE 1: Balance Inquiry PlanStep
    - Task: Consultar saldos disponibles en todas las cuentas.
    - Expected Tool: get_account_balances (1 call, user_id match).
    - Forbidden Tool: get_recent_transactions (0 calls).
    - Validation: status COMPLETED, balances in final_response, plan step completed.
    """
    user_id = "user_default_01"
    mocked_step = {
        "step_id": 1,
        "title": "Consultar saldos de cuentas",
        "target_node": "readonly_agent",
        "description": "Consultar los saldos actuales de todas las cuentas bancarias asociadas al usuario.",
        "result": None,
    }
    state: AgentState = {
        "session_id": "sess_eval_balances_01",
        "user_id": user_id,
        "execution_plan": [mocked_step],
        "messages": [],
    }

    with patch.object(
        financial_storage, "get_account_balances", wraps=financial_storage.get_account_balances
    ) as spy_balances:
        with patch.object(
            financial_storage, "get_recent_transactions", wraps=financial_storage.get_recent_transactions
        ) as spy_txs:
            eval_res = run_readonly_eval(
                test_name="balances_inquiry", state=state, expected_tools=["get_account_balances"], user_id=user_id
            )

    output = eval_res["node_output"]

    # 1. Verify Tool Calls
    assert spy_balances.call_count == 1, (
        f"get_account_balances debió llamarse 1 vez, se llamó {spy_balances.call_count}"
    )
    assert spy_txs.call_count == 0, f"get_recent_transactions no debió llamarse, se llamó {spy_txs.call_count}"
    assert spy_balances.call_args.kwargs.get("user_id") == user_id

    # 2. Verify Node Output Structure
    assert output["status"] == "COMPLETED"
    assert output["final_response"] is not None and len(output["final_response"]) > 0

    # 3. Verify Financial Content in Response
    final_text = output["final_response"]
    assert "485,250.75" in final_text or "485250" in final_text or "CA-001293847-ARS" in final_text
    assert "1,520" in final_text or "1520" in final_text or "USD" in final_text

    # 4. Verify Execution Plan Completion
    completed_plan = output["execution_plan"]
    assert len(completed_plan) == 1
    assert completed_plan[0]["step_id"] == 1
    assert completed_plan[0]["result"] == final_text

    # 5. Verify Messages history
    assert len(output["messages"]) == 1
    assert "[Read-Only Agent]:" in output["messages"][0].content


def test_eval_transactions_task_calls_only_recent_transactions():
    """
    EVAL CASE 2: Recent Transactions PlanStep
    - Task: Consultar últimos movimientos y compras recientes.
    - Expected Tool: get_recent_transactions (>= 1 call, user_id match).
    - Forbidden Tool: get_account_balances (0 calls).
    - Validation: status COMPLETED, transactions detailed in response, plan step completed.
    """
    user_id = "user_default_01"
    mocked_step = {
        "step_id": 1,
        "title": "Consultar últimos movimientos bancarios",
        "target_node": "readonly_agent",
        "description": "Obtener y listar los últimos 5 movimientos y compras realizadas por el usuario.",
        "result": None,
    }
    state: AgentState = {
        "session_id": "sess_eval_txs_01",
        "user_id": user_id,
        "execution_plan": [mocked_step],
        "messages": [],
    }

    with patch.object(
        financial_storage, "get_account_balances", wraps=financial_storage.get_account_balances
    ) as spy_balances:
        with patch.object(
            financial_storage, "get_recent_transactions", wraps=financial_storage.get_recent_transactions
        ) as spy_txs:
            eval_res = run_readonly_eval(
                test_name="recent_transactions",
                state=state,
                expected_tools=["get_recent_transactions"],
                user_id=user_id,
            )

    output = eval_res["node_output"]

    # 1. Verify Tool Calls
    assert spy_txs.call_count >= 1, (
        f"get_recent_transactions debió llamarse al menos 1 vez, se llamó {spy_txs.call_count}"
    )
    assert spy_balances.call_count == 0, f"get_account_balances no debió llamarse, se llamó {spy_balances.call_count}"
    assert spy_txs.call_args.kwargs.get("user_id") == user_id

    # 2. Verify Output
    assert output["status"] == "COMPLETED"
    final_text = output["final_response"]
    assert final_text is not None and len(final_text) > 0

    # 3. Verify Transaction Content in Response
    assert any(
        term in final_text
        for term in ["Depósito", "Haberes", "Coto", "Supermercado", "Servicios", "tx_01", "tx_02", "350000", "15400"]
    )

    # 4. Verify Plan Completion
    completed_plan = output["execution_plan"]
    assert completed_plan[0]["result"] == final_text


def test_eval_compound_task_calls_both_tools():
    """
    EVAL CASE 3: Compound PlanStep (Balances + Transactions)
    - Task: Consultar saldos y movimientos para conciliación contable.
    - Expected Tools: BOTH get_account_balances AND get_recent_transactions.
    - Validation: Both tools invoked, synthesis includes both sections, plan completed.
    """
    user_id = "user_default_01"
    mocked_step = {
        "step_id": 1,
        "title": "Auditoría integral de saldos y movimientos",
        "target_node": "readonly_agent",
        "description": "Consultar los saldos disponibles en todas las cuentas y también los últimos movimientos bancarios para conciliar el estado de la cuenta.",
        "result": None,
    }
    state: AgentState = {
        "session_id": "sess_eval_compound_01",
        "user_id": user_id,
        "execution_plan": [mocked_step],
        "messages": [],
    }

    with patch.object(
        financial_storage, "get_account_balances", wraps=financial_storage.get_account_balances
    ) as spy_balances:
        with patch.object(
            financial_storage, "get_recent_transactions", wraps=financial_storage.get_recent_transactions
        ) as spy_txs:
            eval_res = run_readonly_eval(
                test_name="compound_balances_and_transactions",
                state=state,
                expected_tools=["get_account_balances", "get_recent_transactions"],
                user_id=user_id,
            )

    output = eval_res["node_output"]

    # 1. Verify Both Tools Called
    assert spy_balances.call_count >= 1, "get_account_balances debió invocarse en tarea compuesta"
    assert spy_txs.call_count >= 1, "get_recent_transactions debió invocarse en tarea compuesta"

    # 2. Verify Output Content
    assert output["status"] == "COMPLETED"
    final_text = output["final_response"]
    # Should include balance info
    assert any(term in final_text for term in ["485,250", "485250", "1,520", "1520", "ARS", "USD", "CA-001293847-ARS"])
    # Should include transaction info
    assert any(term in final_text for term in ["Depósito", "Haberes", "Coto", "Supermercado", "Servicios", "tx_"])


def test_eval_multi_step_plan_completes_only_active_step():
    """
    EVAL CASE 4: Multi-Step Execution Plan (Step 2 is Active)
    - Plan has 3 steps: Step 1 (already completed), Step 2 (readonly_agent, active), Step 3 (action_agent, pending).
    - Validation:
      * Only Step 2 is processed and completed.
      * Step 1 retains its previous result.
      * Step 3 remains with result=None.
    """
    user_id = "user_default_01"
    plan = [
        {
            "step_id": 1,
            "title": "Lectura y análisis de comprobante",
            "target_node": "router",
            "description": "Verificar liquidación adjunta",
            "result": "Comprobante verificado con éxito",
        },
        {
            "step_id": 2,
            "title": "Consultar saldo disponible en cuentas",
            "target_node": "readonly_agent",
            "description": "Consultar los saldos disponibles para verificar fondos antes de operar.",
            "result": None,
        },
        {
            "step_id": 3,
            "title": "Emitir transferencia bancaria",
            "target_node": "action_agent",
            "description": "Transferir fondos por $25,000 al proveedor",
            "result": None,
        },
    ]

    state: AgentState = {
        "session_id": "sess_eval_multistep_01",
        "user_id": user_id,
        "execution_plan": plan,
        "messages": [],
    }

    with patch.object(
        financial_storage, "get_account_balances", wraps=financial_storage.get_account_balances
    ) as spy_balances:
        eval_res = run_readonly_eval(
            test_name="multistep_active_step_2", state=state, expected_tools=["get_account_balances"], user_id=user_id
        )

    output = eval_res["node_output"]
    completed_plan = output["execution_plan"]

    assert spy_balances.call_count == 1
    assert output["status"] == "COMPLETED"

    # Step 1 unchanged
    assert completed_plan[0]["step_id"] == 1
    assert completed_plan[0]["result"] == "Comprobante verificado con éxito"

    # Step 2 completed
    assert completed_plan[1]["step_id"] == 2
    assert completed_plan[1]["result"] == output["final_response"]
    assert len(completed_plan[1]["result"]) > 0

    # Step 3 still pending
    assert completed_plan[2]["step_id"] == 3
    assert completed_plan[2]["result"] is None


def test_eval_langsmith_telemetry_and_config_propagation():
    """
    EVAL CASE 5: LangSmith Telemetry & Context Propagation
    - Verifies that RunnableConfig generated by build_trace_config is properly
      passed through readonly_agent_node down to the sub-agent ReAct executor.
    - Validates trace_id generation, tag assignment, and user_id isolation.
    """
    session_id = "sess_telemetry_eval_01"
    user_id = "user_auditor_99"

    # Seed an account for user_auditor_99 to verify data isolation
    from mcp_financial_action_server.storage import AccountRecord

    financial_storage.accounts["acc_audit_01"] = AccountRecord(
        account_id="acc_audit_01",
        owner_id=user_id,
        owner_name="Auditor General",
        account_number="CA-9999-ARS",
        balance=75000.0,
        currency="ARS",
    )

    mocked_step = {
        "step_id": 1,
        "title": "Verificación de saldos auditor",
        "target_node": "readonly_agent",
        "description": "Consultar saldos de cuentas para auditoría de seguridad.",
        "result": None,
    }
    state: AgentState = {
        "session_id": session_id,
        "user_id": user_id,
        "execution_plan": [mocked_step],
        "messages": [],
    }

    config, trace_id = build_trace_config(
        session_id=session_id,
        user_id=user_id,
        endpoint="test_telemetry_propagation",
        run_name="eval_telemetry_propagation",
        extra_tags=["eval", "telemetry-check"],
        extra_metadata={"audit_purpose": "langsmith_verification"},
    )

    # Validate trace config structure
    assert uuid.UUID(trace_id)
    assert config["configurable"]["thread_id"] == session_id
    assert "eval" in config["tags"]
    assert config["metadata"]["session_id"] == session_id
    assert config["metadata"]["trace_id"] == trace_id

    # Execute with explicit config
    with patch.object(
        financial_storage, "get_account_balances", wraps=financial_storage.get_account_balances
    ) as spy_balances:
        output = readonly_agent_node(state, config=config)

    assert output["status"] == "COMPLETED"
    assert spy_balances.call_count == 1
    # User ID must be passed securely to the tool call
    assert spy_balances.call_args.kwargs.get("user_id") == user_id


# ==============================================================================
# Standalone Evaluation Runner CLI
# ==============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("🚀 Iniciando Suite de Evaluación para readonly_agent con LLM Real")
    print(f"   Proveedor: {settings.llm_provider.upper()} | Modelo: {settings.llm_model}")
    print("=" * 80)

    test_cases = [
        ("Balances Task", test_eval_balances_task_calls_only_account_balances),
        ("Transactions Task", test_eval_transactions_task_calls_only_recent_transactions),
        ("Compound Task", test_eval_compound_task_calls_both_tools),
        ("Multi-step Plan", test_eval_multi_step_plan_completes_only_active_step),
        ("Telemetry Propagation", test_eval_langsmith_telemetry_and_config_propagation),
    ]

    passed = 0
    total = len(test_cases)

    for name, test_func in test_cases:
        print(f"\n[EVAL] Ejecutando: {name}...")
        start = time.time()
        try:
            test_func()
            duration = time.time() - start
            print(f"✅ PASSED ({duration:.2f}s)")
            passed += 1
        except Exception as exc:
            duration = time.time() - start
            print(f"❌ FAILED ({duration:.2f}s): {exc}")

    print("\n" + "=" * 80)
    print(f"📊 Resumen de Evaluaciones: {passed}/{total} pruebas superadas ({(passed / total) * 100:.1f}%)")
    print("=" * 80)
