"""
Mock Core Banking Database.
Implements data storage for bank accounts, transactions, and staged transfers.
Enforces separation between Read-Only queries and Authorized Write operations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


class BankAccount:
    def __init__(self, account_id: str, owner_id: str, owner_name: str, account_number: str, balance: float, currency: str = "ARS"):
        self.account_id = account_id
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.account_number = account_number
        self.balance = balance
        self.currency = currency
        self.created_at = datetime.utcnow().isoformat()


class Transaction:
    def __init__(self, tx_id: str, account_id: str, tx_type: str, amount: float, description: str, timestamp: Optional[str] = None):
        self.tx_id = tx_id
        self.account_id = account_id
        self.tx_type = tx_type  # "CREDIT" | "DEBIT"
        self.amount = amount
        self.description = description
        self.timestamp = timestamp or datetime.utcnow().isoformat()


class MockBankingDB:
    """
    Simulated Core Banking System.
    """
    def __init__(self):
        # Initial Seed Data
        self.accounts: Dict[str, BankAccount] = {
            "acc_001": BankAccount(
                account_id="acc_001",
                owner_id="user_default_01",
                owner_name="Santiago Gómez",
                account_number="CA-001293847-ARS",
                balance=485250.75,
                currency="ARS"
            ),
            "acc_002": BankAccount(
                account_id="acc_002",
                owner_id="user_default_01",
                owner_name="Santiago Gómez",
                account_number="CC-009847321-USD",
                balance=1520.00,
                currency="USD"
            )
        }

        self.transactions: List[Transaction] = [
            Transaction("tx_01", "acc_001", "CREDIT", 350000.00, "Depósito de Haberes / Sueldo Empresa Tech"),
            Transaction("tx_02", "acc_001", "DEBIT", 15400.00, "Supermercado Coto"),
            Transaction("tx_03", "acc_001", "DEBIT", 6500.00, "Servicios Públicos - Luz y Agua"),
            Transaction("tx_04", "acc_001", "DEBIT", 4200.00, "Suscripción Streaming"),
            Transaction("tx_05", "acc_002", "CREDIT", 500.00, "Transferencia Internacional Recibida")
        ]

        # Staged pending transfers awaiting Human-in-the-Loop review
        self.pending_transfers: Dict[str, Dict[str, Any]] = {}

    # ----------------------------------------------------------------------
    # READ-ONLY OPERATIONS (Callable by Read-Only DB Agent)
    # ----------------------------------------------------------------------
    def get_account_balances(self, user_id: str) -> List[Dict[str, Any]]:
        """Returns account summaries and current balances for user."""
        user_accounts = [acc for acc in self.accounts.values() if acc.owner_id == user_id]
        return [
            {
                "account_id": acc.account_id,
                "account_number": acc.account_number,
                "owner_name": acc.owner_name,
                "balance": acc.balance,
                "currency": acc.currency
            }
            for acc in user_accounts
        ]

    def get_recent_transactions(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns recent transactions for user accounts."""
        user_account_ids = {acc.account_id for acc in self.accounts.values() if acc.owner_id == user_id}
        filtered_txs = [tx for tx in self.transactions if tx.account_id in user_account_ids]
        filtered_txs.sort(key=lambda x: x.timestamp, reverse=True)
        return [
            {
                "tx_id": tx.tx_id,
                "account_id": tx.account_id,
                "type": tx.tx_type,
                "amount": tx.amount,
                "description": tx.description,
                "timestamp": tx.timestamp
            }
            for tx in filtered_txs[:limit]
        ]

    # ----------------------------------------------------------------------
    # WRITE OPERATIONS (Restricted: Action Agent stages, Admin approves)
    # ----------------------------------------------------------------------
    def stage_transfer(
        self,
        session_id: str,
        user_id: str,
        amount: float,
        source_account_id: str,
        target_token: str
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
            "created_at": datetime.utcnow().isoformat()
        }
        self.pending_transfers[tx_id] = transfer_record
        return transfer_record

    def get_pending_transfers(self) -> List[Dict[str, Any]]:
        """Returns all transfers in PENDING_APPROVAL status."""
        return [t for t in self.pending_transfers.values() if t["status"] == "PENDING_APPROVAL"]

    def execute_approved_transfer(
        self,
        tx_id: str,
        admin_user: str,
        real_target_account: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a staged transfer after Human-in-the-Loop approval.
        Modifies balance atomically.
        """
        if tx_id not in self.pending_transfers:
            raise ValueError(f"Transacción {tx_id} no encontrada en cola de pendientes.")

        record = self.pending_transfers[tx_id]
        if record["status"] != "PENDING_APPROVAL":
            raise ValueError(f"Transacción ya procesada con estado: {record['status']}")

        source_acc = self.accounts.get(record["source_account"])
        if not source_acc:
            raise ValueError(f"Cuenta de origen {record['source_account']} no existe.")

        if source_acc.balance < record["amount"]:
            record["status"] = "REJECTED_INSUFFICIENT_FUNDS"
            raise ValueError(f"Fondos insuficientes. Saldo disponible: {source_acc.balance}, Monto requerido: {record['amount']}")

        # Atomic debit
        source_acc.balance -= record["amount"]
        record["status"] = "EXECUTED"
        record["approved_by"] = admin_user
        record["executed_at"] = datetime.utcnow().isoformat()
        record["resolved_target"] = real_target_account or "CUENTA_EXTERNA_AUTORIZADA"

        # Record ledger transaction
        ledger_tx = Transaction(
            tx_id=f"tx_exec_{uuid.uuid4().hex[:8]}",
            account_id=source_acc.account_id,
            tx_type="DEBIT",
            amount=record["amount"],
            description=f"Transferencia enviada a {record['resolved_target']} (Aprobada por {admin_user})"
        )
        self.transactions.insert(0, ledger_tx)

        return {
            "status": "EXECUTED",
            "transaction_id": tx_id,
            "debited_amount": record["amount"],
            "remaining_balance": source_acc.balance,
            "currency": source_acc.currency,
            "timestamp": record["executed_at"]
        }

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


# Global singleton mock core banking instance
mock_banking_db = MockBankingDB()
