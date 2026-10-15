"""
Database Seeding Script for Financial Read-Only Server.
Idempotent script to populate SQLite database with realistic financial data
for testing and local development (user_default_01, user_cliente_02, user_empresa_03).
Can be executed directly: python -m app.mcp_servers.readonly_server.seed_db
"""
import sys
import os
from typing import Optional, Dict, Any

# Ensure backend root is on sys.path when executed directly
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.mcp_servers.readonly_server.db import (
    DEFAULT_DB_PATH,
    init_db,
    get_db_connection,
)

SAMPLE_ACCOUNTS = [
    # user_default_01 (Santiago Gómez) - Retail Personal
    {
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "owner_name": "Santiago Gómez",
        "account_number": "CA-001293847-ARS",
        "balance": 485250.75,
        "currency": "ARS",
        "status": "ACTIVE",
        "account_type": "CAJA_AHORRO",
    },
    {
        "account_id": "acc_002",
        "user_id": "user_default_01",
        "owner_name": "Santiago Gómez",
        "account_number": "CC-009847321-USD",
        "balance": 1520.00,
        "currency": "USD",
        "status": "ACTIVE",
        "account_type": "CUENTA_CORRIENTE",
    },
    # user_cliente_02 (Mariana López) - Retail Professional
    {
        "account_id": "acc_003",
        "user_id": "user_cliente_02",
        "owner_name": "Mariana López",
        "account_number": "CA-004455667-ARS",
        "balance": 125000.50,
        "currency": "ARS",
        "status": "ACTIVE",
        "account_type": "CAJA_AHORRO",
    },
    {
        "account_id": "acc_004",
        "user_id": "user_cliente_02",
        "owner_name": "Mariana López",
        "account_number": "CC-004455668-USD",
        "balance": 350.00,
        "currency": "USD",
        "status": "ACTIVE",
        "account_type": "CUENTA_CORRIENTE",
    },
    # user_empresa_03 (Tech Solutions S.A.) - Corporate Account
    {
        "account_id": "acc_005",
        "user_id": "user_empresa_03",
        "owner_name": "Tech Solutions S.A.",
        "account_number": "CC-008899112-ARS",
        "balance": 4500000.00,
        "currency": "ARS",
        "status": "ACTIVE",
        "account_type": "CUENTA_CORRIENTE",
    },
    {
        "account_id": "acc_006",
        "user_id": "user_empresa_03",
        "owner_name": "Tech Solutions S.A.",
        "account_number": "CC-008899113-USD",
        "balance": 35000.00,
        "currency": "USD",
        "status": "ACTIVE",
        "account_type": "CUENTA_CORRIENTE",
    },
]

SAMPLE_TRANSACTIONS = [
    # user_default_01
    {
        "tx_id": "tx_001",
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "type": "CREDIT",
        "amount": 350000.00,
        "currency": "ARS",
        "category": "HABERES",
        "description": "Depósito de Haberes / Sueldo Empresa Tech",
        "timestamp": "2026-03-01T09:00:00",
    },
    {
        "tx_id": "tx_002",
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "type": "DEBIT",
        "amount": 15400.00,
        "currency": "ARS",
        "category": "SUPERMERCADO",
        "description": "Supermercado Coto",
        "timestamp": "2026-03-02T14:30:00",
    },
    {
        "tx_id": "tx_003",
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "type": "DEBIT",
        "amount": 6500.00,
        "currency": "ARS",
        "category": "SERVICIOS",
        "description": "Servicios Públicos - Luz y Agua",
        "timestamp": "2026-03-03T11:15:00",
    },
    {
        "tx_id": "tx_004",
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "type": "DEBIT",
        "amount": 28000.00,
        "currency": "ARS",
        "category": "TARJETA",
        "description": "Pago resumen Tarjeta Visa",
        "timestamp": "2026-03-05T18:20:00",
    },
    {
        "tx_id": "tx_005",
        "account_id": "acc_001",
        "user_id": "user_default_01",
        "type": "CREDIT",
        "amount": 50000.00,
        "currency": "ARS",
        "category": "TRANSFERENCIA",
        "description": "Transferencia recibida de Martín Pérez",
        "timestamp": "2026-03-10T16:00:00",
    },
    {
        "tx_id": "tx_006",
        "account_id": "acc_002",
        "user_id": "user_default_01",
        "type": "CREDIT",
        "amount": 1000.00,
        "currency": "USD",
        "category": "TRANSFERENCIA",
        "description": "Transferencia Internacional SWIFT",
        "timestamp": "2026-03-04T10:00:00",
    },
    {
        "tx_id": "tx_007",
        "account_id": "acc_002",
        "user_id": "user_default_01",
        "type": "DEBIT",
        "amount": 50.00,
        "currency": "USD",
        "category": "SERVICIOS",
        "description": "Suscripción Cloud AWS/GCP",
        "timestamp": "2026-03-08T08:00:00",
    },
    # user_cliente_02
    {
        "tx_id": "tx_008",
        "account_id": "acc_003",
        "user_id": "user_cliente_02",
        "type": "CREDIT",
        "amount": 180000.00,
        "currency": "ARS",
        "category": "HABERES",
        "description": "Sueldo Docente / Ministerio de Educación",
        "timestamp": "2026-03-01T08:30:00",
    },
    {
        "tx_id": "tx_009",
        "account_id": "acc_003",
        "user_id": "user_cliente_02",
        "type": "DEBIT",
        "amount": 22000.00,
        "currency": "ARS",
        "category": "SUPERMERCADO",
        "description": "Supermercado Carrefour",
        "timestamp": "2026-03-03T19:40:00",
    },
    {
        "tx_id": "tx_010",
        "account_id": "acc_003",
        "user_id": "user_cliente_02",
        "type": "DEBIT",
        "amount": 12500.00,
        "currency": "ARS",
        "category": "FARMACIA",
        "description": "Farmacity medicamentos y cuidado personal",
        "timestamp": "2026-03-06T15:10:00",
    },
    {
        "tx_id": "tx_011",
        "account_id": "acc_003",
        "user_id": "user_cliente_02",
        "type": "DEBIT",
        "amount": 45000.00,
        "currency": "ARS",
        "category": "ALQUILER",
        "description": "Pago alquiler departamento",
        "timestamp": "2026-03-07T12:00:00",
    },
    # user_empresa_03
    {
        "tx_id": "tx_012",
        "account_id": "acc_005",
        "user_id": "user_empresa_03",
        "type": "CREDIT",
        "amount": 2500000.00,
        "currency": "ARS",
        "category": "COBROS",
        "description": "Cobro Factura A 0001-00042 cliente corporativo",
        "timestamp": "2026-03-02T11:00:00",
    },
    {
        "tx_id": "tx_013",
        "account_id": "acc_005",
        "user_id": "user_empresa_03",
        "type": "DEBIT",
        "amount": 850000.00,
        "currency": "ARS",
        "category": "PROVEEDORES",
        "description": "Pago a proveedores de infraestructura datacenter",
        "timestamp": "2026-03-04T16:30:00",
    },
    {
        "tx_id": "tx_014",
        "account_id": "acc_005",
        "user_id": "user_empresa_03",
        "type": "DEBIT",
        "amount": 320000.00,
        "currency": "ARS",
        "category": "IMPUESTOS",
        "description": "AFIP VEP Impuesto a las Ganancias",
        "timestamp": "2026-03-09T09:15:00",
    },
    {
        "tx_id": "tx_015",
        "account_id": "acc_006",
        "user_id": "user_empresa_03",
        "type": "CREDIT",
        "amount": 15000.00,
        "currency": "USD",
        "category": "EXPORTACIONES",
        "description": "Cobro exportación de servicios software Estados Unidos",
        "timestamp": "2026-03-05T14:00:00",
    },
    {
        "tx_id": "tx_016",
        "account_id": "acc_006",
        "user_id": "user_empresa_03",
        "type": "DEBIT",
        "amount": 2500.00,
        "currency": "USD",
        "category": "SERVICIOS",
        "description": "Licencias SaaS GitHub Enterprise y Atlassian Jira",
        "timestamp": "2026-03-08T17:00:00",
    },
]

