"""
Transactional persistence layer for core banking operations.
Thread-safe and atomic with balance lock and idempotency cache.
"""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mcp_financial_action_server.config import settings
from mcp_financial_action_server.models import (
    CancelDebitReceipt,
    RegisteredAccountReceipt,
    ScheduledDebitReceipt,
    TransferReceipt,
)
from mcp_financial_action_server.validation import (
    validate_alias,
    validate_cbu_or_cvu,
    validate_daily_limit,
    validate_destination_cbu_or_alias,
    validate_transfer_amount,
)


def _current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AccountRecord:
    def __init__(
        self,
        account_id: str,
        owner_id: str,
        owner_name: str,
        account_number: str,
        balance: float,
        currency: str = "ARS",
    ):
        self.account_id = account_id
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.account_number = account_number
        self.balance = float(balance)
        self.currency = currency


class FinancialStorage:
    """
    Thread-safe storage supporting atomic asset mutations, idempotency protection,
    address book registration, and direct debit scheduling.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.accounts: Dict[str, AccountRecord] = {}
        self.ledger: List[Dict[str, Any]] = []
        self.idempotency_store: Dict[str, TransferReceipt] = {}
        self.daily_transfers: Dict[str, Dict[str, float]] = {}  # user_id -> {date: total_amount}
        self.address_book: Dict[str, List[Dict[str, Any]]] = {}  # user_id -> list of recipients
        self.direct_debits: Dict[str, Dict[str, Any]] = {}  # debit_id -> debit record
        self._seed_default_data()

    def _seed_default_data(self):
        """Seed initial accounts for testing and standard user environments."""
        self.accounts = {
            "acc_001": AccountRecord(
                account_id="acc_001",
                owner_id="user_default_01",
                owner_name="Santiago Gómez",
                account_number="CA-001293847-ARS",
                balance=485250.75,
                currency="ARS",
            ),
            "acc_002": AccountRecord(
                account_id="acc_002",
                owner_id="user_default_01",
                owner_name="Santiago Gómez",
                account_number="CC-009847321-USD",
                balance=1520.00,
                currency="USD",
            ),
        }
        self.ledger = []
        self.history = [
            {
                "tx_id": "tx_01",
                "account_id": "acc_001",
                "type": "CREDIT",
                "amount": 350000.00,
                "description": "Depósito de Haberes / Sueldo Empresa Tech",
                "timestamp": "2026-10-01T09:00:00+00:00",
            },
            {
                "tx_id": "tx_02",
                "account_id": "acc_001",
                "type": "DEBIT",
                "amount": 15400.00,
                "description": "Supermercado Coto",
                "timestamp": "2026-10-03T18:30:00+00:00",
            },
            {
                "tx_id": "tx_03",
                "account_id": "acc_001",
                "type": "DEBIT",
                "amount": 6500.00,
                "description": "Servicios Públicos - Luz y Agua",
                "timestamp": "2026-10-05T10:15:00+00:00",
            },
            {
                "tx_id": "tx_04",
                "account_id": "acc_001",
                "type": "DEBIT",
                "amount": 4200.00,
                "description": "Suscripción Streaming",
                "timestamp": "2026-10-07T21:00:00+00:00",
            },
            {
                "tx_id": "tx_05",
                "account_id": "acc_002",
                "type": "CREDIT",
                "amount": 500.00,
                "description": "Transferencia Internacional Recibida",
                "timestamp": "2026-10-08T12:00:00+00:00",
            },
        ]
        self.idempotency_store = {}
        self.daily_transfers = {}
        self.address_book = {}
        self.direct_debits = {}

    def reset_state(self):
        """Resets the storage to default seed state (used in automated test suites)."""
        with self._lock:
            self._seed_default_data()

    def get_account(self, account_id: str) -> Optional[AccountRecord]:
        with self._lock:
            return self.accounts.get(account_id)

    def get_accounts_by_user_id(self, user_id: str) -> List[AccountRecord]:
        with self._lock:
            return [account for account in self.accounts.values() if account.owner_id == user_id]

    def get_account_balances(self, user_id: str) -> List[Dict[str, Any]]:
        """Read-only: account summaries and current balances of a user."""
        with self._lock:
            return [
                {
                    "account_id": acc.account_id,
                    "account_number": acc.account_number,
                    "owner_name": acc.owner_name,
                    "balance": acc.balance,
                    "currency": acc.currency,
                }
                for acc in self.accounts.values()
                if acc.owner_id == user_id
            ]

    def get_recent_transactions(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Read-only: most recent movements (seeded history plus executed transfers) of a user's accounts."""
        with self._lock:
            account_ids = {acc.account_id for acc in self.accounts.values() if acc.owner_id == user_id}
            movements = [dict(tx) for tx in self.history if tx["account_id"] in account_ids]
            movements += [
                {
                    "tx_id": e["transaction_id"],
                    "account_id": e["account_id"],
                    "type": e["type"],
                    "amount": e["amount"],
                    "description": f"Transferencia enviada a {e['destination']} ({e['concept']})",
                    "timestamp": e["timestamp"],
                }
                for e in self.ledger
                if e["user_id"] == user_id
            ]
            movements.sort(key=lambda m: m["timestamp"], reverse=True)
            return movements[: max(int(limit), 0)]

    def get_user_daily_total(self, user_id: str, date_str: Optional[str] = None) -> float:
        d = date_str or _current_date_str()
        with self._lock:
            return self.daily_transfers.get(user_id, {}).get(d, 0.0)

    # -------------------------------------------------------------------------
    # 1. ATOMIC BANK TRANSFER WITH IDEMPOTENCY
    # -------------------------------------------------------------------------
    def execute_bank_transfer(
        self,
        user_id: str,
        source_account_id: str,
        destination_cbu_or_alias: str,
        amount: float,
        currency: str = "ARS",
        concept: str = "VARIOS",
        idempotency_key: str = "",
    ) -> TransferReceipt:
        """
        Executes an atomic bank transfer. Deducts balance, updates daily usage,
        and saves idempotency record under an RLock.
        """
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("La clave de idempotencia (idempotency_key) es obligatoria.")

        with self._lock:
            # 1. Idempotency Check
            if idempotency_key in self.idempotency_store:
                existing = self.idempotency_store[idempotency_key]
                return TransferReceipt(
                    status="ALREADY_PROCESSED",
                    transaction_id=existing.transaction_id,
                    receipt_number=existing.receipt_number,
                    user_id=existing.user_id,
                    source_account_id=existing.source_account_id,
                    destination_cbu_or_alias=existing.destination_cbu_or_alias,
                    amount=existing.amount,
                    currency=existing.currency,
                    concept=existing.concept,
                    remaining_balance=existing.remaining_balance,
                    timestamp=existing.timestamp,
                    idempotency_key=existing.idempotency_key,
                )

            # 2. Account verification
            account = self.accounts.get(source_account_id)
            if not account:
                raise ValueError(f"Cuenta de origen '{source_account_id}' no encontrada.")
            if account.owner_id != user_id:
                raise ValueError(f"El usuario '{user_id}' no es titular de la cuenta '{source_account_id}'.")
            if account.currency.upper() != currency.upper():
                raise ValueError(
                    f"Discrepancia de moneda. La cuenta está en {account.currency} pero la transferencia solicita {currency}."
                )

            # 3. Validations
            validate_transfer_amount(amount)
            normalized_destination = validate_destination_cbu_or_alias(destination_cbu_or_alias)

            today = _current_date_str()
            current_daily = self.get_user_daily_total(user_id, today)
            validate_daily_limit(amount, current_daily, settings.daily_transfer_limit)

            # 4. Solvency check
            if account.balance < amount:
                raise ValueError(
                    f"Fondos insuficientes en la cuenta '{source_account_id}'. "
                    f"Saldo disponible: ${account.balance:,.2f} {account.currency}, Monto requerido: ${amount:,.2f}."
                )

            # 5. Atomic debit
            account.balance = round(account.balance - amount, 2)

            # 6. Update daily usage
            if user_id not in self.daily_transfers:
                self.daily_transfers[user_id] = {}
            self.daily_transfers[user_id][today] = round(current_daily + amount, 2)

            # 7. Generate ledger and receipt
            tx_id = f"tx_mcp_{uuid.uuid4().hex[:10]}"
            receipt_no = f"REC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            now_iso = _current_timestamp()

            ledger_entry = {
                "transaction_id": tx_id,
                "receipt_number": receipt_no,
                "user_id": user_id,
                "account_id": source_account_id,
                "type": "DEBIT",
                "amount": amount,
                "currency": currency.upper(),
                "destination": normalized_destination,
                "concept": concept,
                "balance_after": account.balance,
                "timestamp": now_iso,
                "idempotency_key": idempotency_key,
            }
            self.ledger.append(ledger_entry)

            receipt = TransferReceipt(
                status="EXECUTED",
                transaction_id=tx_id,
                receipt_number=receipt_no,
                user_id=user_id,
                source_account_id=source_account_id,
                destination_cbu_or_alias=normalized_destination,
                amount=amount,
                currency=currency.upper(),
                concept=concept,
                remaining_balance=account.balance,
                timestamp=now_iso,
                idempotency_key=idempotency_key,
            )

            # Cache idempotency
            self.idempotency_store[idempotency_key] = receipt
            return receipt

    # -------------------------------------------------------------------------
    # 2. REGISTER DESTINATION ACCOUNT (ADDRESS BOOK)
    # -------------------------------------------------------------------------
    def register_destination_account(
        self,
        user_id: str,
        cbu_or_cvu: str,
        alias: Optional[str],
        holder_name: str,
        bank_name: str,
    ) -> RegisteredAccountReceipt:
        """
        Registers a new recipient into the user's agenda / address book.
        """
        validate_cbu_or_cvu(cbu_or_cvu)
        cleaned_alias = None
        if alias and alias.strip():
            validate_alias(alias)
            cleaned_alias = alias.strip().lower()

        if not holder_name or len(holder_name.strip()) < 2:
            raise ValueError("El nombre del titular debe contener al menos 2 caracteres.")
        if not bank_name or len(bank_name.strip()) < 2:
            raise ValueError("El nombre del banco/entidad debe contener al menos 2 caracteres.")

        with self._lock:
            recipients = self.address_book.setdefault(user_id, [])

            # Check for existing duplicate by CBU
            for rec in recipients:
                if rec["cbu_or_cvu"] == cbu_or_cvu:
                    # Update metadata and return
                    rec["holder_name"] = holder_name.strip()
                    rec["bank_name"] = bank_name.strip()
                    if cleaned_alias:
                        rec["alias"] = cleaned_alias
                    return RegisteredAccountReceipt(
                        status="REGISTERED",
                        recipient_id=rec["recipient_id"],
                        user_id=user_id,
                        cbu_or_cvu=rec["cbu_or_cvu"],
                        alias=rec.get("alias"),
                        holder_name=rec["holder_name"],
                        bank_name=rec["bank_name"],
                        created_at=rec["created_at"],
                    )

            recipient_id = f"rec_{uuid.uuid4().hex[:8]}"
            now_iso = _current_timestamp()
            new_record = {
                "recipient_id": recipient_id,
                "user_id": user_id,
                "cbu_or_cvu": cbu_or_cvu.strip(),
                "alias": cleaned_alias,
                "holder_name": holder_name.strip(),
                "bank_name": bank_name.strip(),
                "created_at": now_iso,
            }
            recipients.append(new_record)

            return RegisteredAccountReceipt(
                status="REGISTERED",
                recipient_id=recipient_id,
                user_id=user_id,
                cbu_or_cvu=new_record["cbu_or_cvu"],
                alias=new_record["alias"],
                holder_name=new_record["holder_name"],
                bank_name=new_record["bank_name"],
                created_at=now_iso,
            )

    # -------------------------------------------------------------------------
    # 3. DIRECT DEBIT SCHEDULING & CANCELLATION
    # -------------------------------------------------------------------------
    def schedule_direct_debit(
        self,
        user_id: str,
        account_id: str,
        service_provider: str,
        customer_reference: str,
        max_amount: float,
        frequency: str = "MONTHLY",
    ) -> ScheduledDebitReceipt:
        """
        Schedules a new recurring direct debit on the client's bank account.
        """
        if max_amount <= 0:
            raise ValueError(f"El tope máximo para débito directo debe ser positivo. Recibido: {max_amount}")
        if not service_provider or len(service_provider.strip()) < 2:
            raise ValueError("El proveedor del servicio debe contener al menos 2 caracteres.")
        if not customer_reference or len(customer_reference.strip()) < 1:
            raise ValueError("La referencia de cliente es obligatoria.")

        valid_frequencies = {"MONTHLY", "BIWEEKLY", "WEEKLY", "ANNUAL"}
        freq_upper = frequency.strip().upper()
        if freq_upper not in valid_frequencies:
            raise ValueError(f"Frecuencia '{frequency}' inválida. Debe ser una de: {valid_frequencies}")

        with self._lock:
            account = self.accounts.get(account_id)
            if not account:
                raise ValueError(f"Cuenta '{account_id}' no encontrada.")
            if account.owner_id != user_id:
                raise ValueError(f"El usuario '{user_id}' no es titular de la cuenta '{account_id}'.")

            debit_id = f"deb_{uuid.uuid4().hex[:8]}"
            now_iso = _current_timestamp()

            debit_record = {
                "debit_id": debit_id,
                "user_id": user_id,
                "account_id": account_id,
                "service_provider": service_provider.strip(),
                "customer_reference": customer_reference.strip(),
                "max_amount": round(float(max_amount), 2),
                "frequency": freq_upper,
                "status": "ACTIVE",
                "created_at": now_iso,
            }
            self.direct_debits[debit_id] = debit_record

            return ScheduledDebitReceipt(
                status="SCHEDULED",
                debit_id=debit_id,
                user_id=user_id,
                account_id=account_id,
                service_provider=debit_record["service_provider"],
                customer_reference=debit_record["customer_reference"],
                max_amount=debit_record["max_amount"],
                frequency=freq_upper,
                created_at=now_iso,
            )

    def cancel_scheduled_debit(self, user_id: str, debit_id: str) -> CancelDebitReceipt:
        """
        Cancels an existing active direct debit.
        """
        with self._lock:
            if debit_id not in self.direct_debits:
                raise ValueError(f"Débito directo con ID '{debit_id}' no encontrado.")

            record = self.direct_debits[debit_id]
            if record["user_id"] != user_id:
                raise ValueError(f"El débito '{debit_id}' no pertenece al usuario '{user_id}'.")
            if record["status"] == "CANCELLED":
                raise ValueError(f"El débito '{debit_id}' ya se encuentra cancelado.")

            record["status"] = "CANCELLED"
            now_iso = _current_timestamp()
            record["cancelled_at"] = now_iso

            return CancelDebitReceipt(
                status="CANCELLED",
                debit_id=debit_id,
                user_id=user_id,
                cancelled_at=now_iso,
            )


# Global singleton storage instance
financial_storage = FinancialStorage()
