"""
FastMCP Read-Only Server for Banking Operations.
Provides secure, read-only tools for account balances, transaction history,
credit scoring, and financial summaries according to the Least Privilege principle.
Zero write permissions to any database or transfer service.
"""
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from app.mcp_servers.readonly_server.db import (
    ensure_db_initialized,
    query_accounts,
    query_transactions,
    query_credit_scoring,
    query_financial_summary,
)

# Initialize FastMCP Server with descriptive name
mcp = FastMCP("FinancialReadOnlyServer")


@mcp.tool()
def get_account_balance(
    user_id: str,
    account_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retorna los saldos actuales (en ARS y USD) y el estado de las cuentas bancarias del usuario.
    Permite filtrar opcionalmente por un identificador de cuenta específico (account_id).
    Herramienta de solo lectura (Principle of Least Privilege).
    """
    ensure_db_initialized()
    return query_accounts(user_id=user_id, account_id=account_id)


@mcp.tool()
def get_transaction_history(
    user_id: str,
    limit: int = 20,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retorna el historial de movimientos y transferencias bancarias del usuario,
    ordenadas cronológicamente de forma descendente.
    Soporta paginación mediante limit y filtrado por rango de fechas (start_date, end_date en formato ISO)
    y categoría de gasto/ingreso (e.g., 'SUPERMERCADO', 'HABERES', 'SERVICIOS', 'TRANSFERENCIA').
    Herramienta de solo lectura (Principle of Least Privilege).
    """
    ensure_db_initialized()
    return query_transactions(
        user_id=user_id,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        category=category,
    )


@mcp.tool()
def get_credit_scoring(user_id: str) -> Dict[str, Any]:
    """
    Retorna el scoring crediticio del usuario, incluyendo puntaje (300 a 950), nivel de riesgo
    ('BAJO', 'MEDIO', 'ALTO'), ratio deuda/ingreso, puntaje de historial de pago y utilización de crédito.
    Si el usuario no tiene historial crediticio registrado, retorna un reporte informativo sin fallar.
    Herramienta de solo lectura (Principle of Least Privilege).
    """
    ensure_db_initialized()
    scoring = query_credit_scoring(user_id=user_id)
    if not scoring:
        return {
            "user_id": user_id,
            "error": f"No se encontró información de scoring crediticio para el usuario '{user_id}'.",
            "score": None,
            "risk_level": "NO_DISPONIBLE",
        }
    return scoring


@mcp.tool()
def get_financial_summary(user_id: str) -> Dict[str, Any]:
    """
    Retorna un resumen consolidado de las finanzas del usuario:
    total de cuentas activas, saldos agrupados por moneda, total de ingresos acumulados,
    total de egresos, flujo neto de fondos, desglose de gastos por categoría y nivel de riesgo.
    Herramienta de solo lectura (Principle of Least Privilege).
    """
    ensure_db_initialized()
    return query_financial_summary(user_id=user_id)


if __name__ == "__main__":
    mcp.run()
