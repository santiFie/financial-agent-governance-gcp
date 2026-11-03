"""Offline tests for account reference handling in agent tools and for MCP-backed read tools."""
import pytest

from app.core.mcp_action_client import mcp_action_client
from app.core.security_context import normalize_account_reference, token_vault
from app.graph.tools.banking_read import get_account_balances, get_recent_transactions
from app.graph.tools.banking_write import register_recipient_tool, transfer_funds_tool
from mcp_financial_action_server.storage import financial_storage

CBU = "0170099900000012345678"


@pytest.fixture(autouse=True)
def clean_state():
    financial_storage.reset_state()
    yield
    financial_storage.reset_state()


@pytest.mark.parametrize("value,expected", [
    (CBU, CBU),
    ("[CBU_BANCARIO_1]", "[CBU_BANCARIO_1]"),
    ("CBU_BANCARIO_1", "[CBU_BANCARIO_1]"),
    (" [ CBU_BANCARIO_2 ] ", "[CBU_BANCARIO_2]"),
    ("mi.alias.banco", "mi.alias.banco"),
    ("", ""),
    (None, ""),
])
def test_normalize_account_reference(value, expected):
    assert normalize_account_reference(value) == expected


def test_register_recipient_accepts_token_with_or_without_brackets():
    token_vault.store_token("sess_refs", "[CBU_BANCARIO_1]", CBU)

    receipt = register_recipient_tool.invoke(
        {"user_id": "user_default_01", "recipient_name": "Proveedor", "cbu": "CBU_BANCARIO_1", "session_id": "sess_refs"}
    )

    assert receipt["cbu_or_cvu"] == CBU
    assert mcp_action_client.is_trusted_recipient("user_default_01", CBU)


def test_register_recipient_accepts_a_real_cbu():
    receipt = register_recipient_tool.invoke(
        {"user_id": "user_default_01", "recipient_name": "Proveedor", "cbu": CBU}
    )

    assert receipt["cbu_or_cvu"] == CBU


def test_transfer_tool_stages_the_normalized_destination():
    staged = transfer_funds_tool.invoke(
        {"user_id": "user_default_01", "destination_account": "CBU_BANCARIO_1", "amount": 100.0}
    )

    assert staged["destination"] == "[CBU_BANCARIO_1]"
    assert staged["status"] == "PENDING_PEP"


def test_read_tools_are_served_by_the_mcp_server():
    balances = get_account_balances.invoke({"user_id": "user_default_01"})
    assert {a["account_id"] for a in balances} == {"acc_001", "acc_002"}

    mcp_action_client.execute_approved_transfer(
        user_id="user_default_01", source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234", amount=500.0, idempotency_key="refs-read-1",
    )
    after = {a["account_id"]: a["balance"] for a in get_account_balances.invoke({"user_id": "user_default_01"})}
    assert after["acc_001"] == 484750.75

    txs = get_recent_transactions.invoke({"user_id": "user_default_01", "limit": 2})
    assert txs[0]["amount"] == 500.0


def test_read_tools_report_empty_results_for_unknown_users():
    assert "No se encontraron cuentas" in get_account_balances.invoke({"user_id": "nadie"})
