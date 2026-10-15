"""
Comprehensive Test Suite for FastMCP Read-Only Server and Read-Only Agent Integration.
Validates:
1. SQLite schema creation and idempotent seed data.
2. Unit behavior of all FastMCP read tools with filters, pagination, and nonexistent user handling.
3. FastMCP server protocol tool invocation (mcp.call_tool, mcp.list_tools).
4. Read-Only Agent integration, Least Privilege enforcement, and LangChain tool bindings.
"""
import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage

from app.mcp_servers.readonly_server.db import (
    init_db,
    get_db_connection,
    query_accounts,
    query_transactions,
    query_credit_scoring,
    query_financial_summary,
)
from app.mcp_servers.readonly_server.seed_db import seed_database
from app.mcp_servers.readonly_server.server import (
    mcp,
    get_account_balance,
    get_transaction_history,
    get_credit_scoring,
    get_financial_summary,
)
from app.graph.tools.banking_read import (
    get_account_balance as lc_get_account_balance,
    get_account_balances as lc_get_account_balances,
    get_recent_transactions as lc_get_recent_transactions,
    get_transaction_history as lc_get_transaction_history,
    get_credit_scoring as lc_get_credit_scoring,
    get_financial_summary as lc_get_financial_summary,
)
from app.graph.nodes.readonly_agent import readonly_agent_node, READONLY_TOOLS, TOOL_MAP


