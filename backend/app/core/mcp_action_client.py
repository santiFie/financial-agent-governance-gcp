"""
MCP Action Client.
Integrates the Action Agent and Backend with the FastMCP Financial Action Server.
Enforces that critical tools are only executed following HITL approval.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_workspace_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from mcp_financial_action_server.server import (  # noqa: E402
    cancel_scheduled_debit,
    execute_bank_transfer,
    get_account_balances,
    get_recent_transactions,
    register_destination_account,
    schedule_direct_debit,
)

MCP_SERVER_URL = os.getenv("MCP_ACTION_SERVER_URL", "http://localhost:8001")


class MCPActionClient:
    """Client for executing asset-modifying actions via FastMCP server with governance."""

    @classmethod
    def execute_approved_transfer(
        cls,
        user_id: str,
        source_account_id: str,
        destination_cbu_or_alias: str,
        amount: float,
        currency: str = "ARS",
        concept: str = "TRANSFERENCIA_HITL",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        """
        Executes an approved transfer through the FastMCP server tools.
        Enforces that this is ONLY called after human compliance approval.
        """
        return execute_bank_transfer(
            user_id=user_id,
            source_account_id=source_account_id,
            destination_cbu_or_alias=destination_cbu_or_alias,
            amount=amount,
            currency=currency,
            concept=concept,
            idempotency_key=idempotency_key,
        )

    @classmethod
    def read_account_balances(cls, user_id: str) -> List[Dict[str, Any]]:
        """Read-only: balances of the user's accounts, served by the MCP server."""
        return get_account_balances(user_id=user_id)["accounts"]

    @classmethod
    def read_recent_transactions(cls, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Read-only: latest movements of the user's accounts, served by the MCP server."""
        return get_recent_transactions(user_id=user_id, limit=limit)["transactions"]

    @classmethod
    def register_account(
        cls,
        user_id: str,
        cbu_or_cvu: str,
        alias: Optional[str] = None,
        holder_name: str = "",
        bank_name: str = "",
    ) -> Dict[str, Any]:
        """Registers a recipient in the client's address book via MCP."""
        return register_destination_account(
            user_id=user_id,
            cbu_or_cvu=cbu_or_cvu,
            alias=alias,
            holder_name=holder_name,
            bank_name=bank_name,
        )

    @classmethod
    def schedule_debit(
        cls,
        user_id: str,
        account_id: str,
        service_provider: str,
        customer_reference: str,
        max_amount: float,
        frequency: str = "MONTHLY",
    ) -> Dict[str, Any]:
        """Schedules recurring direct debit via MCP."""
        return schedule_direct_debit(
            user_id=user_id,
            account_id=account_id,
            service_provider=service_provider,
            customer_reference=customer_reference,
            max_amount=max_amount,
            frequency=frequency,
        )

    @classmethod
    def cancel_debit(
        cls,
        user_id: str,
        debit_id: str,
    ) -> Dict[str, Any]:
        """Cancels recurring direct debit via MCP."""
        return cancel_scheduled_debit(
            user_id=user_id,
            debit_id=debit_id,
        )

    @classmethod
    def is_trusted_recipient(cls, user_id: str, destination: str, session_id: Optional[str] = None) -> "AwaitableBool":
        """
        Queries FastMCP financial_storage address book to verify if destination
        (CBU, CVU, alias, account_number, or surrogate token) is registered as a trusted recipient for user_id.
        Returns an AwaitableBool that can be both awaited (await client.is_trusted_recipient(...))
        and evaluated synchronously (if client.is_trusted_recipient(...):).
        """
        if not destination or not user_id:
            return AwaitableBool(False)

        from mcp_financial_action_server.storage import financial_storage

        from app.core.security_context import token_vault

        clean_dest = destination.strip()
        resolved_dest = None

        if clean_dest.startswith("[") and clean_dest.endswith("]"):
            if session_id:
                resolved_dest = token_vault.resolve_token(session_id, clean_dest)
            if not resolved_dest:
                with token_vault._lock:
                    for s_id, tokens in token_vault._vault.items():
                        if clean_dest in tokens:
                            resolved_dest = tokens[clean_dest]
                            break

        candidates = {clean_dest}
        if resolved_dest:
            candidates.add(resolved_dest.strip())

        with financial_storage._lock:
            recipients = financial_storage.address_book.get(user_id, [])
            for rec in recipients:
                cbu = rec.get("cbu_or_cvu", "")
                alias = rec.get("alias", "")
                acc_num = rec.get("account_number", "")
                rec_id = rec.get("recipient_id", "")
                token = rec.get("token", "")

                for candidate in candidates:
                    if candidate and (
                        candidate == cbu
                        or (alias and candidate.lower() == alias.lower())
                        or (acc_num and candidate.lower() == acc_num.lower())
                        or candidate == rec_id
                        or (token and candidate == token)
                    ):
                        return AwaitableBool(True)

        return AwaitableBool(False)


class AwaitableBool:
    """Helper enabling dual async/await and synchronous boolean evaluation."""

    def __init__(self, value: bool):
        self._val = bool(value)

    def __bool__(self) -> bool:
        return self._val

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, AwaitableBool):
            return self._val == other._val
        return self._val == bool(other)

    def __await__(self):
        async def _coro():
            return self._val

        return _coro().__await__()

    def __repr__(self) -> str:
        return repr(self._val)


# Global singleton client instance
mcp_action_client = MCPActionClient()
