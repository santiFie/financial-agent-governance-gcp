"""
Read-Only Banking Tools.
These tools only query account information and statement data.
They have zero modification capabilities, enforcing the Least-Privilege principle.
"""
from typing import List, Dict, Any
from langchain_core.tools import tool
from app.db.mock_banking import mock_banking_db


@tool
def get_account_balances(user_id: str) -> Any:
    """
    Consulta los saldos actuales de todas las cuentas bancarias asociadas al usuario.
    Solo tiene permisos de lectura sobre la base de datos de saldos.
    """
    balances = mock_banking_db.get_account_balances(user_id=user_id)
    return balances if balances else "No se encontraron cuentas bancarias para el usuario especificado."


@tool
def get_recent_transactions(user_id: str, limit: int = 5) -> Any:
    """
    Consulta los últimos movimientos y transferencias realizadas o recibidas por el usuario.
    Permiso de solo lectura.
    """
    txs = mock_banking_db.get_recent_transactions(user_id=user_id, limit=limit)
    return txs if txs else "No se encontraron movimientos registrados para el usuario especificado."
