"""
Policy Enforcement Point (PEP) Node.
Security boundary enforcing authorization policies before funds are debited.
Evaluates Policy Decision Point (PDP) responses to determine Fast-Path vs HITL interrupt.
"""
import logging
from typing import Dict, Any, List, Optional
import httpx
import uuid
from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.graph.state import AgentState
from app.graph.plan_utils import complete_active_step
from app.config import settings
from app.db.mock_banking import mock_banking_db
from app.core.security_context import token_vault, normalize_account_reference
from app.core.mcp_action_client import mcp_action_client

logger = logging.getLogger(__name__)


class AwaitableDict(dict):
    """Dictionary subclass enabling both sync and async await execution."""
    def __await__(self):
        async def _identity():
            return self
        return _identity().__await__()


def _mark_current_step_completed(state: AgentState, result_text: str) -> List[Dict[str, Any]]:
    """Marks the active plan step as completed (transfer steps are completed by the PEP)."""
    return complete_active_step(state, result_text)


def _execute_via_mcp(
    user_id: str, source_account: str, destination: str, amount: float, concept: str, idempotency_key: str
) -> Dict[str, Any]:
    """Executes the transfer through the MCP action server (validation, idempotency, limits, atomic debit)."""
    return mcp_action_client.execute_approved_transfer(
        user_id=user_id,
        source_account_id=source_account,
        destination_cbu_or_alias=destination,
        amount=amount,
        currency="ARS",
        concept=concept,
        idempotency_key=idempotency_key,
    )


def _execution_failed(state: AgentState, error: Exception, approval_status: str) -> "AwaitableDict":
    msg = f"❌ **No se pudo ejecutar la transferencia**\n\n• Motivo: {error}"
    return AwaitableDict({
        "final_response": msg,
        "status": "FAILED",
        "approval_status": approval_status,
        "pending_tool_calls": [],
        "pending_transfer": None,
        "execution_plan": _mark_current_step_completed(state, msg),
        "messages": [AIMessage(content=msg)],
    })


