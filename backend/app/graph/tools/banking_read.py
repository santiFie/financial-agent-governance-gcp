"""
Read-Only Banking Tools.
These tools only query account information and statement data.
They have zero modification capabilities, enforcing the Least-Privilege principle.
"""
from typing import List, Dict, Any
from langchain_core.tools import tool
from app.core.mcp_action_client import mcp_action_client


@tool
def get_account_balances(user_id: str) -> Any:
    """
    Consulta los saldos actuales de todas las cuentas bancarias asociadas al usuario.
    Solo tiene permisos de lectura sobre la base de datos de saldos.
    """
    balances = mcp_action_client.read_account_balances(user_id=user_id)
    return balances if balances else "No se encontraron cuentas bancarias para el usuario especificado."


@tool
def get_recent_transactions(user_id: str, limit: int = 5) -> Any:
    """
    Consulta los últimos movimientos y transferencias realizadas o recibidas por el usuario.
    Permiso de solo lectura.
    """
    txs = mcp_action_client.read_recent_transactions(user_id=user_id, limit=limit)
    return txs if txs else "No se encontraron movimientos registrados para el usuario especificado."
