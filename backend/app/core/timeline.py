"""
Observability only: turns LangGraph `stream_mode="updates"` chunks into a readable timeline of what the graph did
(sanitizer, document ingestion, plan and replan, staged tool calls, PEP/PDP decision, result).
It never alters state or decisions; nothing here can block an execution.
"""

import json
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional

# Set by the API layer for the duration of a graph run; pep_node reports each PDP decision to it.
pdp_decision_observer: ContextVar[Optional[Callable[[Dict[str, Any]], None]]] = ContextVar(
    "pdp_decision_observer", default=None
)

DOC_PREVIEW_CHARS = 600


def _compact(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TimelineBuilder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._seen_steps = 0
        self._pdp_decisions: List[Dict[str, Any]] = []
        self._pdp_reported = 0

    def _add(
        self,
        stage: str,
        node: str,
        title: str,
        detail: Optional[str] = None,
        added_by_replan: bool = False,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.events.append(
            {
                "stage": stage,
                "node": node,
                "title": title,
                "detail": detail,
                "added_by_replan": added_by_replan,
                "data": data,
            }
        )

    def on_pdp_decision(self, record: Dict[str, Any]) -> None:
        self._pdp_decisions.append(record)

    def add_update(self, node: str, update: Any) -> None:
        if node == "__interrupt__":
            payload = getattr(update[0], "value", update[0]) if update else {}
            self._add("hitl_pause", "pep_node", "Pausa HITL: se requiere aprobación humana", _compact(payload))
            return
        if not isinstance(update, dict):
            return
        handler = getattr(self, f"_on_{node}", None)
        if handler:
            handler(update)

    def _on_sanitizer(self, u: Dict[str, Any]) -> None:
        pii = u.get("detected_pii") or []
        title = "Sanitizer bloqueó el pedido" if u.get("is_threat") else "Sanitizer: DLP + Model Armor sobre el pedido"
        self._add(
            "sanitizer",
            "sanitizer",
            title,
            f"defense_action={u.get('defense_action')}, PII tokenizada={len(pii)}"
            + (f", motivo: {u.get('threat_reason')}" if u.get("is_threat") else ""),
        )

    def _on_router(self, u: Dict[str, Any]) -> None:
        for msg in u.get("orchestrator_scratchpad") or []:
            for call in getattr(msg, "tool_calls", None) or []:
                if call.get("name") == "read_document_tool":
                    self._add(
                        "document_requested",
                        "router",
                        "El Orquestador pide leer el documento adjunto",
                        _compact(call.get("args")),
                    )
        plan = u.get("execution_plan")
        if plan is not None and len(plan) > self._seen_steps:
            first = self._seen_steps == 0
            for step in plan[self._seen_steps :]:
                self._add(
                    "plan" if first else "replan",
                    "router",
                    f"{'Plan inicial' if first else 'Replan (paso agregado)'}: {step.get('title')}",
                    f"{step.get('target_node')} — {_compact(step.get('description'), 400)}",
                    added_by_replan=not first,
                    data={"step_id": step.get("step_id"), "target_node": step.get("target_node")},
                )
            self._seen_steps = len(plan)

    def _on_orchestrator_tools(self, u: Dict[str, Any]) -> None:
        for msg in u.get("orchestrator_scratchpad") or []:
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content:
                preview = content if len(content) <= DOC_PREVIEW_CHARS else content[:DOC_PREVIEW_CHARS] + "…"
                self._add(
                    "document_ingested",
                    "orchestrator_tools",
                    "Documento ingerido (ya pasó Model Armor + DLP) y agregado como contexto",
                    preview,
                )

    def _on_readonly_agent(self, u: Dict[str, Any]) -> None:
        self._add(
            "readonly_result",
            "readonly_agent",
            "readonly_agent: consulta realizada",
            _compact(u.get("final_response"), 400),
        )

    def _on_action_agent(self, u: Dict[str, Any]) -> None:
        calls = u.get("pending_tool_calls") or []
        for call in calls:
            self._add(
                "tool_call_staged",
                "action_agent",
                f"action_agent dejó en staging: {call.get('name')}",
                _compact(call.get("args")),
                data={"name": call.get("name"), "args": call.get("args")},
            )
        if not calls:
            self._add(
                "action_result", "action_agent", "action_agent completó el paso", _compact(u.get("final_response"), 300)
            )

    def _on_pep_node(self, u: Dict[str, Any]) -> None:
        # Nothing was staged: pep_node crossed without asking the PDP.
        if len(self._pdp_decisions) == self._pdp_reported and not u.get("final_response"):
            return
        self._pdp_reported = len(self._pdp_decisions)
        decision = self._pdp_decisions[-1] if self._pdp_decisions else None
        detail = f"status={u.get('status')}, approval_status={u.get('approval_status')}"
        if decision:
            detail = f"OPA: {_compact(decision['decision'])} | {detail}"
        self._add(
            "pep_decision",
            "pep_node",
            "pep_node: decisión de la política",
            detail,
            data={"pdp": decision} if decision else None,
        )
        if u.get("final_response"):
            self._add("result", "pep_node", "Resultado de la acción", _compact(u["final_response"], 500))
