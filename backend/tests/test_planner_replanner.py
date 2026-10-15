"""
Tests for Planner + Replanner Multi-Agent Architecture.
Validates:
1. Pydantic schemas: Task, Plan, PlanState, ReplannerDecision.
2. Context window optimization and compaction in ContextOptimizer.
3. Planner Node strictly relying on LLM structured output without deterministic fallbacks.
4. Replanner Node handling CONTINUE, REPLAN, FINISH and HITL rejections.
5. Fault-tolerant PostgreSQL Checkpointer initialization & fallback.
6. Full end-to-end integration through LangGraph.
"""
from unittest.mock import MagicMock, patch
import pytest
from langchain_core.messages import HumanMessage

from app.graph.schemas import Task, Plan, PlanState, ReplannerDecision
from app.graph.context_optimizer import ContextOptimizer, context_optimizer
from app.graph.nodes.planner import planner_node
from app.graph.nodes.replanner import replanner_node
from app.db.checkpointer import get_checkpointer
from app.config import settings
from app.graph.graph import financial_graph


# ==============================================================================
# 1. Schemas Validation Tests
# ==============================================================================

def test_task_schema_creation_and_dependencies():
    task1 = Task(
        id=1,
        dependencies=[],
        description="Consultar balance de cuenta",
        assigned_tool="DB_Query"
    )
    task2 = Task(
        id=2,
        dependencies=[1],
        description="Transferir fondos si hay saldo suficiente",
        assigned_tool="Transfer_API"
    )
    assert task1.id == 1
    assert task2.dependencies == [1]
    assert task2.assigned_tool == "Transfer_API"


def test_plan_schema_with_tasks():
    plan = Plan(
        steps=[
            Task(id=1, dependencies=[], description="Obtener extracto", assigned_tool="DB_Query"),
            Task(id=2, dependencies=[1], description="Generar reporte", assigned_tool="Analytics_API")
        ]
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].id == 1
    assert plan.steps[1].id == 2


def test_replanner_decision_schema():
    decision_finish = ReplannerDecision(
        action="FINISH",
        explanation="Objetivo cumplido",
        final_response="Transacción completada"
    )
    assert decision_finish.action == "FINISH"
    assert decision_finish.new_plan is None

    decision_replan = ReplannerDecision(
        action="REPLAN",
        explanation="Saldo insuficiente, se requiere consultar otra cuenta",
        new_plan=Plan(
            steps=[Task(id=1, dependencies=[], description="Consultar cuenta secundaria", assigned_tool="DB_Query")]
        )
    )
    assert decision_replan.action == "REPLAN"
    assert len(decision_replan.new_plan.steps) == 1


# ==============================================================================
# 2. Context Window Optimizer Tests
# ==============================================================================

def test_context_optimizer_truncates_large_results():
    optimizer = ContextOptimizer(max_chars_per_result=100)
    huge_text = "A" * 300
    truncated = optimizer.truncate_result(huge_text)
    assert len(truncated) <= 150
    assert "... [truncado por límite de contexto] ..." in truncated


def test_context_optimizer_summarizes_old_steps_when_window_exceeded():
    optimizer = ContextOptimizer(max_chars_per_result=200, recent_window_size=2)
    past_steps = [
        {"task_id": 1, "description": "Tarea 1", "assigned_tool": "ToolA", "result": "Res 1", "status": "COMPLETED"},
        {"task_id": 2, "description": "Tarea 2", "assigned_tool": "ToolB", "result": "Res 2", "status": "COMPLETED"},
        {"task_id": 3, "description": "Tarea 3", "assigned_tool": "ToolC", "result": "Res 3", "status": "COMPLETED"},
        {"task_id": 4, "description": "Tarea 4", "assigned_tool": "ToolD", "result": "Res 4", "status": "COMPLETED"},
    ]
    formatted = optimizer.format_past_steps(past_steps)
    # Must indicate earlier steps were summarized
    assert "pasos anteriores completados" in formatted
    # Must retain details of recent steps (3 and 4)
    assert "Tarea #3" in formatted
    assert "Tarea #4" in formatted


