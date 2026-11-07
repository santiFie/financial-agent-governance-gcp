"""
Staged-transfer queue for Human-in-the-Loop approval.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


class MockBankingDB:
    """
    HITL approval queue.
    Accounts, balances, movements and transfer execution live in the MCP action server;
    this store only keeps the transfers staged for human review.
    """

    def __init__(self):
        self.pending_transfers: Dict[str, Dict[str, Any]] = {}

    def stage_transfer(
        self, session_id: str, user_id: str, amount: float, source_account_id: str, target_token: str
    ) -> Dict[str, Any]:
        """
        Stages a transfer request into the pending approval queue.
        Does NOT execute or modify balances yet.
        """
        tx_id = f"tx_pending_{uuid.uuid4().hex[:8]}"
        transfer_record = {
            "transaction_id": tx_id,
            "session_id": session_id,
            "user_id": user_id,
            "amount": amount,
            "currency": "ARS",
            "source_account": source_account_id,
            "target_token": target_token,
            "status": "PENDING_APPROVAL",
            "created_at": datetime.utcnow().isoformat(),
        }
        self.pending_transfers[tx_id] = transfer_record
        return transfer_record

    def get_pending_transfers(self) -> List[Dict[str, Any]]:
        """Returns all transfers in PENDING_APPROVAL status."""
        return [t for t in self.pending_transfers.values() if t["status"] == "PENDING_APPROVAL"]

    def close_staged_transfer(self, tx_id: str, admin_user: str, resolved_target: str) -> None:
        """Marks a staged transfer as executed. The debit itself is done by the MCP action server."""
        record = self.pending_transfers.get(tx_id)
        if record is None:
            raise ValueError(f"Transacción {tx_id} no encontrada en cola de pendientes.")
        record["status"] = "EXECUTED"
        record["approved_by"] = admin_user
        record["executed_at"] = datetime.utcnow().isoformat()
        record["resolved_target"] = resolved_target

    def reject_staged_transfer(self, tx_id: str, admin_user: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Rejects a staged transfer."""
        if tx_id not in self.pending_transfers:
            raise ValueError(f"Transacción {tx_id} no encontrada.")

        record = self.pending_transfers[tx_id]
        record["status"] = "REJECTED"
        record["rejected_by"] = admin_user
        record["rejection_reason"] = reason or "Rechazada por política de cumplimiento / operador"
        record["updated_at"] = datetime.utcnow().isoformat()
        return record


mock_banking_db = MockBankingDB()
