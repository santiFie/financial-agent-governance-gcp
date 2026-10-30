"""
Policy Enforcement Point (PEP) Node.
Security boundary enforcing authorization policies before funds are debited.
Evaluates Policy Decision Point (PDP) responses to determine Fast-Path vs HITL interrupt.
"""
from typing import Dict, Any, List, Optional
import httpx
from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.graph.state import AgentState
from app.config import settings
from app.db.mock_banking import mock_banking_db
from app.core.security_context import token_vault
from app.core.mcp_action_client import mcp_action_client


class AwaitableDict(dict):
    """Dictionary subclass enabling both sync and async await execution."""
    def __await__(self):
        async def _identity():
            return self
        return _identity().__await__()


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
        if tc_name in ["transfer_funds_tool", "stage_transfer_request"]:
            args = tool_call.get("args", {})
            amount = float(args.get("amount", 0.0))
            destination = str(
                args.get("destination_account")
                or args.get("destination")
                or args.get("target_token")
                or ""
            )
            source_account = str(args.get("source_account_id") or args.get("source_account") or "acc_001")
            concept = str(args.get("concept", concept))
            break

    # Fallback to pending_transfer dict if needed
    if amount is None and pending_transfer:
        amount = float(pending_transfer.get("amount", 0.0))
        destination = str(
            pending_transfer.get("destination")
            or pending_transfer.get("destination_account")
            or pending_transfer.get("target_token")
            or ""
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
    pdp_decision: Optional[Dict[str, Any]] = None
    pdp_payload = {
        "user_id": user_id,
        "source_account_id": source_account,
        "destination": target_for_check,
        "amount": amount,
        "currency": "ARS",
        "is_trusted_recipient": is_trusted
    }

    try:
        with httpx.Client(timeout=settings.pdp_timeout_seconds) as client:
            resp = client.post(settings.pdp_url, json=pdp_payload)
            if resp.status_code == 200:
                pdp_decision = resp.json()
    except Exception:
        pdp_decision = None

    # Offline resiliency & fallback policy
    if pdp_decision is None:
        if is_trusted and amount < 50000.0:
            pdp_decision = {
                "allow": True,
                "require_hitl": False,
                "reason": "Fast-Path (resilient fallback: destinatario registrado y monto < $50.000 ARS)"
            }
        else:
            pdp_decision = {
                "allow": True,
                "require_hitl": True,
                "reason": "HITL requerido (resilient fallback: destinatario no registrado o monto >= $50.000 ARS)"
            }

    # Evaluate PDP Decision
    if not pdp_decision.get("allow", False):
        reason = pdp_decision.get("reason", "Operación bloqueada por política de seguridad")
        block_msg = f"❌ **Operación Bloqueada por Gobernanza PEP**\n\n• Motivo: {reason}"
        return AwaitableDict({
            "final_response": block_msg,
            "status": "BLOCKED",
            "approval_status": "BLOCKED",
            "is_threat": True,
            "pending_tool_calls": [],
            "pending_transfer": None,
            "messages": [AIMessage(content=block_msg)],
        })

    # Fast-Path Granted: No interrupt(), direct transfer
    if not pdp_decision.get("require_hitl", False):
        result = mock_banking_db.execute_direct_transfer(
            session_id=session_id,
            user_id=user_id,
            source_acc=source_account,
            dest_acc=target_for_check,
            amount=amount,
            concept=concept
        )

        msg = (
            f"⚡ **Transferencia Ejecutada con Éxito (Fast-Path PEP)**\n\n"
            f"• **ID Transacción:** `{result['transaction_id']}`\n"
            f"• **Monto debitado:** ${result['debited_amount']:,.2f} {result['currency']}\n"
            f"• **Destino:** `{destination}` (Verificado de forma segura)\n"
            f"• **Nuevo saldo disponible:** ${result['remaining_balance']:,.2f}\n"
            f"• **Gobernanza PEP:** Autorización Fast-Path concedida por PDP (Destinatario en agenda y monto < $50.000 ARS)."
        )

        return AwaitableDict({
            "final_response": msg,
            "status": "COMPLETED",
            "approval_status": "APPROVED",
            "pending_tool_calls": [],
            "pending_transfer": None,
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
        result = mock_banking_db.execute_approved_transfer(
            tx_id=tx_id,
            admin_user=admin_user,
            real_target_account=target_for_check
        )

        # Synchronize with FastMCP
        try:
            dest_id = target_for_check if (len(target_for_check) == 22 and target_for_check.isdigit()) else "0170099220000067891234"
            mcp_action_client.execute_approved_transfer(
                user_id=user_id,
                source_account_id=source_account,
                destination_cbu_or_alias=dest_id,
                amount=amount,
                currency="ARS",
                concept=f"HITL_TRANSFER_{tx_id}",
                idempotency_key=f"hitl-tx-{tx_id}"
            )
        except Exception:
            pass

        msg = (
            f"✅ **Transferencia Ejecutada con Éxito**\n\n"
            f"• **ID Transacción:** `{tx_id}`\n"
            f"• **Monto debitado:** ${result['debited_amount']:,.2f} {result['currency']}\n"
            f"• **Destino:** `{destination}` (Verificado de forma segura)\n"
            f"• **Nuevo saldo disponible:** ${result['remaining_balance']:,.2f}\n"
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
        "messages": [AIMessage(content=msg)],
    })
