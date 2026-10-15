"""
Core Schemas for Multi-Agent Planning and Replanning Architecture.
Enforces typed schemas for Tasks, Plans, State, and Replanning Decisions.
"""
from typing import List, Optional, Literal, Dict, Any, TypedDict
from pydantic import BaseModel, Field


class Task(BaseModel):
    """
    Individual atomic task assigned to a specific execution tool/agent.
    """
    id: int = Field(description="ID único de la tarea")
    dependencies: List[int] = Field(
        default_factory=list,
        description="IDs de tareas que deben ejecutarse antes"
    )
    description: str = Field(description="Instrucción explícita de lo que debe hacer el ejecutor")
    assigned_tool: str = Field(
        description="Herramienta corporativa asignada (ej: 'CRM_API', 'DB_Query', 'banking_read', 'transfer')"
    )


class Plan(BaseModel):
    """
    Structured ordered sequence of tasks formulated to accomplish the user objective.
    """
    steps: List[Task] = Field(description="Lista ordenada de pasos no redundantes para cumplir el objetivo")


class PlanState(TypedDict):
    """
    State contract dedicated to plan execution tracking and past steps history.
    """
    input: str                     # Petición inicial del usuario corporativo
    plan: List[Task]               # El plan de trabajo actual
    past_steps: List[dict]         # Historial de tareas ejecutadas y sus resultados numéricos/textuales
    current_task_idx: int          # Índice de control de ejecución
    final_response: str            # Respuesta final formateada para producción


class ReplannerDecision(BaseModel):
    """
    Structured decision emitted by the Replanner after analyzing execution feedback.
    """
    action: Literal["CONTINUE", "REPLAN", "FINISH"] = Field(
        description="'FINISH' si el objetivo está cumplido, 'CONTINUE' si el plan actual sigue siendo válido, o 'REPLAN' si se requiere modificar las tareas restantes."
    )
    explanation: str = Field(description="Justificación y razonamiento explícito de la decisión tomada")
    new_plan: Optional[Plan] = Field(
        default=None,
        description="Nuevo plan modificado si action == 'REPLAN'"
    )
    final_response: Optional[str] = Field(
        default=None,
        description="Respuesta final sintetizada y formateada para el usuario si action == 'FINISH'"
    )
