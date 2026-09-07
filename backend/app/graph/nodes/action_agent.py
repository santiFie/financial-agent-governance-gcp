"""
Action Agent Node (Enqueues transfers & triggers Human-in-the-Loop breakpoint).
Enforces separation of duties: LLM binds to stage_transfer_request tool to stage the
transaction and pauses execution; only a validated Human Approval resumes and finalizes execution.
"""
import re
from typing import Dict, Any, Optional
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.types import interrupt

from app.graph.state import AgentState
from app.db.mock_banking import mock_banking_db
from app.core.security_context import token_vault
from app.graph.tools.banking_write import stage_transfer_request
from app.core.llm import get_chat_model

ACTION_TOOLS = [stage_transfer_request]

def action_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    1. Uses LLM with stage_transfer_request tool 
       to extract transfer parameters from the sanitized prompt.
    2. Stages the transaction into the pending queue (does NOT execute direct debit).
    3. Invokes LangGraph interrupt() to pause execution for Human-in-the-Loop (HITL) approval.
    4. Upon resumption, processes the human compliance decision.
    """
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "user_default_01")
    sanitized_text = state.get("sanitized_input", "")
    source_acc = "acc_001"

    amount = None
    target_token = None

    # Step 1: Extract transfer details using LLM with Tool Calling if available
    llm = get_chat_model(temperature=0.0)

    llm_with_tools = llm.bind_tools(ACTION_TOOLS)
    system_prompt = (
        "Eres un agente bancario transaccional de alta seguridad. "
        "Tu responsabilidad es analizar la solicitud de transferencia del usuario y llamar a la herramienta "
        "`stage_transfer_request` con los parámetros correspondientes: "
        "session_id='{session_id}', user_id='{user_id}', amount (número flotante), "
        "source_account_id='{source_acc}', target_token (el token seguro anonimizado presente en el texto, ej: [CBU_BANCARIO_1]). "
        "Nunca omitas la llamada a la herramienta para encolar la transferencia."
    ).format(session_id=session_id, user_id=user_id, source_acc=source_acc)

    ai_msg = llm_with_tools.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=sanitized_text)
    ])

    if hasattr(ai_msg, "tool_calls") and ai_msg.tool_calls:
        for tool_call in ai_msg.tool_calls:
            if tool_call["name"] == "stage_transfer_request":
                args = tool_call.get("args", {})
                amount = float(args.get("amount", 25000.0))
                target_token = str(args.get("target_token", "[DESTINATARIO_ENMASCARADO]"))
                source_acc = str(args.get("source_account_id", source_acc))
                break


    # Step 2: Stage transfer in core banking system via tool
    staged = stage_transfer_request.invoke({
        "session_id": session_id,
        "user_id": user_id,
        "amount": amount,
        "source_account_id": source_acc,
        "target_token": target_token
    })
    tx_id = staged["transaction_id"]

    # Step 3: Interrupt graph execution for Human-In-The-Loop approval
    # Pauses the graph and saves state in the checkpointer
    approval_decision = interrupt({
        "type": "HUMAN_IN_THE_LOOP_APPROVAL_REQUIRED",
        "action": "BANK_TRANSFER",
        "transaction_id": tx_id,
        "amount": amount,
        "currency": "ARS",
        "source_account": source_acc,
        "target_token": target_token,
        "message": f"Transferencia de ${amount:,.2f} a {target_token} requiere autorización humana."
    })

    # Step 4: Resumed Execution (Triggered when /api/approve is called with Command(resume=...))
    decision = approval_decision.get("decision", "REJECTED") if isinstance(approval_decision, dict) else "REJECTED"
    admin_user = approval_decision.get("admin_user", "compliance_officer") if isinstance(approval_decision, dict) else "compliance_officer"

    if decision == "APPROVED":
        # Resolve real destination from the secure token vault (never revealed to LLM prompt)
        real_target = token_vault.resolve_token(session_id, target_token) or "DESTINO_AUTORIZADO"
        result = mock_banking_db.execute_approved_transfer(
            tx_id=tx_id,
            admin_user=admin_user,
            real_target_account=real_target
        )
        msg = (
            f"✅ **Transferencia Ejecutada con Éxito**\n\n"
            f"• **ID Transacción:** `{tx_id}`\n"
            f"• **Monto debitado:** ${result['debited_amount']:,.2f} {result['currency']}\n"
            f"• **Destino:** `{target_token}` (Verificado de forma segura)\n"
            f"• **Nuevo saldo disponible:** ${result['remaining_balance']:,.2f}\n"
            f"• **Aprobado por:** `{admin_user}` (Auditoría HITL)"
        )
        status = "COMPLETED"
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

    return {
        "final_response": msg,
        "status": status,
        "approval_status": decision,
        "pending_transfer": staged,
        "messages": [AIMessage(content=msg)]
    }
