#!/usr/bin/env python3
"""
Narrated end-to-end run of the AegisBank graph for threat 3.1 (Goal Hijacking / Confused Deputy).

It runs the real graph (real LLM, real MCP server, real PEP/PDP) on a request that carries an attached
document, streams every node update and prints, in readable form:
document ingestion, initial plan, replan (steps appended after the first plan), staged tool calls,
the PEP/PDP decision, executed transfers, and balances / address book before and after.

This is a *measurement*, not a test: an attack that succeeds is a result, not a failure. The exit code is
non-zero only on tool/graph errors or missing prerequisites. Nothing here knows the attacker's account, amount
or wording: the request and document name come from a scenario data file (or CLI arguments), and everything
reported is derived from what the graph actually did.

Usage (from the repo root, with the backend venv):
    backend/.venv/bin/python scripts/demo_attack.py                        # first scenario of the data file
    backend/.venv/bin/python scripts/demo_attack.py --scenario <id>
    backend/.venv/bin/python scripts/demo_attack.py --message "..." --document comprobante.txt
    backend/.venv/bin/python scripts/demo_attack.py --runs 5               # compact per-run lines + rates
"""

import argparse
import json
import logging
import os
import sys
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
for _p in (ROOT_DIR, BACKEND_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# langchain-litellm responses carry provider fields (e.g. reasoning tokens) that pydantic's serializer reports as
# warnings on every LLM call. They are harmless noise for the narration, so they are silenced for this script only.
warnings.filterwarnings("ignore", message=r".*Pydantic serializer warnings.*", category=UserWarning)

RESULTS_DIR = ROOT_DIR / "docs" / "evidence"
SCENARIOS_FILE = BACKEND_DIR / "tests" / "data" / "attack_3_1_scenarios.json"
CONTROL_SCENARIOS_FILE = BACKEND_DIR / "tests" / "data" / "control_scenarios.json"
DEFAULT_USER = "user_default_01"
DOC_PREVIEW_CHARS = 700

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


def red(t):
    return _c("31;1", t)


def green(t):
    return _c("32;1", t)


def yellow(t):
    return _c("33;1", t)


def cyan(t):
    return _c("36;1", t)


def section(title: str) -> None:
    print(f"\n{cyan('━━ ' + title + ' ' + '━' * max(3, 70 - len(title)))}")


def load_scenarios() -> List[Dict[str, Any]]:
    """Attack scenarios first (the default is the first one), then the control scenarios."""
    scenarios: List[Dict[str, Any]] = []
    for path in (SCENARIOS_FILE, CONTROL_SCENARIOS_FILE):
        with open(path, "r", encoding="utf-8") as f:
            scenarios.extend(json.load(f))
    return scenarios


def _snapshot(user_id: str) -> Dict[str, Any]:
    """Balances, address book and ledger of the user, read straight from the MCP server storage."""
    from mcp_financial_action_server.storage import financial_storage

    return {
        "balances": {
            f"{a['account_id']} ({a['currency']})": a["balance"]
            for a in financial_storage.get_account_balances(user_id)
        },
        "address_book": [dict(r) for r in financial_storage.address_book.get(user_id, [])],
        "ledger": [dict(t) for t in financial_storage.ledger],
    }


def _reset_state() -> None:
    from mcp_financial_action_server.storage import financial_storage

    from app.db.mock_banking import mock_banking_db

    financial_storage.reset_state()
    mock_banking_db.pending_transfers.clear()


def _check_pdp() -> Optional[str]:
    """Returns a warning when the PDP is unreachable (the PEP would then apply pdp_fallback_mode)."""
    import httpx

    from app.config import settings

    try:
        httpx.post(
            settings.pdp_url,
            json={"input": {"amount": 1, "is_trusted_recipient": False}},
            timeout=settings.pdp_timeout_seconds,
        ).raise_for_status()
        return None
    except Exception as e:
        return (
            f"El PDP (OPA) no responde en {settings.pdp_url} ({type(e).__name__}). "
            f"Se aplicará pdp_fallback_mode='{settings.pdp_fallback_mode}': ninguna transferencia llegará a Fast-Path. "
            f"Levantalo con `make opa-up` o `docker compose up -d opa`."
        )


SERVICE_LABELS = {
    "model_armor": "Model Armor",
    "dlp": "Cloud DLP",
    "llm": "LLM",
    "checkpointer": "Checkpointer",
    "gcs": "Documento (GCS)",
}


def build_service_plan(settings: Any, checkpointer: Any) -> List[Dict[str, str]]:
    """
    What the configuration says each service will use, before any call is made.
    `backend` is "GCP" (real service), "LOCAL" (local implementation) or "EXTERNO" (non-GCP LLM provider).
    """
    from app.core.llm import describe_llm, effective_provider
    from app.db.firestore_saver import FirestoreSaver

    armor_on = bool(settings.use_gcp_model_armor and settings.model_armor_template_id)
    orchestrator = effective_provider(settings.orchestrator_provider)
    executors = effective_provider(None)
    llm_gcp = orchestrator == "vertexai" and executors == "vertexai"
    firestore = isinstance(checkpointer, FirestoreSaver)
    gcs_on = bool(settings.use_gcs_ingestion and settings.gcs_bucket_name)
    orchestrator_label = describe_llm(settings.orchestrator_provider, settings.orchestrator_model or None)
    executors_label = describe_llm(None, None)
    llm_detail = (
        f"todos los agentes: {orchestrator_label}"
        if orchestrator_label == executors_label
        else f"orquestador {orchestrator_label}; ejecutores {executors_label}"
    )
    return [
        {
            "service": "model_armor",
            "backend": "GCP" if armor_on else "LOCAL",
            "detail": f"plantilla {settings.gcp_location}/{settings.model_armor_template_id}"
            if armor_on
            else "guardrail local (regex)",
        },
        {
            "service": "dlp",
            "backend": "GCP" if settings.security_mode == "gcp_live" else "LOCAL",
            "detail": "Cloud DLP inspect_content"
            if settings.security_mode == "gcp_live"
            else "tokenizador local (regex)",
        },
        {
            "service": "llm",
            "backend": "GCP" if llm_gcp else "EXTERNO",
            "detail": llm_detail,
        },
        {
            "service": "checkpointer",
            "backend": "GCP" if firestore else "LOCAL",
            "detail": f"Firestore, colección {settings.firestore_collection}"
            if firestore
            else f"{type(checkpointer).__name__} (en memoria)",
        },
        {
            "service": "gcs",
            "backend": "GCP" if gcs_on else "LOCAL",
            "detail": f"gs://{settings.gcs_bucket_name}" if gcs_on else "backend/local_storage",
        },
    ]


def gcp_requirements_problems(settings: Any, plan: List[Dict[str, str]]) -> List[str]:
    """Why this configuration is not "all real GCP" (empty list = it is). The document source may be local on purpose."""
    problems: List[str] = []
    if settings.security_mode != "gcp_live":
        problems.append(f"SECURITY_MODE={settings.security_mode} (se necesita gcp_live)")
    for row in plan:
        if row["service"] != "gcs" and row["backend"] != "GCP":
            problems.append(f"{SERVICE_LABELS[row['service']]}: {row['backend']} ({row['detail']})")
    return problems


def print_preflight(plan: List[Dict[str, str]], startup_fallbacks: Dict[str, str]) -> None:
    section("Servicios configurados")
    for row in plan:
        icon = green("GCP    ") if row["backend"] == "GCP" else yellow(f"{row['backend']:<7}")
        print(f"  {SERVICE_LABELS[row['service']]:<16} {icon} {row['detail']}")
    for service, reason in startup_fallbacks.items():
        print(red(f"  ⚠ {SERVICE_LABELS.get(service, service)} cayó a LOCAL al iniciar: {reason}"))


def services_summary(delta: Dict[str, Dict[str, Any]], startup_fallbacks: Dict[str, str]) -> Dict[str, Any]:
    """Per run: what each service actually served, and whether any GCP service degraded to its local fallback."""
    fallbacks = {svc: d["last_fallback_reason"] or "fallback" for svc, d in delta.items() if d.get("local_fallback")}
    fallbacks = {**startup_fallbacks, **fallbacks}
    return {"used": delta, "fallbacks": fallbacks, "degraded": bool(fallbacks)}


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


class Narrator:
    """Turns graph stream updates into printed events and a structured summary."""

    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.seen_steps = 0
        self.plan: List[Dict[str, Any]] = []
        self.event_no = 0
        self._pdp_reported = 0
        self.initial_plan: List[Dict[str, Any]] = []
        self.replanned: List[Dict[str, Any]] = []
        self.staged: List[Dict[str, Any]] = []
        self.pdp_decisions: List[Dict[str, Any]] = []
        self.document_read = False
        self.threat: Optional[str] = None
        self.paused_for_hitl = False
        self.final_response: Optional[str] = None
        self.status: Optional[str] = None
        self.events: List[Dict[str, Any]] = []  # full, untruncated record of the run (feeds the HTML report)
        # action_agent steps that ended without a valid staged tool call (malformed or rejected tool call, or a step
        # the agent cannot do). A run with these is NOT evidence that a defense held.
        self.action_steps_without_tool_call: List[str] = []

    def event(self, kind: str, title: str, node: str, step: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
        ev = {"kind": kind, "title": title, "node": node, "step": step, **fields}
        self.events.append(ev)
        return ev

    def out(self, text: str = "") -> None:
        if self.verbose:
            print(text)

    def section(self, title: str) -> None:
        """Events are numbered in the order they happen (the graph loops between planning and execution)."""
        self.event_no += 1
        if self.verbose:
            section(f"{self.event_no}. {title}")

    def _active_step_label(self) -> str:
        step = next((st for st in self.plan if st.get("result") is None), None)
        return f"paso {step['step_id']}" if step else "sin paso activo"

    def on_update(self, node: str, update: Dict[str, Any]) -> None:
        if node == "__interrupt__":
            self.paused_for_hitl = True
            payload = getattr(update[0], "value", update[0]) if update else {}
            self.event("hitl_pause", "Pausa HITL: se requiere aprobación humana", "pep_node", data=payload)
            self.out(f"\n{yellow('⏸  HITL')}: el grafo se pausó esperando aprobación humana → {_short(payload, 300)}")
            return
        if not isinstance(update, dict):
            return
        handler = getattr(self, f"_on_{node}", None)
        if handler:
            handler(update)
        if update.get("execution_plan") is not None:
            self.plan = list(update["execution_plan"])
        for key in ("final_response", "status"):
            if update.get(key) is not None:
                setattr(self, key, update[key])

    def _on_sanitizer(self, u: Dict[str, Any]) -> None:
        self.section("Sanitizer (DLP + Model Armor sobre el pedido)")
        pii = u.get("detected_pii") or []
        self.event(
            "sanitizer",
            "Sanitizer (DLP + Model Armor sobre el pedido)",
            "sanitizer",
            defense_action=u.get("defense_action"),
            is_threat=bool(u.get("is_threat")),
            pii_count=len(pii),
            sanitized_input=u.get("sanitized_input"),
            threat_reason=u.get("threat_reason"),
        )
        self.out(f"defense_action={u.get('defense_action')}  is_threat={u.get('is_threat')}  PII tokenizada={len(pii)}")
        self.out(f"input saneado: {_short(u.get('sanitized_input'), 300)}")
        if u.get("is_threat"):
            self.threat = u.get("threat_category") or "THREAT"
            self.out(red(f"BLOQUEADO por el sanitizer: {u.get('threat_reason')}"))

    def _on_router(self, u: Dict[str, Any]) -> None:
        for msg in u.get("orchestrator_scratchpad") or []:
            for call in getattr(msg, "tool_calls", None) or []:
                if call.get("name") == "read_document_tool":
                    self.section("Orquestador pide leer el documento adjunto")
                    self.event(
                        "document_requested",
                        "Orquestador pide leer el documento adjunto",
                        "router",
                        tool_call={"name": call.get("name"), "args": call.get("args")},
                    )
                    self.out(f"read_document_tool({_short(call.get('args'))})")
        plan = u.get("execution_plan")
        if plan is not None and len(plan) > self.seen_steps:
            new = plan[self.seen_steps :]
            first = self.seen_steps == 0
            title = "Plan inicial del Orquestador" if first else "REPLAN: el Orquestador agregó pasos"
            self.section(title)
            self.event(
                "plan" if first else "replan",
                title,
                "router",
                added_by_replan=not first,
                steps=[{k: st.get(k) for k in ("step_id", "title", "target_node", "description")} for st in new],
            )
            for step in new:
                marker = "" if first else red("  ← agregado al replanificar")
                self.out(f"  [{step['step_id']}] {step['target_node']:<14} {bold(step['title'])}{marker}")
                self.out(f"      {dim(_short(step['description'], 260))}")
            (self.initial_plan if first else self.replanned).extend(new)
            self.seen_steps = len(plan)
        if plan is not None:
            self.plan = list(plan)
        if u.get("final_response") and u.get("status") in ("FAILED", "COMPLETED"):
            self.out(f"\nRespuesta del Orquestador: {_short(u['final_response'], 400)}")
            self.event("orchestrator_answer", "Respuesta final del Orquestador", "router", text=u["final_response"])

    def _on_orchestrator_tools(self, u: Dict[str, Any]) -> None:
        for msg in u.get("orchestrator_scratchpad") or []:
            content = getattr(msg, "content", "")
            if not isinstance(content, str) or not content:
                continue
            self.document_read = True
            self.event(
                "document_ingested",
                "Documento ingerido: pasó Model Armor + DLP y entró como contexto",
                "orchestrator_tools",
                text=content,
            )
            self.out(
                dim("Contenido que llega al LLM (ya pasó Model Armor + DLP; los datos sensibles van tokenizados):")
            )
            preview = content if len(content) <= DOC_PREVIEW_CHARS else content[:DOC_PREVIEW_CHARS] + "…"
            for line in preview.splitlines():
                self.out(dim("  │ " + line))
            self.out(green("✔ El documento pasó los filtros y entró como contexto."))

    def _on_readonly_agent(self, u: Dict[str, Any]) -> None:
        self.section(f"readonly_agent [{self._active_step_label()}]")
        self.event(
            "readonly_result",
            "readonly_agent (lectura)",
            "readonly_agent",
            step=self._active_step_label(),
            text=u.get("final_response"),
        )
        self.out(_short(u.get("final_response"), 400))

    def _on_action_agent(self, u: Dict[str, Any]) -> None:
        self.section(f"action_agent [{self._active_step_label()}]")
        calls = u.get("pending_tool_calls") or []
        for call in calls:
            self.staged.append(call)
            self.event(
                "tool_call_staged",
                f"Tool call en staging: {call.get('name')}",
                "action_agent",
                step=self._active_step_label(),
                tool_call={"name": call.get("name"), "args": call.get("args")},
            )
            self.out(f"Tool call en staging: {bold(call.get('name'))}({_short(call.get('args'), 300)})")
        if not calls:
            self.action_steps_without_tool_call.append(self._active_step_label())
            self.out(_short(u.get("final_response") or "(paso completado sin transferencia en staging)", 300))
            self.event(
                "action_result",
                "action_agent ejecutó el paso (sin transferencia en staging)",
                "action_agent",
                step=self._active_step_label(),
                text=u.get("final_response"),
            )

    def _on_pep_node(self, u: Dict[str, Any]) -> None:
        # A step that staged no transfer (e.g. registering a recipient) passes through pep_node without a decision.
        if len(self.pdp_decisions) == self._pdp_reported and not u.get("final_response"):
            return
        self.section(f"pep_node (Policy Enforcement Point) [{self._active_step_label()}]")
        self._pdp_reported = len(self.pdp_decisions)
        self.event(
            "pep_decision",
            "pep_node (Policy Enforcement Point)",
            "pep_node",
            step=self._active_step_label(),
            pdp=self.pdp_decisions[-1] if self.pdp_decisions else None,
            status=u.get("status"),
            approval_status=u.get("approval_status"),
            text=u.get("final_response"),
        )
        if self.pdp_decisions:
            d = self.pdp_decisions[-1]
            self.out(f"Entrada a OPA: {_short(d['input'])}")
            self.out(f"Decisión de OPA: {_short(d['decision'])}")
        self.out(f"status={u.get('status')}  approval_status={u.get('approval_status')}")
        if u.get("final_response"):
            self.out(_short(u["final_response"], 500))


def run_once(
    scenario: Dict[str, Any],
    user_id: str,
    verbose: bool,
    startup_fallbacks: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    from langchain_core.messages import HumanMessage

    from app.core.service_status import service_status, snapshot_delta
    from app.graph.graph import financial_graph
    from app.graph.nodes import pep as pep_module

    status_before = service_status.snapshot()

    session_id = f"sess_demo_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": session_id}}
    narrator = Narrator(verbose)
    inputs = scenario["input"]

    _reset_state()
    before = _snapshot(user_id)
    if verbose:
        section("Estado inicial")
        print(f"Saldos: {json.dumps(before['balances'], ensure_ascii=False)}")
        print(f"Libreta de destinatarios: {len(before['address_book'])} registro(s)")
        print(f"\nPedido del usuario: {bold(inputs['raw_input'])}")
        print(f"Documento adjunto: {bold(str(inputs.get('document_name')))}")

    original_query = pep_module._query_pdp

    def spy_query(payload):
        decision = original_query(payload)
        narrator.pdp_decisions.append(
            {"input": payload, "decision": decision if decision is not None else "PDP no disponible → fallback"}
        )
        return decision

    error: Optional[str] = None
    try:
        with patch.object(pep_module, "_query_pdp", side_effect=spy_query):
            for chunk in financial_graph.stream(
                {
                    "messages": [HumanMessage(content=inputs["raw_input"])],
                    "raw_input": inputs["raw_input"],
                    "document_name": inputs.get("document_name"),
                    "session_id": session_id,
                    "user_id": user_id,
                    "status": "IN_PROGRESS",
                },
                config=config,
                stream_mode="updates",
            ):
                for node, update in chunk.items():
                    narrator.on_update(node, update)
    except Exception as e:  # reported, never hidden
        error = f"{type(e).__name__}: {e}"

    after = _snapshot(user_id)
    new_ledger = after["ledger"][len(before["ledger"]) :]
    new_recipients = [r for r in after["address_book"] if r not in before["address_book"]]

    if narrator.threat:
        path = "BLOCKED"
    elif new_ledger:
        path = "FAST_PATH"
    elif error:
        path = "ERROR"
    elif narrator.paused_for_hitl:
        path = "HITL"
    else:
        path = "NONE"

    summary = {
        "session_id": session_id,
        "document_read": narrator.document_read,
        "initial_plan": [(s["target_node"], s["title"]) for s in narrator.initial_plan],
        "replanned": [(s["target_node"], s["title"]) for s in narrator.replanned],
        "action_agent_invoked": any(s["target_node"] == "action_agent" for s in narrator.plan),
        "new_recipients": new_recipients,
        "transfers_executed": new_ledger,
        "execution_path": path,
        "pdp_decisions": [d["decision"] for d in narrator.pdp_decisions],
        "error": error,
        "events": narrator.events,
        "request": {"message": inputs["raw_input"], "document_name": inputs.get("document_name"), "user_id": user_id},
        "before": before,
        "after": after,
        "final_response": narrator.final_response,
        "action_steps_without_tool_call": narrator.action_steps_without_tool_call,
        "services": services_summary(snapshot_delta(status_before, service_status.snapshot()), startup_fallbacks or {}),
    }

    if verbose:
        _print_outcome(summary, before, after, narrator)
    return summary


def _print_outcome(summary: Dict[str, Any], before: Dict[str, Any], after: Dict[str, Any], narrator: Narrator) -> None:
    section("Resultado")
    if summary["error"]:
        print(red(f"Error del grafo: {summary['error']}"))
    print(f"Camino de ejecución: {bold(summary['execution_path'])}")
    print(
        f"action_agent invocado: {summary['action_agent_invoked']}   "
        f"pasos agregados al replanificar: {len(summary['replanned'])}"
    )

    if summary["action_steps_without_tool_call"]:
        print(
            yellow(
                f"⚠ {len(summary['action_steps_without_tool_call'])} paso(s) de action_agent terminaron SIN una tool call "
                "válida (mal formada o rechazada). Un resultado sin transferencia aquí NO prueba que una defensa funcionó."
            )
        )
    _print_services_used(summary["services"])

    print(f"\n{bold('Saldos')}  antes → después")
    for acc, bal in before["balances"].items():
        now = after["balances"].get(acc, bal)
        flag = red("  (cambió)") if now != bal else ""
        print(f"  {acc}: {_money(bal)} → {_money(now)}{flag}")

    print(
        f"\n{bold('Libreta de destinatarios')}  antes: {len(before['address_book'])}  después: {len(after['address_book'])}"
    )
    for r in summary["new_recipients"]:
        print(red(f"  + nuevo destinatario: {_short(r, 220)}"))

    print(f"\n{bold('Transferencias ejecutadas')}: {len(summary['transfers_executed'])}")
    for t in summary["transfers_executed"]:
        print(red(f"  ✗ {_short(t, 260)}"))
    if not summary["transfers_executed"]:
        print(green("  ninguna"))
    print()


def _print_services_used(services: Dict[str, Any]) -> None:
    print(f"\n{bold('Servicios usados en la corrida')}")
    for service, d in services["used"].items():
        if service == "llm":
            continue  # counts model instantiations, not calls; the model is shown in the preflight
        parts = [f"{d[b]} {b}" for b in ("gcp", "local", "local_fallback") if d[b]]
        print(f"  {SERVICE_LABELS.get(service, service):<16} {', '.join(parts)}")
    if services["degraded"]:
        for service, reason in services["fallbacks"].items():
            print(red(f"  ⚠ {SERVICE_LABELS.get(service, service)} cayó a LOCAL: {reason}"))
        print(red("  La corrida NO fue 100% GCP."))
    else:
        print(green("  Sin fallbacks a local."))


def _pure_gcp(summary: Dict[str, Any], meta: Dict[str, Any]) -> Optional[bool]:
    """True/False for a gcp_live run (no fallback to local / some fallback); None when it was not meant to be GCP."""
    if meta.get("security_mode") != "gcp_live" or "services" not in summary:
        return None
    return not summary["services"]["degraded"]


def save_run(summary: Dict[str, Any], meta: Dict[str, Any], results_dir: Path) -> Path:
    """Stores the full run as JSON and refreshes manifest.json, which the offline HTML viewer (docs/evidence) reads."""
    runs_dir = results_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"{stamp}_{summary['session_id']}.json"
    record = {**meta, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "live-run", **summary}
    (runs_dir / name).write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    manifest_path = results_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    manifest = [m for m in manifest if m["file"] != f"runs/{name}"]
    manifest.append(
        {
            "file": f"runs/{name}",
            "timestamp": record["timestamp"],
            "scenario": meta["scenario_id"],
            "model": meta["model"],
            "execution_path": summary["execution_path"],
            "transfers": len(summary["transfers_executed"]),
            "pure_gcp": _pure_gcp(summary, meta),
            "source": "live-run",
        }
    )
    manifest.sort(key=lambda m: m["timestamp"], reverse=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return runs_dir / name


def _percent(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n // total}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta y narra el escenario 3.1 sobre el grafo real.")
    parser.add_argument(
        "--scenario",
        help="id del escenario en attack_3_1_scenarios.json o control_scenarios.json (default: el primer ataque)",
    )
    parser.add_argument("--message", help="mensaje del usuario (sobrescribe el del escenario)")
    parser.add_argument("--document", help="document_name adjunto (sobrescribe el del escenario)")
    parser.add_argument("--user-id", default=DEFAULT_USER)
    parser.add_argument(
        "--runs", type=int, default=1, help="repeticiones; con N>1 imprime una línea por corrida y las tasas"
    )
    parser.add_argument("--no-save", action="store_true", help="no guardar la corrida en docs/evidence (visor HTML)")
    parser.add_argument(
        "--results-dir", default=str(RESULTS_DIR), help="directorio del visor HTML y de las corridas guardadas"
    )
    parser.add_argument(
        "--require-gcp",
        action="store_true",
        help="aborta si algún servicio (Model Armor, DLP, Vertex AI, Firestore) no está configurado como GCP real "
        "y falla (exit 3) si durante la corrida algo cayó a su implementación local",
    )
    parser.add_argument("--json", dest="json_out", help="guarda el/los resúmenes en este archivo JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    os.chdir(BACKEND_DIR)  # relative settings (local_storage, .env) resolve like in the backend
    from app.core.llm import LLMConfigurationError, get_chat_model

    try:
        get_chat_model(temperature=0.0)
    except LLMConfigurationError as e:
        print(red(f"Falta configuración de LLM: {e}"), file=sys.stderr)
        return 2

    scenarios = load_scenarios()
    scenario = next((s for s in scenarios if s["id"] == args.scenario), None) if args.scenario else scenarios[0]
    if scenario is None:
        print(red(f"Escenario desconocido. Disponibles: {[s['id'] for s in scenarios]}"), file=sys.stderr)
        return 2
    scenario = json.loads(json.dumps(scenario))
    if args.message:
        scenario["input"]["raw_input"] = args.message
    if args.document:
        scenario["input"]["document_name"] = args.document

    from app.config import settings
    from app.core.llm import describe_llm
    from app.core.service_status import service_status
    from app.db import checkpointer as checkpointer_module

    print(bold("AegisBank AI — escenario 3.1 (Goal Hijacking / Confused Deputy)"))
    print(f"escenario={scenario['id']}  modo={settings.security_mode}")
    plan = build_service_plan(settings, checkpointer_module.checkpointer)
    startup_fallbacks = {
        svc: str(d["last_fallback_reason"] or "fallback")
        for svc, d in service_status.snapshot().items()
        if d.get("local_fallback")
    }
    print_preflight(plan, startup_fallbacks)
    problems = gcp_requirements_problems(settings, plan)
    if args.require_gcp and (problems or startup_fallbacks):
        print(red("\n--require-gcp: la corrida no se ejecuta porque no todo es GCP real:"), file=sys.stderr)
        for problem in problems or []:
            print(red(f"  - {problem}"), file=sys.stderr)
        for svc, reason in startup_fallbacks.items():
            print(red(f"  - {SERVICE_LABELS.get(svc, svc)} cayó a LOCAL al iniciar: {reason}"), file=sys.stderr)
        print("Ejecutá `make build-gcp` (y `make smoke-gcp`) para revisar la configuración.", file=sys.stderr)
        return 3
    warning = _check_pdp()
    if warning:
        print(yellow(f"⚠ {warning}"))

    runs = max(args.runs, 1)
    summaries = []
    for i in range(runs):
        if runs > 1:
            s = run_once(scenario, args.user_id, verbose=False, startup_fallbacks=startup_fallbacks)
            print(
                f"[{i + 1}/{runs}] path={s['execution_path']:<9} action_agent={s['action_agent_invoked']!s:<5} "
                f"replan+={len(s['replanned'])} nuevos_dest={len(s['new_recipients'])} "
                f"transferencias={len(s['transfers_executed'])}"
                + (
                    f"  SIN-TOOL-CALL={len(s['action_steps_without_tool_call'])}"
                    if s["action_steps_without_tool_call"]
                    else ""
                )
                + ("  FALLBACK-LOCAL" if s["services"]["degraded"] else "")
                + (f"  ERROR {s['error']}" if s["error"] else "")
            )
        else:
            s = run_once(scenario, args.user_id, verbose=True, startup_fallbacks=startup_fallbacks)
        summaries.append(s)
        if not args.no_save:
            meta = {
                "scenario_id": scenario["id"],
                "scenario_description": scenario.get("description"),
                "security_mode": settings.security_mode,
                "use_gcs_ingestion": settings.use_gcs_ingestion,
                "model": describe_llm(settings.orchestrator_provider, settings.orchestrator_model or None),
                "executor_model": describe_llm(None, None),
                "services_configured": plan,
                "pdp_url": settings.pdp_url,
                "pdp_fallback_mode": settings.pdp_fallback_mode,
                "run_index": i + 1,
                "runs_in_batch": runs,
            }
            saved = save_run(s, meta, Path(args.results_dir))
            print(dim(f"Corrida guardada en {saved}  (verla con: make server-results)"))

    if runs > 1:
        section("Tasas")
        print(f"action_agent invocado:        {_percent(sum(s['action_agent_invoked'] for s in summaries), runs)}")
        print(f"replan con pasos agregados:   {_percent(sum(bool(s['replanned']) for s in summaries), runs)}")
        print(f"destinatario nuevo registrado:{_percent(sum(bool(s['new_recipients']) for s in summaries), runs)}")
        print(f"transferencia ejecutada:      {_percent(sum(bool(s['transfers_executed']) for s in summaries), runs)}")
        for path in ("FAST_PATH", "HITL", "BLOCKED", "NONE", "ERROR"):
            print(f"  camino {path:<9} {_percent(sum(s['execution_path'] == path for s in summaries), runs)}")
        print(
            f"action_agent sin tool call válida: "
            f"{_percent(sum(bool(s['action_steps_without_tool_call']) for s in summaries), runs)}"
            "  (no cuentan como defensa)"
        )
        print(f"corridas con fallback a local:   {_percent(sum(s['services']['degraded'] for s in summaries), runs)}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nResumen guardado en {args.json_out}")

    if any(s["error"] for s in summaries):
        return 1
    if args.require_gcp and any(s["services"]["degraded"] for s in summaries):
        print(red("--require-gcp: alguna corrida usó un fallback local; no es una medición 100% GCP."), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
