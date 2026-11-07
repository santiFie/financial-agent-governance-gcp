"""
Restricted Banking Action Tools.
Enqueues and stages critical operations (e.g. transfers) for Human-in-the-Loop authorization.
Does NOT directly debit funds until validated by compliance/operator.
"""

from typing import Any, Dict, Optional

from langchain_core.tools import tool

from app.core.security_context import normalize_account_reference


def _resolve_cbu(cbu: str, session_id: Optional[str]) -> str:
    """Resolves a DLP surrogate token (e.g. [CBU_BANCARIO_1]) to the real value; any other reference is returned as is."""
    from app.core.security_context import token_vault

    if not (cbu.startswith("[") and cbu.endswith("]")):
        return cbu
    token_val = token_vault.resolve_token(session_id, cbu) if session_id else None
    if not token_val:
        with token_vault._lock:
            for tokens in token_vault._vault.values():
                if cbu in tokens:
                    token_val = tokens[cbu]
                    break
    return token_val.strip() if token_val else cbu


def execute_register_recipient(
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
    Registers the recipient in the FastMCP address book. It is the real mutation behind register_recipient_tool
    and is only called by pep_node once the PDP has allowed the staged call.
    """
    from mcp_financial_action_server.storage import financial_storage

    from app.core.mcp_action_client import MCPActionClient

    effective_name = (holder_name or recipient_name or "Destinatario Registrado").strip()
    effective_cbu = normalize_account_reference(cbu or cbu_or_cvu)
    effective_bank = (bank_name or "Banco Destino").strip()
    resolved_cbu = _resolve_cbu(effective_cbu, session_id)

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
    Solicita registrar una nueva cuenta o CBU/CVU/Alias en la agenda de destinatarios confiables
    del cliente (FastMCP address book). No modifica la agenda hasta la resolución de gobernanza por el PEP.
    """
    from mcp_financial_action_server.validation import validate_cbu_or_cvu

    from app.core.mcp_action_client import MCPActionClient  # noqa: F401  (puts the MCP server package on sys.path)

    effective_cbu = normalize_account_reference(cbu or cbu_or_cvu)
    # Validate the reference now so a malformed CBU goes back to the model like any other tool error.
    validate_cbu_or_cvu(_resolve_cbu(effective_cbu, session_id))
    return {
        "status": "PENDING_PEP",
        "user_id": user_id,
        "recipient_name": (holder_name or recipient_name or "Destinatario Registrado").strip(),
        "cbu": effective_cbu,
        "alias": alias,
    }


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
    dest = normalize_account_reference(destination or destination_account)
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
