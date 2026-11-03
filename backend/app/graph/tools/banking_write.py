"""
Restricted Banking Action Tools.
Enqueues and stages critical operations (e.g. transfers) for Human-in-the-Loop authorization.
Does NOT directly debit funds until validated by compliance/operator.
"""
from typing import Dict, Any, Optional
import uuid
from datetime import datetime, timezone
from langchain_core.tools import tool
from app.db.mock_banking import mock_banking_db


@tool
def register_recipient_tool(
    user_id: str,
    recipient_name: str = "",
    cbu: str = "",
    alias: Optional[str] = None,
    holder_name: Optional[str] = None,
    cbu_or_cvu: Optional[str] = None,
    bank_name: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Registra una nueva cuenta o CBU/CVU/Alias en la agenda de destinatarios confiables
    del cliente (FastMCP address book).
    """
    from app.core.mcp_action_client import MCPActionClient
    from app.core.security_context import token_vault
    from mcp_financial_action_server.storage import financial_storage

    effective_name = (holder_name or recipient_name or "Destinatario Registrado").strip()
    effective_cbu = (cbu or cbu_or_cvu or "").strip()
    effective_bank = (bank_name or "Banco Destino").strip()

    # Resolve token if CBU is a surrogate token (e.g. [CBU_BANCARIO_1])
    resolved_cbu = effective_cbu
    if effective_cbu.startswith("[") and effective_cbu.endswith("]"):
        token_val = None
        if session_id:
            token_val = token_vault.resolve_token(session_id, effective_cbu)
        if not token_val:
            with token_vault._lock:
                for s_id, tokens in token_vault._vault.items():
                    if effective_cbu in tokens:
                        token_val = tokens[effective_cbu]
                        break
        if token_val:
            resolved_cbu = token_val.strip()

    receipt = MCPActionClient.register_account(
        user_id=user_id,
        cbu_or_cvu=resolved_cbu,
        alias=alias,
        holder_name=effective_name,
        bank_name=effective_bank,
    )
    # Store token association if resolved
    if effective_cbu != resolved_cbu:
        with financial_storage._lock:
            for rec in financial_storage.address_book.get(user_id, []):
                if rec.get("cbu_or_cvu") == resolved_cbu:
                    rec["token"] = effective_cbu
    return receipt


@tool
def transfer_funds_tool(
    user_id: str,
    source_account: str = "acc_001",
    destination_account: str = "",
    amount: float = 0.0,
    concept: Optional[str] = "transfer",
    destination: Optional[str] = None,
    source_account_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Emite una solicitud estructurada de transferencia de fondos hacia la capa PEP.
    No ejecuta el débito bancario directo hasta la resolución de gobernanza por el PEP.
    """
    src = source_account_id or source_account or "acc_001"
    dest = destination or destination_account or ""
    return {
        "status": "PENDING_PEP",
        "user_id": user_id,
        "source_account": src,
        "source_account_id": src,
        "destination_account": dest,
        "destination": dest,
        "amount": float(amount),
        "concept": concept or "transfer",
        "session_id": session_id,
    }


ACTION_TOOLS = [register_recipient_tool, transfer_funds_tool]