# ==============================================================================
# 3. Planner Agent Tests (Strictly LLM - Zero Deterministic Fallback)
# ==============================================================================

def test_planner_node_blocks_threats():
    state = {"is_threat": True, "sanitized_input": "ataque"}
    res = planner_node(state)
    assert res["status"] == "BLOCKED"
    assert res["plan_completed"] is True
    assert res["plan"] == []


def test_planner_node_raises_error_when_llm_is_unavailable():
    """Confirms no deterministic fallback: must fail explicitly if LLM is unavailable."""
    with patch("app.graph.nodes.planner.get_chat_model", return_value=None):
        state = {"is_threat": False, "sanitized_input": "Ver mis saldos"}
        with pytest.raises(ValueError, match="LLM no disponible"):
            planner_node(state)


def test_planner_node_formulates_plan_with_structured_llm():
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    expected_plan = Plan(
        steps=[
            Task(id=1, dependencies=[], description="Consultar saldo", assigned_tool="DB_Query"),
            Task(id=2, dependencies=[1], description="Transferir fondos", assigned_tool="Transfer_API")
        ]
    )
    mock_structured.invoke.return_value = expected_plan
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("app.graph.nodes.planner.get_chat_model", return_value=mock_llm):
        state = {"is_threat": False, "sanitized_input": "Ver saldo y enviar dinero"}
        res = planner_node(state)
        assert res["status"] == "IN_PROGRESS"
        assert res["plan_completed"] is False
        assert len(res["plan"]) == 2
        assert res["current_task_idx"] == 0
        assert res["plan"][0]["assigned_tool"] == "DB_Query"


# ==============================================================================
# 4. Replanner Agent Tests
# ==============================================================================

def test_replanner_node_halts_on_hitl_rejection():
    state = {
        "input": "Transferir 50000",
        "plan": [{"id": 1, "description": "Transferir dinero", "assigned_tool": "Transfer_API"}],
        "current_task_idx": 0,
        "past_steps": [],
        "approval_status": "REJECTED",
        "final_response": "Transferencia rechazada por oficial de cumplimiento"
    }
    res = replanner_node(state)
    assert res["plan_completed"] is True
    assert res["status"] == "COMPLETED"
    assert "rechazad" in res["final_response"].lower()
    assert len(res["past_steps"]) == 1
    assert res["past_steps"][0]["status"] == "REJECTED"


def test_replanner_node_handles_finish_decision():
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    decision = ReplannerDecision(
        action="FINISH",
        explanation="El saldo ha sido informado correctamente.",
        final_response="Tu saldo total es de $485,250.75 ARS."
    )
    mock_structured.invoke.return_value = decision
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("app.graph.nodes.replanner.get_chat_model", return_value=mock_llm):
        state = {
            "input": "Consultar saldo",
            "plan": [{"id": 1, "description": "Consultar saldo", "assigned_tool": "DB_Query"}],
            "current_task_idx": 0,
            "past_steps": [],
            "approval_status": "APPROVED",
            "final_response": "Saldo en cuenta: $485,250.75"
        }
        res = replanner_node(state)
        assert res["plan_completed"] is True
        assert res["status"] == "COMPLETED"
        assert res["final_response"] == "Tu saldo total es de $485,250.75 ARS."
        assert len(res["past_steps"]) == 1


def test_replanner_node_handles_continue_decision():
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    decision = ReplannerDecision(
        action="CONTINUE",
        explanation="Saldo verificado, continuar con la transferencia.",
        final_response=None
    )
    mock_structured.invoke.return_value = decision
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("app.graph.nodes.replanner.get_chat_model", return_value=mock_llm):
        state = {
            "input": "Consultar saldo y transferir",
            "plan": [
                {"id": 1, "description": "Consultar saldo", "assigned_tool": "DB_Query"},
                {"id": 2, "description": "Transferir dinero", "assigned_tool": "Transfer_API"}
            ],
            "current_task_idx": 0,
            "past_steps": [],
            "approval_status": "APPROVED",
            "final_response": "Saldo suficiente: $100,000"
        }
        res = replanner_node(state)
        assert res["plan_completed"] is False
        assert res["current_task_idx"] == 1
        assert res["current_intent"] == "transfer"
        assert len(res["past_steps"]) == 1