SAMPLE_SCORINGS = [
    {
        "user_id": "user_default_01",
        "score": 780,
        "risk_level": "BAJO",
        "debt_to_income_ratio": 0.18,
        "payment_history_score": 0.98,
        "credit_utilization": 0.22,
        "last_updated": "2026-03-15T10:00:00",
    },
    {
        "user_id": "user_cliente_02",
        "score": 640,
        "risk_level": "MEDIO",
        "debt_to_income_ratio": 0.38,
        "payment_history_score": 0.85,
        "credit_utilization": 0.55,
        "last_updated": "2026-03-15T10:00:00",
    },
    {
        "user_id": "user_empresa_03",
        "score": 890,
        "risk_level": "BAJO",
        "debt_to_income_ratio": 0.12,
        "payment_history_score": 0.99,
        "credit_utilization": 0.15,
        "last_updated": "2026-03-15T10:00:00",
    },
]


def seed_database(db_path: Optional[str] = None) -> Dict[str, int]:
    """
    Idempotent seeding function.
    Inserts or updates accounts, transactions, and credit scorings.
    """
    target_path = db_path or DEFAULT_DB_PATH
    init_db(target_path)

    with get_db_connection(target_path) as conn:
        # 1. Accounts
        conn.executemany(
            """
            INSERT OR REPLACE INTO accounts
            (account_id, user_id, owner_name, account_number, balance, currency, status, account_type)
            VALUES (:account_id, :user_id, :owner_name, :account_number, :balance, :currency, :status, :account_type);
            """,
            SAMPLE_ACCOUNTS,
        )

        # 2. Transactions
        conn.executemany(
            """
            INSERT OR REPLACE INTO transactions
            (tx_id, account_id, user_id, type, amount, currency, category, description, timestamp)
            VALUES (:tx_id, :account_id, :user_id, :type, :amount, :currency, :category, :description, :timestamp);
            """,
            SAMPLE_TRANSACTIONS,
        )

        # 3. Credit Scorings
        conn.executemany(
            """
            INSERT OR REPLACE INTO credit_scorings
            (user_id, score, risk_level, debt_to_income_ratio, payment_history_score, credit_utilization, last_updated)
            VALUES (:user_id, :score, :risk_level, :debt_to_income_ratio, :payment_history_score, :credit_utilization, :last_updated);
            """,
            SAMPLE_SCORINGS,
        )

        conn.commit()

    return {
        "accounts": len(SAMPLE_ACCOUNTS),
        "transactions": len(SAMPLE_TRANSACTIONS),
        "scorings": len(SAMPLE_SCORINGS),
    }


if __name__ == "__main__":
    db_file = DEFAULT_DB_PATH
    print(f"[*] Seeding SQLite Financial Read-Only database at: {db_file}")
    counts = seed_database(db_file)
    print(f"[+] Seeding completed successfully:")
    print(f"    - {counts['accounts']} Cuentas registradas.")
    print(f"    - {counts['transactions']} Transacciones registradas.")
    print(f"    - {counts['scorings']} Registros de scoring crediticio.")
