"""
Read-Only Banking Tools for LangGraph Agents.
Integrates directly with the FastMCP Read-Only Server (server.py).
Enforces Principle of Least Privilege: Zero write operations permitted.
"""
from typing import List, Dict, Any, Optional
from langchain_core.tools import tool

from app.mcp_servers.readonly_server.server import (
    get_account_balance as fastmcp_get_account_balance,
    get_transaction_history as fastmcp_get_transaction_history,
    get_credit_scoring as fastmcp_get_credit_scoring,
    get_financial_summary as fastmcp_get_financial_summary,
)


@tool
def get_account_balance(
    user_id: str,
    account_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Consulta los saldos actuales (ARS/USD) y estado de cuentas del usuario desde el servidor FastMCP.
    Permite filtrar opcionalmente por una cuenta específica (account_id).
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_account_balance(user_id=user_id, account_id=account_id)


@tool
def get_account_balances(
    user_id: str,
    account_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Alias compatible para consultar saldos actuales de cuentas bancarias asociadas al usuario.
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_account_balance(user_id=user_id, account_id=account_id)


@tool
def get_transaction_history(
    user_id: str,
    limit: int = 20,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Consulta el historial de movimientos y transacciones bancarias del usuario con paginación
    y filtros opcionales por rango de fechas (ISO 8601) y categoría de gasto/ingreso.
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_transaction_history(
        user_id=user_id,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        category=category,
    )


@tool
def get_recent_transactions(
    user_id: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Consulta los últimos movimientos bancarios realizados o recibidos por el usuario.
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_transaction_history(user_id=user_id, limit=limit)


@tool
def get_credit_scoring(
    user_id: str
) -> Dict[str, Any]:
    """
    Consulta el scoring crediticio, nivel de riesgo e indicadores financieros del usuario.
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_credit_scoring(user_id=user_id)


@tool
def get_financial_summary(
    user_id: str
) -> Dict[str, Any]:
    """
    Consulta el resumen financiero consolidado de ingresos, egresos, liquidez y riesgo del usuario.
    Permiso exclusivo de solo lectura.
    """
    return fastmcp_get_financial_summary(user_id=user_id)
