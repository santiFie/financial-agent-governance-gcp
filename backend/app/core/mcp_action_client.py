"""
MCP Action Client.
Integrates the Action Agent and Backend with the FastMCP Financial Action Server.
Enforces that critical tools are only executed following HITL approval.
"""
from typing import Dict, Any, Optional
import os

from mcp_financial_action_server.server import (
    execute_bank_transfer,
    register_destination_account,
    schedule_direct_debit,
    cancel_scheduled_debit,
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
