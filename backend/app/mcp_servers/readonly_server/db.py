"""
SQLite Relational Database & Query Layer for Financial Read-Only Server.
Enforces parameterized, injection-safe read queries over financial core data:
- accounts
- transactions
- credit_scorings
"""
import os
import sqlite3
from typing import List, Dict, Any, Optional

DEFAULT_DB_PATH = os.getenv(
    "READONLY_DB_PATH",
    os.path.join(os.path.dirname(__file__), "readonly_banking.db")
)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    account_number TEXT NOT NULL,
    balance REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ARS',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    account_type TEXT NOT NULL DEFAULT 'SAVINGS'
);

CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('CREDIT', 'DEBIT')),
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ARS',
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);

CREATE TABLE IF NOT EXISTS credit_scorings (
    user_id TEXT PRIMARY KEY,
    score INTEGER NOT NULL CHECK(score >= 300 AND score <= 950),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('BAJO', 'MEDIO', 'ALTO')),
    debt_to_income_ratio REAL NOT NULL,
    payment_history_score REAL NOT NULL,
    credit_utilization REAL NOT NULL,
    last_updated TEXT NOT NULL
);
"""


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Returns a SQLite connection with Row factory and foreign keys enabled."""
    target_path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes SQLite schema for accounts, transactions, and credit_scorings."""
    target_path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with get_db_connection(target_path) as conn:
        conn.executescript(CREATE_TABLES_SQL)
        conn.commit()


def ensure_db_initialized(db_path: Optional[str] = None) -> None:
    """Ensures database tables exist and are populated if empty."""
    target_path = db_path or DEFAULT_DB_PATH
    if not os.path.exists(target_path):
        init_db(target_path)
        from app.mcp_servers.readonly_server.seed_db import seed_database
        seed_database(target_path)
    else:
        # Check if tables exist
        with get_db_connection(target_path) as conn:
            cursor = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='accounts';"
            )
            count = cursor.fetchone()[0]
            if count == 0:
                init_db(target_path)
                from app.mcp_servers.readonly_server.seed_db import seed_database
                seed_database(target_path)


def query_accounts(
    user_id: str,
    account_id: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Queries accounts belonging to user_id, optionally filtered by account_id.
    Fully parameterized to prevent SQL injection.
    """
    ensure_db_initialized(db_path)
    target_path = db_path or DEFAULT_DB_PATH

    query = "SELECT account_id, user_id, owner_name, account_number, balance, currency, status, account_type FROM accounts WHERE user_id = ?"
    params: List[Any] = [user_id]

    if account_id:
        query += " AND account_id = ?"
        params.append(account_id)

    query += " ORDER BY account_id ASC;"

    with get_db_connection(target_path) as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def query_transactions(
    user_id: str,
    limit: int = 20,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Queries transaction history for user_id with pagination and optional filters.
    Fully parameterized to prevent SQL injection.
    """
    ensure_db_initialized(db_path)
    target_path = db_path or DEFAULT_DB_PATH

    query = (
        "SELECT tx_id, account_id, user_id, type, amount, currency, category, description, timestamp "
        "FROM transactions WHERE user_id = ?"
    )
    params: List[Any] = [user_id]

    if start_date:
        query += " AND timestamp >= ?"
        params.append(start_date)

    if end_date:
        query += " AND timestamp <= ?"
        params.append(end_date)

    if category:
        query += " AND UPPER(category) = UPPER(?)"
        params.append(category)

    query += " ORDER BY timestamp DESC LIMIT ?;"
    params.append(max(1, limit))

    with get_db_connection(target_path) as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def query_credit_scoring(
    user_id: str,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Queries credit scoring metrics for user_id.
    Fully parameterized to prevent SQL injection.
    """
    ensure_db_initialized(db_path)
    target_path = db_path or DEFAULT_DB_PATH

    query = (
        "SELECT user_id, score, risk_level, debt_to_income_ratio, "
        "payment_history_score, credit_utilization, last_updated "
        "FROM credit_scorings WHERE user_id = ?;"
    )

    with get_db_connection(target_path) as conn:
        cursor = conn.execute(query, [user_id])
        row = cursor.fetchone()
        return dict(row) if row else None


def query_financial_summary(
    user_id: str,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes a consolidated financial summary for user_id:
    total balances by currency, total income (CREDIT), total expenses (DEBIT),
    net cash flow, spending breakdown by category, and credit scoring overview.
    """
    accounts = query_accounts(user_id=user_id, db_path=db_path)
    transactions = query_transactions(user_id=user_id, limit=500, db_path=db_path)
    scoring = query_credit_scoring(user_id=user_id, db_path=db_path)

    if not accounts and not transactions and not scoring:
        return {
            "user_id": user_id,
            "accounts_count": 0,
            "balances_by_currency": {},
            "total_income": 0.0,
            "total_expenses": 0.0,
            "net_cash_flow": 0.0,
            "credit_score": None,
            "risk_level": "NO_DISPONIBLE",
            "spending_by_category": {},
            "message": f"No se registraron cuentas ni transacciones para el usuario '{user_id}'.",
        }

    # Aggregate balances by currency
    balances_by_currency: Dict[str, float] = {}
    for acc in accounts:
        curr = acc["currency"]
        balances_by_currency[curr] = round(balances_by_currency.get(curr, 0.0) + float(acc["balance"]), 2)

    # Aggregate transactions
    total_income = 0.0
    total_expenses = 0.0
    spending_by_category: Dict[str, float] = {}

    for tx in transactions:
        amount = float(tx["amount"])
        tx_type = tx["type"]
        category = tx["category"]

        if tx_type == "CREDIT":
            total_income += amount
        elif tx_type == "DEBIT":
            total_expenses += amount
            spending_by_category[category] = round(spending_by_category.get(category, 0.0) + amount, 2)

    total_income = round(total_income, 2)
    total_expenses = round(total_expenses, 2)
    net_cash_flow = round(total_income - total_expenses, 2)

    return {
        "user_id": user_id,
        "accounts_count": len(accounts),
        "balances_by_currency": balances_by_currency,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_cash_flow": net_cash_flow,
        "credit_score": scoring["score"] if scoring else None,
        "risk_level": scoring["risk_level"] if scoring else "NO_DISPONIBLE",
        "spending_by_category": spending_by_category,
        "accounts": [
            {
                "account_id": a["account_id"],
                "account_number": a["account_number"],
                "balance": a["balance"],
                "currency": a["currency"],
                "status": a["status"],
            }
            for a in accounts
        ]
    }