def test_replanner_node_handles_replan_decision():
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    new_plan = Plan(
        steps=[
            Task(id=10, dependencies=[], description="Solicitar confirmación de cuenta", assigned_tool="CRM_API")
        ]
    )
    decision = ReplannerDecision(
        action="REPLAN",
        explanation="Cuenta de origen no encontrada, consultar CRM.",
        new_plan=new_plan
    )
    mock_structured.invoke.return_value = decision
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("app.graph.nodes.replanner.get_chat_model", return_value=mock_llm):
        state = {
            "input": "Transferir dinero",
            "plan": [{"id": 1, "description": "Transferir", "assigned_tool": "Transfer_API"}],
            "current_task_idx": 0,
            "past_steps": [],
            "approval_status": "APPROVED",
            "final_response": "Error: cuenta no identificada"
        }
        res = replanner_node(state)
        assert res["plan_completed"] is False
        assert len(res["plan"]) == 1
        assert res["plan"][0]["id"] == 10
        assert res["plan"][0]["assigned_tool"] == "CRM_API"


# ==============================================================================
# 5. PostgreSQL Checkpointer Fault Tolerance Tests
# ==============================================================================

def test_checkpointer_falls_back_gracefully_when_postgres_unreachable():
    """When PostgreSQL is selected but unavailable, get_checkpointer falls back to MemorySaver."""
    with patch.object(settings, "checkpointer_type", "postgres"):
        with patch.object(settings, "postgres_uri", "postgresql://invalid_host:5432/db"):
            checkpointer_instance = get_checkpointer()
            assert checkpointer_instance is not None
            # MemorySaver fallback should be active without raising an unhandled exception
            from langgraph.checkpoint.memory import MemorySaver
            assert isinstance(checkpointer_instance, MemorySaver)


# ==============================================================================
# 6. End-to-End Multi-Agent Flow via financial_graph
# ==============================================================================

def test_financial_graph_planner_replanner_execution_cycle():
    """
    Tests complete cycle:
    1. Sanitizer -> Planner (creates 1-step balance plan via mocked LLM)
    2. ReadOnly Agent executes read
    3. Replanner receives response and finishes plan
    """
    session_id = "test_planner_replanner_graph_sess"
    config = {"configurable": {"thread_id": session_id}}

    mock_llm = MagicMock()
    
    # 1. Planner output
    mock_plan = Plan(
        steps=[
            Task(id=1, dependencies=[], description="Consultar saldos", assigned_tool="banking_read")
        ]
    )
    # 2. Replanner output
    mock_replanner_decision = ReplannerDecision(
        action="FINISH",
        explanation="Saldos informados exitosamente.",
        final_response="Estado de Cuentas y Saldos Disponibles: $485,250.75 ARS."
    )

    mock_structured = MagicMock()
    # First invoke is Planner (returns Plan), second invoke is Replanner (returns ReplannerDecision)
    mock_structured.invoke.side_effect = [mock_plan, mock_replanner_decision]
    mock_llm.with_structured_output.return_value = mock_structured

    initial_state = {
        "messages": [HumanMessage(content="¿Cuál es mi saldo actual?")],
        "raw_input": "¿Cuál es mi saldo actual?",
        "sanitized_input": "¿Cuál es mi saldo actual?",
        "session_id": session_id,
        "user_id": "user_default_01",
        "status": "IN_PROGRESS"
    }

    with patch("app.graph.nodes.planner.get_chat_model", return_value=mock_llm):
        with patch("app.graph.nodes.replanner.get_chat_model", return_value=mock_llm):
            result = financial_graph.invoke(initial_state, config=config)
            assert result["status"] == "COMPLETED"
            assert result["plan_completed"] is True
            assert "Saldos Disponibles" in result["final_response"]
            assert len(result["past_steps"]) >= 1
