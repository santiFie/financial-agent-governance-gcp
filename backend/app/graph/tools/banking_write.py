"""
Restricted Banking Action Tools.
Enqueues and stages critical operations (e.g. transfers) for Human-in-the-Loop authorization.
Does NOT directly debit funds until validated by compliance/operator.
"""
from typing import Dict, Any
from langchain_core.tools import tool
from app.db.mock_banking import mock_banking_db


@tool
def stage_transfer_request(
    session_id: str,
    user_id: str,
    amount: float,
    source_account_id: str,
    target_token: str
) -> Dict[str, Any]:
    """
    Encola una solicitud de transferencia para aprobación humana previa.
    Recibe el identificador anonimizado del destinatario (token generado por DLP).
    NO ejecuta el débito bancario en este paso.
    """
    return mock_banking_db.stage_transfer(
        session_id=session_id,
        user_id=user_id,
        amount=amount,
        source_account_id=source_account_id,
        target_token=target_token
    )
