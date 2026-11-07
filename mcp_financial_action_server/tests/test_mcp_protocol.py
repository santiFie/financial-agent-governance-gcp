"""
MCP Protocol and FastMCP Integration Tests.
Verifies tool discovery, schemas, annotations, and ASGI HTTP endpoint behavior.
"""

import pytest
from starlette.testclient import TestClient

from mcp_financial_action_server.server import app, mcp
from mcp_financial_action_server.storage import financial_storage


@pytest.fixture(autouse=True)
def clean_storage():
    """Reset storage to seed state before each test."""
    financial_storage.reset_state()


@pytest.mark.asyncio
async def test_mcp_list_tools():
    """Verify that all critical action tools are exposed with correct metadata and annotations."""
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}

    expected_tools = {
        "execute_bank_transfer",
        "register_destination_account",
        "schedule_direct_debit",
        "cancel_scheduled_debit",
        "get_account_balances",
        "get_recent_transactions",
    }
    assert expected_tools.issubset(set(tool_map.keys()))

    # Read tools are annotated read-only
    for name in ("get_account_balances", "get_recent_transactions"):
        assert getattr(tool_map[name].annotations, "read_only_hint", None) is True

    # Check annotations on execute_bank_transfer
    transfer_tool = tool_map["execute_bank_transfer"]
    assert transfer_tool.annotations is not None
    assert getattr(transfer_tool.annotations, "destructive_hint", None) is True
    assert getattr(transfer_tool.annotations, "read_only_hint", None) is False
    assert getattr(transfer_tool.annotations, "idempotent_hint", None) is True

    # Check annotations on cancel_scheduled_debit
    cancel_tool = tool_map["cancel_scheduled_debit"]
    assert cancel_tool.annotations is not None
    assert getattr(cancel_tool.annotations, "destructive_hint", None) is True
    assert getattr(cancel_tool.annotations, "read_only_hint", None) is False


@pytest.mark.asyncio
async def test_mcp_call_tool_execute_transfer():
    """Invoking execute_bank_transfer via MCP protocol executes transaction and returns structured result."""
    args = {
        "user_id": "user_default_01",
        "source_account_id": "acc_001",
        "destination_cbu_or_alias": "0170099220000067891234",
        "amount": 7500.0,
        "currency": "ARS",
        "concept": "MCP_PROTOCOL_TEST",
        "idempotency_key": "mcp-protocol-key-01",
    }

    result = await mcp.call_tool("execute_bank_transfer", args)

    assert result.is_error is False
    assert result.structured_content is not None
    res_data = result.structured_content
    assert res_data["status"] == "EXECUTED"
    assert res_data["amount"] == 7500.0
    assert res_data["idempotency_key"] == "mcp-protocol-key-01"


@pytest.mark.asyncio
async def test_mcp_call_tool_register_account():
    """Invoking register_destination_account via MCP protocol registers the account."""
    args = {
        "user_id": "user_default_01",
        "cbu_or_cvu": "0170099220000088888888",
        "alias": "clara.diseno",
        "holder_name": "Clara Beltrán",
        "bank_name": "BBVA Francés",
    }

    result = await mcp.call_tool("register_destination_account", args)

    assert result.is_error is False
    res_data = result.structured_content
    assert res_data["status"] == "REGISTERED"
    assert res_data["holder_name"] == "Clara Beltrán"
    assert res_data["cbu_or_cvu"] == "0170099220000088888888"


def test_health_check_endpoint():
    """HTTP GET /health returns 200 and healthy server status."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["server"] == "FinancialActionServer"
    assert "active_accounts" in data
