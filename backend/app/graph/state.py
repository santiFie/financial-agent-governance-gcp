"""
State Definition for the Multi-Agent Financial Assistant Graph.
Enforces typed transitions, plan tracking, and preserves isolated audit trails.
"""
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.models.api_schemas import DetectedPII
from app.graph.schemas import Task, PlanState


class AgentState(TypedDict, total=False):
    """
    Core state passed through all nodes of the LangGraph architecture.
    Inherits PlanState attributes for strict planner/replanner execution contracts.
    """
    # --------------------------------------------------------------------------
    # PlanState Core Fields
    # --------------------------------------------------------------------------
    input: str                     # Petición inicial del usuario corporativo
    plan: List[Dict[str, Any]]     # El plan de trabajo actual (lista de Tasks o dicts)
    past_steps: List[Dict[str, Any]] # Historial de tareas ejecutadas y sus resultados numéricos/textuales
    current_task_idx: int          # Índice de control de ejecución
    final_response: Optional[str]  # Respuesta final formateada para producción

    # --------------------------------------------------------------------------
    # LangChain message history
    # --------------------------------------------------------------------------
    messages: Annotated[List[BaseMessage], add_messages]

    # --------------------------------------------------------------------------
    # Session & Identity
    # --------------------------------------------------------------------------
    session_id: str
    user_id: str

    # --------------------------------------------------------------------------
    # Security & Sanitization context
    # --------------------------------------------------------------------------
    raw_input: str
    sanitized_input: str
    detected_pii: List[DetectedPII]
    is_threat: bool
    threat_category: Optional[str]
    threat_reason: Optional[str]
    defense_action: str  # "PASSED" | "DEIDENTIFIED" | "BLOCKED"

    # --------------------------------------------------------------------------
    # Execution & Routing metadata
    # --------------------------------------------------------------------------
    current_intent: Optional[str]  # "general" | "balance" | "transactions" | "transfer" | "blocked"
    current_step_index: int        # Alias de compatibilidad hacia current_task_idx
    plan_completed: bool           # Flag de completitud de ejecución
    plan_summary: Optional[str]    # Resumen descriptivo del plan

    # --------------------------------------------------------------------------
    # Action / HITL Staging
    # --------------------------------------------------------------------------
    pending_transfer: Optional[Dict[str, Any]]
    approval_status: Optional[str]  # "NONE" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED"
    status: str                    # "COMPLETED" | "BLOCKED" | "PENDING_APPROVAL" | "IN_PROGRESS"