# =====================================================================
# 1. Tests de Base de Datos y Seed
# =====================================================================
class TestDatabaseAndSeed:
    """Verifica creación de tablas, esquema e inserción idempotente."""

    def test_schema_creation_and_tables_exist(self, tmp_path):
        test_db = str(tmp_path / "test_schema.db")
        init_db(test_db)

        with get_db_connection(test_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cursor.fetchall()}

            assert "accounts" in tables
            assert "transactions" in tables
            assert "credit_scorings" in tables

            # Verify accounts columns
            cursor.execute("PRAGMA table_info(accounts);")
            acc_cols = {col[1] for col in cursor.fetchall()}
            expected_acc_cols = {
                "account_id", "user_id", "owner_name", "account_number",
                "balance", "currency", "status", "account_type"
            }
            assert expected_acc_cols.issubset(acc_cols)

            # Verify transactions columns
            cursor.execute("PRAGMA table_info(transactions);")
            tx_cols = {col[1] for col in cursor.fetchall()}
            expected_tx_cols = {
                "tx_id", "account_id", "user_id", "type", "amount",
                "currency", "category", "description", "timestamp"
            }
            assert expected_tx_cols.issubset(tx_cols)

            # Verify credit_scorings columns
            cursor.execute("PRAGMA table_info(credit_scorings);")
            score_cols = {col[1] for col in cursor.fetchall()}
            expected_score_cols = {
                "user_id", "score", "risk_level", "debt_to_income_ratio",
                "payment_history_score", "credit_utilization", "last_updated"
            }
            assert expected_score_cols.issubset(score_cols)

    def test_seed_database_is_idempotent(self, tmp_path):
        test_db = str(tmp_path / "test_seed.db")

        # First run
        counts_first = seed_database(test_db)
        assert counts_first["accounts"] > 0
        assert counts_first["transactions"] > 0
        assert counts_first["scorings"] > 0

        with get_db_connection(test_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM accounts;")
            accounts_count_1 = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM transactions;")
            tx_count_1 = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM credit_scorings;")
            score_count_1 = cursor.fetchone()[0]

        # Second run on the same DB
        counts_second = seed_database(test_db)
        assert counts_second == counts_first

        with get_db_connection(test_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM accounts;")
            accounts_count_2 = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM transactions;")
            tx_count_2 = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM credit_scorings;")
            score_count_2 = cursor.fetchone()[0]

        # Idempotency assertion: count must not increase
        assert accounts_count_1 == accounts_count_2
        assert tx_count_1 == tx_count_2
        assert score_count_1 == score_count_2

    def test_seed_contains_all_expected_clients(self, tmp_path):
        test_db = str(tmp_path / "test_clients.db")
        seed_database(test_db)

        for client_id in ["user_default_01", "user_cliente_02", "user_empresa_03"]:
            accounts = query_accounts(client_id, db_path=test_db)
            assert len(accounts) >= 1
            txs = query_transactions(client_id, db_path=test_db)
            assert len(txs) >= 1
            score = query_credit_scoring(client_id, db_path=test_db)
            assert score is not None
            assert 300 <= score["score"] <= 950
            assert score["risk_level"] in ("BAJO", "MEDIO", "ALTO")


# =====================================================================
# 2. Tests Unitarios de Tools FastMCP
# =====================================================================
class TestReadOnlyTools:
    """Valida cada tool individualmente con filtros, orden y casos límite."""

    def test_get_account_balance_returns_user_accounts(self):
        accounts = get_account_balance(user_id="user_default_01")
        assert len(accounts) >= 2
        currencies = {a["currency"] for a in accounts}
        assert "ARS" in currencies
        assert "USD" in currencies
        for acc in accounts:
            assert acc["user_id"] == "user_default_01"
            assert "balance" in acc
            assert "status" in acc

    def test_get_account_balance_filter_by_account_id(self):
        acc = get_account_balance(user_id="user_default_01", account_id="acc_001")
        assert len(acc) == 1
        assert acc[0]["account_id"] == "acc_001"
        assert acc[0]["currency"] == "ARS"

    def test_get_account_balance_nonexistent_user(self):
        acc = get_account_balance(user_id="user_nonexistent_xyz")
        assert acc == []

    def test_get_transaction_history_limit(self):
        txs = get_transaction_history(user_id="user_default_01", limit=2)
        assert len(txs) <= 2

    def test_get_transaction_history_category_filter(self):
        txs = get_transaction_history(user_id="user_default_01", category="SUPERMERCADO")
        assert len(txs) >= 1
        for tx in txs:
            assert tx["category"].upper() == "SUPERMERCADO"

    def test_get_transaction_history_date_filters(self):
        # Filtering strictly inside date window
        txs = get_transaction_history(
            user_id="user_default_01",
            start_date="2026-03-02T00:00:00",
            end_date="2026-03-03T23:59:59"
        )
        assert len(txs) >= 1
        for tx in txs:
            assert "2026-03-02" <= tx["timestamp"] <= "2026-03-03T23:59:59"

    def test_get_transaction_history_nonexistent_user(self):
        txs = get_transaction_history(user_id="user_nonexistent_xyz")
        assert txs == []

    def test_get_credit_scoring_success(self):
        scoring_01 = get_credit_scoring(user_id="user_default_01")
        assert scoring_01["user_id"] == "user_default_01"
        assert scoring_01["score"] == 780
        assert scoring_01["risk_level"] == "BAJO"
        assert "debt_to_income_ratio" in scoring_01
        assert "payment_history_score" in scoring_01
        assert "credit_utilization" in scoring_01

        scoring_02 = get_credit_scoring(user_id="user_cliente_02")
        assert scoring_02["score"] == 640
        assert scoring_02["risk_level"] == "MEDIO"

        scoring_03 = get_credit_scoring(user_id="user_empresa_03")
        assert scoring_03["score"] == 890
        assert scoring_03["risk_level"] == "BAJO"

    def test_get_credit_scoring_nonexistent_user(self):
        scoring = get_credit_scoring(user_id="user_nonexistent_xyz")
        assert scoring["score"] is None
        assert scoring["risk_level"] == "NO_DISPONIBLE"
        assert "error" in scoring

    def test_get_financial_summary_success(self):
        summary = get_financial_summary(user_id="user_default_01")
        assert summary["user_id"] == "user_default_01"
        assert summary["accounts_count"] >= 2
        assert "ARS" in summary["balances_by_currency"]
        assert "USD" in summary["balances_by_currency"]
        assert summary["total_income"] > 0
        assert summary["total_expenses"] > 0
        assert summary["net_cash_flow"] == round(summary["total_income"] - summary["total_expenses"], 2)
        assert summary["credit_score"] == 780
        assert summary["risk_level"] == "BAJO"
        assert isinstance(summary["spending_by_category"], dict)

    def test_get_financial_summary_nonexistent_user(self):
        summary = get_financial_summary(user_id="user_nonexistent_xyz")
        assert summary["user_id"] == "user_nonexistent_xyz"
        assert summary["accounts_count"] == 0
        assert summary["total_income"] == 0.0
        assert summary["total_expenses"] == 0.0
        assert summary["net_cash_flow"] == 0.0
        assert summary["credit_score"] is None
        assert summary["risk_level"] == "NO_DISPONIBLE"


# =====================================================================
# 3. Tests del Protocolo FastMCP
# =====================================================================
class TestFastMCPProtocol:
    """Valida la interacción a través de las primitivas de FastMCP."""

    @pytest.mark.asyncio
    async def test_fastmcp_list_tools(self):
        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert "get_account_balance" in tool_names
        assert "get_transaction_history" in tool_names
        assert "get_credit_scoring" in tool_names
        assert "get_financial_summary" in tool_names

    @pytest.mark.asyncio
    async def test_fastmcp_call_tool_account_balance(self):
        result = await mcp.call_tool("get_account_balance", {"user_id": "user_default_01"})
        assert result is not None
        # Result tuple: ([TextContent], {'result': [...]})
        content_obj = result[1]["result"]
        assert isinstance(content_obj, list)
        assert len(content_obj) >= 2
        assert content_obj[0]["user_id"] == "user_default_01"

    @pytest.mark.asyncio
    async def test_fastmcp_call_tool_credit_scoring(self):
        result = await mcp.call_tool("get_credit_scoring", {"user_id": "user_empresa_03"})
        scoring = result[1]["result"]
        assert scoring["score"] == 890
        assert scoring["risk_level"] == "BAJO"


# =====================================================================
# 4. Tests de Integración con Read-Only Agent y LangChain Tools
# =====================================================================
class TestReadOnlyAgentIntegration:
    """Valida la integración con Read-Only Agent y cumplimiento de Least Privilege."""

    def test_least_privilege_enforced_on_readonly_agent(self):
        """Asegura que el Read-Only Agent SOLO tiene herramientas de lectura."""
        tool_names = [t.name for t in READONLY_TOOLS]
        for name in tool_names:
            assert name.startswith("get_"), f"Herramienta no autorizada para Read-Only Agent: {name}"

        # Verificar que no existen herramientas de escritura o transferencias
        assert "stage_transfer_request" not in tool_names
        assert "execute_approved_transfer" not in tool_names
        assert "reject_staged_transfer" not in tool_names

    def test_langchain_tools_callable(self):
        """Verifica que las herramientas LangChain encapsuladas ejecuten correctamente."""
        accs = lc_get_account_balance.invoke({"user_id": "user_default_01"})
        assert len(accs) >= 2

        accs_alias = lc_get_account_balances.invoke({"user_id": "user_default_01"})
        assert accs == accs_alias

        txs = lc_get_recent_transactions.invoke({"user_id": "user_default_01", "limit": 3})
        assert len(txs) <= 3

        history = lc_get_transaction_history.invoke({"user_id": "user_default_01", "limit": 10})
        assert len(history) >= len(txs)

        score = lc_get_credit_scoring.invoke({"user_id": "user_default_01"})
        assert score["score"] == 780

        summary = lc_get_financial_summary.invoke({"user_id": "user_default_01"})
        assert summary["accounts_count"] >= 2

    def test_readonly_agent_node_deterministic_balance(self):
        state = {
            "current_intent": "balance",
            "user_id": "user_default_01",
            "sanitized_input": "Consultar saldo disponible"
        }
        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=None):
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "Estado de Cuentas y Saldos Disponibles" in res["final_response"]
            assert "CA-001293847-ARS" in res["final_response"]

    def test_readonly_agent_node_deterministic_transactions(self):
        state = {
            "current_intent": "transactions",
            "user_id": "user_default_01",
            "sanitized_input": "Ver mis últimos movimientos"
        }
        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=None):
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "Últimos 5 Movimientos Bancarios" in res["final_response"]

    def test_readonly_agent_node_deterministic_scoring(self):
        state = {
            "current_intent": "scoring",
            "user_id": "user_default_01",
            "sanitized_input": "¿Cuál es mi scoring crediticio?"
        }
        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=None):
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "Scoring Crediticio" in res["final_response"]
            assert "780/950" in res["final_response"]
            assert "BAJO" in res["final_response"]

    def test_readonly_agent_node_deterministic_summary(self):
        state = {
            "current_intent": "summary",
            "user_id": "user_default_01",
            "sanitized_input": "Dame un resumen financiero completo"
        }
        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=None):
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "Resumen Financiero Consolidado" in res["final_response"]
            assert "Cuentas Activas" in res["final_response"]
            assert "Flujo Neto" in res["final_response"]

    def test_readonly_agent_llm_tool_calling_credit_scoring(self):
        """Verifica ciclo completo cuando el LLM solicita tool credit scoring."""
        mock_llm = MagicMock()
        mock_bound = MagicMock()

        tool_call = {
            "name": "get_credit_scoring",
            "args": {"user_id": "user_default_01"},
            "id": "call_score_99"
        }
        first_resp = AIMessage(content="", tool_calls=[tool_call])
        second_resp = AIMessage(content="Tu scoring crediticio es excelente con 780 puntos.")

        mock_bound.invoke.return_value = first_resp
        mock_llm.bind_tools.return_value = mock_bound
        mock_llm.invoke.return_value = second_resp

        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=mock_llm):
            state = {
                "current_intent": "scoring",
                "user_id": "user_default_01",
                "sanitized_input": "¿Cuál es mi score de crédito?"
            }
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "780 puntos" in res["final_response"]
            mock_bound.invoke.assert_called_once()
            mock_llm.invoke.assert_called_once()

    def test_readonly_agent_llm_tool_calling_financial_summary(self):
        """Verifica ciclo completo cuando el LLM solicita tool financial summary."""
        mock_llm = MagicMock()
        mock_bound = MagicMock()

        tool_call = {
            "name": "get_financial_summary",
            "args": {"user_id": "user_empresa_03"},
            "id": "call_summary_99"
        }
        first_resp = AIMessage(content="", tool_calls=[tool_call])
        second_resp = AIMessage(content="El resumen financiero muestra una excelente liquidez.")

        mock_bound.invoke.return_value = first_resp
        mock_llm.bind_tools.return_value = mock_bound
        mock_llm.invoke.return_value = second_resp

        with patch("app.graph.nodes.readonly_agent.get_chat_model", return_value=mock_llm):
            state = {
                "current_intent": "summary",
                "user_id": "user_empresa_03",
                "sanitized_input": "Quiero ver mi resumen financiero general"
            }
            res = readonly_agent_node(state)
            assert res["status"] == "COMPLETED"
            assert "excelente liquidez" in res["final_response"]
            mock_bound.invoke.assert_called_once()
            mock_llm.invoke.assert_called_once()
