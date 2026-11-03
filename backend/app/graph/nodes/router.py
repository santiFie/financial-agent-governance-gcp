"""
Router / Orchestrator Agent Node.

The Orchestrator maintains an append-only execution plan and delegates each step to an executor agent.
It is domain-agnostic: it knows nothing about specific documents, amounts or recipients.

Per invocation:
1. Threat short-circuit (blocked document read, or a threat flagged earlier in the turn).
2. If a plan step is still pending, no LLM call is made: the graph dispatches the active step.
3. Otherwise one LLM turn happens (orchestrator_initial prompt when the plan is empty, orchestrator_replan otherwise).
   The LLM either requests read_document_tool (executed by the graph's ToolNode) or emits a PlanUpdate
   that appends new steps. An empty PlanUpdate with no pending steps ends the plan.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import settings
from app.core.llm import get_chat_model
from app.core.prompt_manager import prompt_manager
from app.graph.agent_registry import TargetNode, render_agents_catalog
from app.graph.plan_utils import get_active_step
from app.graph.state import AgentState
from app.graph.tools.document_reader import read_document_tool

logger = logging.getLogger(__name__)


class OrchestratorError(RuntimeError):
    """Raised when the Orchestrator LLM turn cannot produce a valid decision."""


class PlanStep(BaseModel):
    """Plan step as stored by the system. step_id and result are never set by the LLM."""
    step_id: int = Field(description="Identificador único del paso (1, 2, ...)")
    title: str = Field(description="Descripción breve de la acción a ejecutar")
    target_node: TargetNode = Field(description="Agente que ejecuta el paso")
    description: str = Field(description="Instrucción completa para el agente, con todos los parámetros concretos")
    result: Optional[str] = Field(default=None, description="Salida producida por el agente tras ejecutar el paso")


class NewPlanStep(BaseModel):
    """Plan step as emitted by the LLM."""
    title: str = Field(description="Descripción breve de la acción a ejecutar")
    target_node: TargetNode = Field(description="Agente que ejecuta el paso")
    description: str = Field(
        description=(
            "Instrucción completa para el agente ejecutor. Debe incluir TODOS los parámetros concretos que necesita "
            "(montos con moneda, destinatarios, identificadores o tokens), copiados literalmente. "
            "El agente ejecutor no ve la solicitud original ni los documentos."
        )
    )


class PlanUpdate(BaseModel):
    """Decisión final del turno del Orquestador: pasos nuevos a agregar al plan (vacío si no falta trabajo)."""
    new_steps: List[NewPlanStep] = Field(
        default_factory=list,
        description="Pasos nuevos a agregar al final del plan. Vacío si no hace falta más trabajo.",
    )
    final_answer: Optional[str] = Field(
        default=None,
        description="Respuesta directa al usuario cuando la solicitud no requiere ningún agente.",
    )


def _render_attachment(document_name: Optional[str]) -> str:
    """Tells the Orchestrator whether the request carries an attached document and how to refer to it."""
    if not document_name:
        return "<attached_document available=\"no\" />"
    return f"<attached_document available=\"yes\" filename=\"{document_name}\" />"


def _build_context(state: AgentState) -> str:
    """Renders the data (not instructions) the Orchestrator reasons over."""
    document_name = state.get("document_name")
    plan = state.get("execution_plan") or []

    if plan:
        lines: List[str] = []
        for step in plan:
            done = step.get("result") is not None
            lines.append(
                f"Paso {step['step_id']} [{step['target_node']}] {step['title']} — "
                f"{'COMPLETADO' if done else 'PENDIENTE'}"
            )
            lines.append(f"  descripción: {step['description']}")
            if done:
                lines.append(f"  resultado: {step['result']}")
        progress = "\n".join(lines)
    else:
        progress = "Aún no hay plan."

    return (
        f"<user_request>\n{state.get('sanitized_input', '')}\n</user_request>\n"
        f"{_render_attachment(document_name)}\n"
        f"<plan_progress>\n{progress}\n</plan_progress>"
    )


def _blocked_document_artifact(scratchpad: List[Any]) -> Optional[Dict[str, Any]]:
    """Returns the read_document_tool artifact if the last scratchpad message is a BLOCKED document read."""
    if not scratchpad:
        return None
    last = scratchpad[-1]
    artifact = getattr(last, "artifact", None) if isinstance(last, ToolMessage) else None
    if isinstance(artifact, dict) and artifact.get("status") == "BLOCKED":
        return artifact
    return None


def _threat_update(state: AgentState, artifact: Dict[str, Any]) -> Dict[str, Any]:
    category = artifact.get("threat_category")
    blocked_msg = prompt_manager.render("sanitizer_responses", threat_cat=category)
    return {
        "is_threat": True,
        "threat_category": category,
        "threat_reason": artifact.get("threat_reason"),
        "defense_action": "BLOCKED",
        "status": "BLOCKED",
        "detected_pii": list(state.get("detected_pii") or []) + list(artifact.get("detected_pii") or []),
        "final_response": blocked_msg,
        "messages": [AIMessage(content=blocked_msg)],
    }


def _merge_document_pii(state: AgentState, scratchpad: List[Any], fresh_tool_result: bool) -> List[Any]:
    """Accumulates PII detected by read_document_tool into the audit trail (once per tool round)."""
    detected = list(state.get("detected_pii") or [])
    if fresh_tool_result and scratchpad and isinstance(scratchpad[-1], ToolMessage):
        artifact = getattr(scratchpad[-1], "artifact", None)
        if isinstance(artifact, dict) and artifact.get("detected_pii"):
            detected.extend(artifact["detected_pii"])
    return detected


def router_node(state: AgentState) -> Dict[str, Any]:
    """Orchestrator Agent node. See module docstring for the per-invocation contract."""
    scratchpad = list(state.get("orchestrator_scratchpad") or [])

    blocked = _blocked_document_artifact(scratchpad)
    if blocked:
        return _threat_update(state, blocked)

    if state.get("is_threat") or state.get("status") == "BLOCKED":
        return {}

    if get_active_step(state) is not None:
        return {}

    plan: List[Dict[str, Any]] = list(state.get("execution_plan") or [])
    rounds = state.get("orchestrator_tool_rounds") or 0

    prompt_name = "orchestrator_replan" if plan else "orchestrator_initial"
    system_prompt = prompt_manager.render(
        prompt_name,
        agents_catalog=render_agents_catalog(),
        max_steps=settings.orchestrator_max_plan_steps,
    )
    llm = get_chat_model(temperature=0.0).bind_tools(
        [read_document_tool, PlanUpdate], tool_choice="any"
    )
    ai_msg = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=_build_context(state))] + scratchpad
    )

    tool_calls = list(getattr(ai_msg, "tool_calls", None) or [])
    unknown = [c["name"] for c in tool_calls if c["name"] not in (read_document_tool.name, "PlanUpdate")]
    if unknown:
        raise OrchestratorError(f"El Orquestador solicitó herramientas desconocidas: {unknown}")

    document_calls = [c for c in tool_calls if c["name"] == read_document_tool.name]
    plan_calls = [c for c in tool_calls if c["name"] == "PlanUpdate"]

    # Information gathering takes precedence: the plan decision is taken after the tool results are in.
    if document_calls:
        if rounds >= settings.orchestrator_max_tool_iterations:
            raise OrchestratorError(
                f"El Orquestador excedió el máximo de {settings.orchestrator_max_tool_iterations} rondas de herramientas."
            )
        return {
            "orchestrator_scratchpad": [
                AIMessage(content=ai_msg.content or "", tool_calls=document_calls)
            ],
            "orchestrator_tool_rounds": rounds + 1,
            "detected_pii": _merge_document_pii(state, scratchpad, rounds > 0),
        }

    if not plan_calls:
        raise OrchestratorError("El Orquestador no entregó una decisión PlanUpdate.")

    update = PlanUpdate(**plan_calls[0]["args"])

    room = max(settings.orchestrator_max_plan_steps - len(plan), 0)
    if len(update.new_steps) > room:
        logger.warning(
            f"[Orchestrator] PlanUpdate truncado: {len(update.new_steps)} pasos solicitados, cupo {room}."
        )
    new_steps = [
        PlanStep(
            step_id=len(plan) + idx + 1,
            title=step.title,
            target_node=step.target_node,
            description=step.description,
        ).model_dump(mode="json")
        for idx, step in enumerate(update.new_steps[:room])
    ]

    output: Dict[str, Any] = {
        "execution_plan": plan + new_steps,
        "orchestrator_tool_rounds": 0,
        "detected_pii": _merge_document_pii(state, scratchpad, rounds > 0),
    }
    if plan and not new_steps:
        output["status"] = "COMPLETED"  # every step has a result and the Orchestrator has nothing left to add
    if not plan and not new_steps and update.final_answer:
        output["final_response"] = update.final_answer
        output["status"] = "COMPLETED"
        output["messages"] = [AIMessage(content=update.final_answer)]
    return output