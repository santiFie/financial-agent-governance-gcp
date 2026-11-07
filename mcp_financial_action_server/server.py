"""
FastMCP Financial Action Server.
Exposes critical asset-modifying tools conforming to the Model Context Protocol (MCP).
"""

from typing import Any, Dict, Optional

from fastmcp import FastMCP
from starlette.responses import JSONResponse

from mcp_financial_action_server.config import settings
from mcp_financial_action_server.storage import financial_storage

# 1. Initialize FastMCP instance
mcp = FastMCP(
    name=settings.server_name,
    instructions=(
        "Servidor transaccional MCP para la ejecución de acciones críticas que modifican activos "
        "financieros del cliente (transferencias bancarias, alta de CBUs/cuentas destino, programación "
        "y baja de débitos directos, requieren autorización previa HITL) y consultas de solo lectura de saldos y movimientos."
    ),
)


# 2. Register Critical Action Tools with MCP annotations
@mcp.tool(
    name="execute_bank_transfer",
    description=(
        "Ejecuta una transferencia bancaria descontando el saldo y registrando el comprobante de la transacción. "
        "Valida fondos suficientes, formato de CBU/Alias, límite diario de operaciones y previene duplicaciones por idempotency_key."
    ),
    annotations={"destructiveHint": True, "idempotentHint": True, "readOnlyHint": False},
)
def execute_bank_transfer(
    user_id: str,
    source_account_id: str,
    destination_cbu_or_alias: str,
    amount: float,
    currency: str = "ARS",
    concept: str = "VARIOS",
    idempotency_key: str = "",
) -> Dict[str, Any]:
    """
    Ejecuta una transferencia bancaria descontando el saldo y registrando el comprobante de la transacción.
    """
    receipt = financial_storage.execute_bank_transfer(
        user_id=user_id,
        source_account_id=source_account_id,
        destination_cbu_or_alias=destination_cbu_or_alias,
        amount=amount,
        currency=currency,
        concept=concept,
        idempotency_key=idempotency_key,
    )
    return receipt.model_dump()


@mcp.tool(
    name="register_destination_account",
    description="Da de alta un nuevo CBU/cuenta destino en la agenda de destinatarios del cliente.",
    annotations={"destructiveHint": False, "idempotentHint": True, "readOnlyHint": False},
)
def register_destination_account(
    user_id: str,
    cbu_or_cvu: str,
    alias: Optional[str] = None,
    holder_name: str = "",
    bank_name: str = "",
) -> Dict[str, Any]:
    """
    Da de alta un nuevo CBU/cuenta destino en la agenda de destinatarios del cliente.
    """
    receipt = financial_storage.register_destination_account(
        user_id=user_id,
        cbu_or_cvu=cbu_or_cvu,
        alias=alias,
        holder_name=holder_name,
        bank_name=bank_name,
    )
    return receipt.model_dump()


@mcp.tool(
    name="schedule_direct_debit",
    description="Programa o adhiere un débito automático/directo sobre la cuenta del cliente.",
    annotations={"destructiveHint": False, "idempotentHint": False, "readOnlyHint": False},
)
def schedule_direct_debit(
    user_id: str,
    account_id: str,
    service_provider: str,
    customer_reference: str,
    max_amount: float,
    frequency: str = "MONTHLY",
) -> Dict[str, Any]:
    """
    Programa o adhiere un débito automático/directo sobre la cuenta del cliente.
    """
    receipt = financial_storage.schedule_direct_debit(
        user_id=user_id,
        account_id=account_id,
        service_provider=service_provider,
        customer_reference=customer_reference,
        max_amount=max_amount,
        frequency=frequency,
    )
    return receipt.model_dump()


@mcp.tool(
    name="get_account_balances",
    description="Consulta los saldos actuales de las cuentas del cliente. Solo lectura.",
    annotations={"destructiveHint": False, "idempotentHint": True, "readOnlyHint": True},
)
def get_account_balances(user_id: str) -> Dict[str, Any]:
    """Consulta los saldos actuales de las cuentas del cliente."""
    return {"accounts": financial_storage.get_account_balances(user_id=user_id)}


@mcp.tool(
    name="get_recent_transactions",
    description="Consulta los últimos movimientos de las cuentas del cliente. Solo lectura.",
    annotations={"destructiveHint": False, "idempotentHint": True, "readOnlyHint": True},
)
def get_recent_transactions(user_id: str, limit: int = 5) -> Dict[str, Any]:
    """Consulta los últimos movimientos de las cuentas del cliente."""
    return {"transactions": financial_storage.get_recent_transactions(user_id=user_id, limit=limit)}


@mcp.tool(
    name="cancel_scheduled_debit",
    description="Permite dar de baja una programación de débito directo existente.",
    annotations={"destructiveHint": True, "idempotentHint": True, "readOnlyHint": False},
)
def cancel_scheduled_debit(
    user_id: str,
    debit_id: str,
) -> Dict[str, Any]:
    """
    Permite dar de baja una programación de débito directo existente.
    """
    receipt = financial_storage.cancel_scheduled_debit(
        user_id=user_id,
        debit_id=debit_id,
    )
    return receipt.model_dump()


# 3. Auxiliary Health Check Endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for Docker Compose and monitoring."""
    return JSONResponse(
        {
            "status": "healthy",
            "server": settings.server_name,
            "version": settings.server_version,
            "environment": settings.environment,
            "port": settings.port,
            "active_accounts": len(financial_storage.accounts),
        }
    )


# 4. ASGI HTTP Application instance
app = mcp.http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
