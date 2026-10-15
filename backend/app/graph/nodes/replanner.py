"""
Replanner Agent Node.
Sole Responsibility: Dynamically assesses execution feedback from specialized subagents,
optimizes context window to prevent saturation, and decides to CONTINUE, REPLAN, or FINISH.
Strictly relies on LLM structured output.
"""
import logging
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage

from app.graph.state import AgentState
from app.graph.schemas import ReplannerDecision, Plan, Task
from app.graph.context_optimizer import context_optimizer
from app.core.llm import get_chat_model

logger = logging.getLogger(__name__)

REPLANNER_SYSTEM_PROMPT = (
    "Eres el Replanner Agent de supervisión ejecutiva para un banco corporativo.\n"
    "Tu misión es analizar rigurosamente el resultado de las tareas recién ejecutadas respecto al objetivo "
    "general del usuario corporativo, y tomar una de tres decisiones estructuradas:\n\n"
    "1. 'FINISH': Si el objetivo está completamente cumplido, o si una tarea falló y se debe dar respuesta final. "
    "En este caso, formula obligatoriamente 'final_response' consolidada y lista para producción.\n"
    "2. 'CONTINUE': Si el plan actual sigue siendo vigente y adecuado para ejecutarse el siguiente paso.\n"
    "3. 'REPLAN': Si los datos obtenidos revelan nueva información que amerita ajustar o reemplazar los pasos "
    "restantes. En este caso, formula obligatoriamente 'new_plan' con los pasos actualizados.\n\n"
    "Se conciso y estrictamente analítico."
)


def _determine_intent_from_tool(tool_name: str) -> str:
    """Helper to map assigned_tool to domain intent alias."""
    name = (tool_name or "").lower()
    if "transfer" in name or "write" in name:
        return "transfer"
    elif "transact" in name:
        return "transactions"
    elif "balan" in name or "query" in name or "read" in name or "db" in name:
        return "balance"
    return "general"


def replanner_node(state: AgentState) -> Dict[str, Any]:
    """
    Evaluates the last executed task, updates past_steps, compacts context,
    and invokes structured LLM to decide on CONTINUE, REPLAN, or FINISH.
    """
    user_input = state.get("input") or state.get("sanitized_input") or state.get("raw_input", "")
    current_plan: List[Dict[str, Any]] = [dict(s) for s in state.get("plan", [])]
    current_idx: int = state.get("current_task_idx", 0)
    past_steps: List[Dict[str, Any]] = [dict(s) for s in state.get("past_steps", [])]

    approval_status = state.get("approval_status")
    last_response = state.get("final_response") or ""

    # Record the recently finished task into past_steps
    if 0 <= current_idx < len(current_plan):
        current_task = current_plan[current_idx]
        task_status = "REJECTED" if approval_status == "REJECTED" else "COMPLETED"
        past_steps.append({
            "task_id": current_task.get("id", current_idx + 1),
            "description": current_task.get("description", "Ejecución de tarea"),
            "assigned_tool": current_task.get("assigned_tool", "Herramienta"),
            "result": last_response,
            "status": task_status
        })

    # Short circuit on human rejection (HITL compliance halt)
    if approval_status == "REJECTED":
        return {
            "past_steps": past_steps,
            "plan_completed": True,
            "status": "COMPLETED",
            "final_response": last_response or "Operación cancelada por rechazo del oficial de cumplimiento.",
            "plan_summary": "Plan interrumpido por rechazo en aprobación humana (HITL)."
        }

    # If all tasks in current plan were executed, check if we can finish immediately
    is_at_end_of_plan = (current_idx + 1) >= len(current_plan)

    # Context window optimization: compact past_steps
    compact_history = context_optimizer.format_past_steps(past_steps)

    llm = get_chat_model(temperature=0.0)
    if llm is None:
        raise ValueError("[ReplannerAgent] LLM no disponible para evaluar el progreso y replanificar.")

    try:
        structured_llm = llm.with_structured_output(ReplannerDecision)
        prompt_content = (
            f"Objetivo inicial: {user_input}\n\n"
            f"Historial acumulado de ejecución (optimizado):\n{compact_history}\n\n"
            f"Paso actual ejecutado: {current_idx + 1} de {len(current_plan)}\n"
            f"¿Es necesario replanificar, continuar o finalizar?"
        )
        decision: ReplannerDecision = structured_llm.invoke([
            SystemMessage(content=REPLANNER_SYSTEM_PROMPT),
            HumanMessage(content=prompt_content)
        ])
    except Exception as e:
        logger.error(f"[ReplannerAgent] Error en decisión LLM del replanner: {e}")
        raise ValueError(f"[ReplannerAgent] Falló la evaluación del replanner con LLM: {e}") from e

    # Apply Decision
    if decision.action == "FINISH":
        final_txt = decision.final_response or last_response
        return {
            "past_steps": past_steps,
            "plan_completed": True,
            "status": "COMPLETED",
            "final_response": final_txt,
            "plan_summary": f"Plan finalizado con éxito: {decision.explanation}"
        }

    elif decision.action == "REPLAN" and decision.new_plan and decision.new_plan.steps:
        new_plan_dicts = [t.model_dump() for t in decision.new_plan.steps]
        next_tool = new_plan_dicts[0]["assigned_tool"]
        return {
            "plan": new_plan_dicts,
            "past_steps": past_steps,
            "current_task_idx": 0,
            "current_step_index": 0,
            "plan_completed": False,
            "current_intent": _determine_intent_from_tool(next_tool),
            "plan_summary": f"Plan replanificado ({len(new_plan_dicts)} nuevos pasos): {decision.explanation}"
        }

    else:  # CONTINUE
        next_idx = current_idx + 1
        if next_idx < len(current_plan):
            next_task = current_plan[next_idx]
            return {
                "past_steps": past_steps,
                "current_task_idx": next_idx,
                "current_step_index": next_idx,
                "plan_completed": False,
                "current_intent": _determine_intent_from_tool(next_task.get("assigned_tool", "")),
                "plan_summary": f"Continuando al paso {next_idx + 1}: {next_task.get('description', '')}"
            }
        else:
            # Plan naturally exhausted
            return {
                "past_steps": past_steps,
                "plan_completed": True,
                "status": "COMPLETED",
                "final_response": last_response,
                "plan_summary": "Todos los pasos del plan han concluido."
            }