def _query_pdp(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Asks the PDP (OPA data API) for a decision. OPA expects {"input": ...} and answers {"result": ...}.
    Returns None when the PDP is unreachable, answers with a non-200 status or returns no usable decision.
    """
    try:
        with httpx.Client(timeout=settings.pdp_timeout_seconds) as client:
            resp = client.post(settings.pdp_url, json={"input": payload})
        if resp.status_code != 200:
            logger.warning("PDP respondió con status %s en %s; se aplica fallback.", resp.status_code, settings.pdp_url)
            return None
        decision = resp.json().get("result")
        if not isinstance(decision, dict) or "allow" not in decision:
            logger.warning("PDP devolvió una respuesta sin decisión válida; se aplica fallback.")
            return None
        return decision
    except Exception as e:
        logger.warning("No se pudo consultar el PDP en %s: %s; se aplica fallback.", settings.pdp_url, e)
        return None


def _pdp_fallback_decision() -> Dict[str, Any]:
    """Decision used when the PDP cannot answer, driven by settings.pdp_fallback_mode ('deny' or 'hitl')."""
    if settings.pdp_fallback_mode == "hitl":
        return {
            "allow": True,
            "require_hitl": True,
            "fallback": True,
            "reason": "HITL requerido (PDP no disponible)",
        }
    return {
        "allow": False,
        "require_hitl": False,
        "fallback": True,
        "reason": "PDP no disponible, solicitud denegada por política de fallback.",
    }


def pep_node(state: AgentState) -> AwaitableDict:
    """
    Policy Enforcement Point (PEP) Node.
    1. Inspects pending_tool_calls or pending_transfer staged by Action Agent.
    2. Resolves surrogate tokens via TokenVault.
    3. Verifies if destination is a trusted recipient via FastMCP address book.
    4. Evaluates PDP policy at settings.pdp_url:
       - Fast-Path (allow=True, require_hitl=False): executes direct transfer without interrupt().
       - HITL (require_hitl=True): stages transfer in mock banking and halts at interrupt().
       - Resumes on approval and finalizes transfer.
    5. Marks the active step in execution_plan as completed before looping back to the Orchestrator.
       Steps that stage no transfer are completed by the Action Agent itself, so nothing is pending here.
    """
    pending_tool_calls: List[Dict[str, Any]] = list(state.get("pending_tool_calls") or [])
    pending_transfer = state.get("pending_transfer")
    user_id = state.get("user_id", "user_default_01")
    session_id = state.get("session_id", "default")

    if not pending_tool_calls and not pending_transfer:
        return AwaitableDict({
            "status": state.get("status", "COMPLETED"),
            "approval_status": state.get("approval_status", "NONE"),
        })

    amount = None
    destination = None
    source_account = "acc_001"
    concept = "Transferencia bancaria"

    # Extract transfer details from pending_tool_calls
    for tool_call in pending_tool_calls:
        tc_name = tool_call.get("name")
        if tc_name in ["transfer_funds_tool"]:
            args = tool_call.get("args", {})
            amount = float(args.get("amount", 0.0))
            destination = normalize_account_reference(
                args.get("destination_account")
                or args.get("destination")
                or args.get("target_token")
            )
            source_account = str(args.get("source_account_id") or args.get("source_account") or "acc_001")
            concept = str(args.get("concept", concept))
            break

    # Fallback to pending_transfer dict if needed
    if amount is None and pending_transfer:
        amount = float(pending_transfer.get("amount", 0.0))
        destination = normalize_account_reference(
            pending_transfer.get("destination")
            or pending_transfer.get("destination_account")
            or pending_transfer.get("target_token")
        )
        source_account = str(
            pending_transfer.get("source_account")
            or pending_transfer.get("source_account_id")
            or "acc_001"
        )
        concept = str(pending_transfer.get("concept", concept))

    if amount is None:
        amount = 0.0
    if not destination:
        destination = "[DESTINATARIO_DESCONOCIDO]"

    # Resolve token if destination is surrogate
    real_dest = None
    if destination.startswith("[") and destination.endswith("]"):
        real_dest = token_vault.resolve_token(session_id, destination)
        if not real_dest:
            with token_vault._lock:
                for s_id, tokens in token_vault._vault.items():
                    if destination in tokens:
                        real_dest = tokens[destination]
                        break

    target_for_check = (real_dest or destination).strip()

    # Check trusted recipient in FastMCP address book
    is_trusted = bool(mcp_action_client.is_trusted_recipient(user_id, target_for_check, session_id=session_id))
    if not is_trusted and real_dest and destination != target_for_check:
        is_trusted = bool(mcp_action_client.is_trusted_recipient(user_id, destination, session_id=session_id))

    # Query Policy Decision Point (PDP) via HTTP POST
    pdp_payload = {
        "user_id": user_id,
        "amount": amount,
        "is_trusted_recipient": is_trusted
    }

    pdp_decision = _query_pdp(pdp_payload)
    if pdp_decision is None:
        pdp_decision = _pdp_fallback_decision()

    # Evaluate PDP Decision
    if not pdp_decision.get("allow", False):
        reason = pdp_decision.get("reason", "Operación bloqueada por política de seguridad")
        if pdp_decision.get("fallback"):
            # PDP unavailable: fail closed without flagging the request as a security threat
            return _execution_failed(state, RuntimeError(reason), approval_status="NONE")
        block_msg = f"❌ **Operación Bloqueada por Gobernanza PEP**\n\n• Motivo: {reason}"
        return AwaitableDict({
            "final_response": block_msg,
            "status": "BLOCKED",
            "approval_status": "BLOCKED",
            "is_threat": True,
            "pending_tool_calls": [],
            "pending_transfer": None,
            "execution_plan": _mark_current_step_completed(state, block_msg),
            "messages": [AIMessage(content=block_msg)],
        })

    # Fast-Path Granted: No interrupt(), direct transfer through the MCP action server
    if not pdp_decision.get("require_hitl", False):
        try:
            receipt = _execute_via_mcp(
                user_id, source_account, target_for_check, amount, concept,
                idempotency_key=f"fastpath-{session_id}-{uuid.uuid4().hex[:12]}",
            )
        except Exception as e:
            return _execution_failed(state, e, approval_status="NONE")

        msg = (
            f"⚡ **Transferencia Ejecutada con Éxito (Fast-Path PEP)**\n\n"
            f"• **ID Transacción:** `{receipt['transaction_id']}`\n"
            f"• **Monto debitado:** ${receipt['amount']:,.2f} {receipt['currency']}\n"
            f"• **Destino:** `{destination}` (Verificado de forma segura)\n"
            f"• **Nuevo saldo disponible:** ${receipt['remaining_balance']:,.2f}\n"
            f"• **Gobernanza PEP:** Autorización Fast-Path concedida por PDP (Destinatario en agenda y monto < $50.000 ARS)."
        )

        return AwaitableDict({
            "final_response": msg,
            "status": "COMPLETED",
            "approval_status": "APPROVED",
            "pending_tool_calls": [],
            "pending_transfer": None,
            "execution_plan": _mark_current_step_completed(state, msg),
            "messages": [AIMessage(content=msg)],
        })

    # HITL Required: Stage transfer and interrupt graph
    staged = mock_banking_db.stage_transfer(
        session_id=session_id,
        user_id=user_id,
        amount=amount,
        source_account_id=source_account,
        target_token=destination
    )
    tx_id = staged["transaction_id"]

    approval_decision = interrupt({
        "action": "APPROVAL_REQUIRED",
        "type": "HUMAN_IN_THE_LOOP_APPROVAL_REQUIRED",
        "transaction_id": tx_id,
        "amount": amount,
        "currency": "ARS",
        "source_account": source_account,
        "target_token": destination,
        "details": {
            "transaction_id": tx_id,
            "user_id": user_id,
            "amount": amount,
            "source_account": source_account,
            "destination": destination,
            "reason": pdp_decision.get("reason", "Monto elevado o destinatario no registrado"),
        },
        "message": f"Transferencia de ${amount:,.2f} a {destination} requiere autorización humana."
    })

    # Resumed execution upon Human Approval/Rejection
    decision = approval_decision.get("decision", "REJECTED") if isinstance(approval_decision, dict) else "REJECTED"
    admin_user = approval_decision.get("admin_user", "compliance_officer") if isinstance(approval_decision, dict) else "compliance_officer"

    if decision == "APPROVED":
        try:
            receipt = _execute_via_mcp(
                user_id, source_account, target_for_check, amount, f"HITL_TRANSFER_{tx_id}",
                idempotency_key=f"hitl-tx-{tx_id}",
            )
        except Exception as e:
            return _execution_failed(state, e, approval_status="APPROVED")
        mock_banking_db.close_staged_transfer(tx_id=tx_id, admin_user=admin_user, resolved_target=target_for_check)

        msg = (
            f"✅ **Transferencia Ejecutada con Éxito**\n\n"
            f"• **ID Transacción:** `{tx_id}`\n"
            f"• **Monto debitado:** ${receipt['amount']:,.2f} {receipt['currency']}\n"
            f"• **Destino:** `{destination}` (Verificado de forma segura)\n"
            f"• **Nuevo saldo disponible:** ${receipt['remaining_balance']:,.2f}\n"
            f"• **Aprobado por:** `{admin_user}` (Auditoría HITL)"
        )
        status = "COMPLETED"
        approval_status = "APPROVED"
    else:
        reason = approval_decision.get("reason", "Rechazada por política de seguridad") if isinstance(approval_decision, dict) else "Rechazada"
        mock_banking_db.reject_staged_transfer(tx_id=tx_id, admin_user=admin_user, reason=reason)
        msg = (
            f"❌ **Transferencia Rechazada**\n\n"
            f"• **ID Transacción:** `{tx_id}`\n"
            f"• **Motivo:** {reason}\n"
            f"• **Revisado por:** `{admin_user}`"
        )
        status = "COMPLETED"
        approval_status = "REJECTED"

    return AwaitableDict({
        "final_response": msg,
        "status": status,
        "approval_status": approval_status,
        "pending_transfer": staged,
        "pending_tool_calls": [],
        "execution_plan": _mark_current_step_completed(state, msg),
        "messages": [AIMessage(content=msg)],
    })
