"""
Planner Agent Node.
Sole Responsibility: Formulates the initial structured Plan (List[Task]) using strictly LLM structured output.
Adheres to Clean Code principles and enforces zero deterministic fallback plans.
"""
import logging
from typing import Dict, Any
from langchain_core.messages import SystemMessage, HumanMessage

from app.graph.state import AgentState
from app.graph.schemas import Plan
from app.core.llm import get_chat_model

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = (
    "Eres el Planner Agent corporativo de una entidad financiera de alta seguridad.\n"
    "Tu única responsabilidad es interpretar la solicitud del usuario corporativo y crear un plan de trabajo "
    "inicial no redundante, óptimo y ordenado.\n\n"
    "Herramientas corporativas disponibles para asignar ('assigned_tool'):\n"
    "- 'DB_Query' / 'banking_read': Consulta de saldos, cuentas o extractos bancarios de solo lectura.\n"
    "- 'CRM_API': Consulta de información del cliente o antecedentes comerciales.\n"
    "- 'Transfer_API' / 'banking_write': Operaciones de envío de dinero o transferencias bancarias (requiere aprobación HITL).\n"
    "- 'Analytics_API': Reportes analíticos o métricas de flujo de caja.\n\n"
    "Reglas de formulación del plan:\n"
    "1. Si el usuario solicita realizar un pago o transferencia bancaria (ej: 'transferir $X al CBU Y'), "
    "asigna directamente la tarea a 'Transfer_API'. NO generes pasos previos de consulta de saldo o validación si el usuario no los solicitó explícitamente.\n"
    "2. Si el usuario solicita consultar saldos o movimientos, asigna a 'DB_Query'.\n"
    "3. Si el usuario hace una petición compuesta (ej: 'ver saldo y luego transferir'), genera primero "
    "la tarea de lectura ('DB_Query') y luego la de transferencia ('Transfer_API') con dependencies=[1].\n"
    "4. Cada paso debe ser una instancia de Task con su ID único incremental (1, 2, ...).\n"
    "5. 'dependencies' debe listar los IDs de las tareas que deben completarse antes de ejecutar la actual.\n"
    "6. 'description' debe contener instrucciones directas y explícitas para el ejecutor.\n"
    "7. El plan NO debe contener pasos innecesarios ni redundantes."
)


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    Executes the initial planning step.
    Strictly uses LLM with structured output to generate the Plan.
    Raises ValueError if LLM formulation fails (no deterministic fallbacks).
    """
    if state.get("is_threat"):
        return {
            "current_intent": "blocked",
            "plan_completed": True,
            "status": "BLOCKED",
            "final_response": "Solicitud bloqueada por las políticas de seguridad bancaria.",
            "plan": [],
            "past_steps": [],
            "current_task_idx": 0,
            "plan_summary": "Operación cancelada por amenaza de seguridad."
        }

    user_input = state.get("sanitized_input") or state.get("raw_input") or state.get("input", "")

    llm = get_chat_model(temperature=0.0)
    if llm is None:
        raise ValueError("[PlannerAgent] LLM no disponible para formular el plan inicial. Operación cancelada.")

    try:
        structured_llm = llm.with_structured_output(Plan)
        plan_output: Plan = structured_llm.invoke([
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=user_input)
        ])
    except Exception as e:
        logger.error(f"[PlannerAgent] Error al formular plan con LLM: {e}")
        raise ValueError(f"[PlannerAgent] Falló la formulación estructurada del plan: {e}") from e

    if not plan_output or not plan_output.steps:
        raise ValueError("[PlannerAgent] El LLM retornó un plan vacío o no estructurado.")

    serialized_steps = [t.model_dump() for t in plan_output.steps]
    first_tool = serialized_steps[0]["assigned_tool"].lower()

    # Determine initial intent alias for compatibility
    if "transfer" in first_tool or "write" in first_tool:
        initial_intent = "transfer"
    elif "transact" in first_tool:
        initial_intent = "transactions"
    elif "balan" in first_tool or "query" in first_tool or "read" in first_tool or "db" in first_tool:
        initial_intent = "balance"
    else:
        initial_intent = "general"

    return {
        "input": user_input,
        "plan": serialized_steps,
        "past_steps": [],
        "current_task_idx": 0,
        "current_step_index": 0,
        "plan_completed": False,
        "current_intent": initial_intent,
        "plan_summary": f"Plan inicial formulado con {len(serialized_steps)} pasos para: '{user_input}'.",
        "status": "IN_PROGRESS"
    }
