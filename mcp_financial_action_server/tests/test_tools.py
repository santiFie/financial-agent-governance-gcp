"""
Unit tests for critical financial action tools.
"""
import pytest
from mcp_financial_action_server.storage import financial_storage
from mcp_financial_action_server.server import (
    execute_bank_transfer,
    register_destination_account,
    schedule_direct_debit,
    cancel_scheduled_debit,
    get_account_balances,
    get_recent_transactions,
)


@pytest.fixture(autouse=True)
def clean_storage():
    """Reset storage to seed state before each test."""
    financial_storage.reset_state()


def test_transfer_success_with_cbu():
    """Transfer to a valid 22-digit CBU succeeds and debits balance."""
    initial_acc = financial_storage.get_account("acc_001")
    initial_balance = initial_acc.balance

    res = execute_bank_transfer(
        user_id="user_default_01",
        source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234",
        amount=15000.0,
        currency="ARS",
        concept="ALQUILER",
        idempotency_key="idempotency-key-001",
    )

    assert res["status"] == "EXECUTED"
    assert res["amount"] == 15000.0
    assert res["remaining_balance"] == round(initial_balance - 15000.0, 2)
    assert res["transaction_id"].startswith("tx_mcp_")
    assert res["receipt_number"].startswith("REC-")


def test_transfer_success_with_alias():
    """Transfer to a valid alphanumeric alias succeeds."""
    res = execute_bank_transfer(
        user_id="user_default_01",
        source_account_id="acc_001",
        destination_cbu_or_alias="martin.tech.fin",
        amount=5000.50,
        currency="ARS",
        concept="SERVICIOS",
        idempotency_key="idempotency-key-alias-01",
    )

    assert res["status"] == "EXECUTED"
    assert res["destination_cbu_or_alias"] == "martin.tech.fin"
    assert res["amount"] == 5000.50


def test_transfer_insufficient_funds_rejected():
    """Transfer with amount greater than account balance fails."""
    with pytest.raises(ValueError, match="Fondos insuficientes"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=999999.0,
            currency="ARS",
            concept="COMPRA_AUTO",
            idempotency_key="idempotency-funds-fail",
        )


def test_transfer_negative_or_zero_amount_rejected():
    """Negative or zero transfer amounts are rejected."""
    with pytest.raises(ValueError, match="estrictamente positivo"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=0.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-zero",
        )

    with pytest.raises(ValueError, match="estrictamente positivo"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=-1000.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-negative",
        )


def test_transfer_invalid_cbu_rejected():
    """CBU with invalid length or non-digits is rejected."""
    # Length < 22 digits
    with pytest.raises(ValueError, match="El CBU/CVU .* es inválido"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="1234567890",
            amount=1000.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-bad-cbu-1",
        )

    # 22 chars with letters
    with pytest.raises(ValueError, match="inválido"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="017009922000006789123X",
            amount=1000.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-bad-cbu-2",
        )


def test_transfer_invalid_alias_rejected():
    """Alias with invalid characters or bad format is rejected."""
    from mcp_financial_action_server.validation import validate_alias

    # Pure numeric alias tested via validate_alias
    with pytest.raises(ValueError, match="no puede consistir exclusivamente en dígitos"):
        validate_alias("12345678")

    # Alias with invalid symbols (@, #, $) rejected during transfer
    with pytest.raises(ValueError, match="es inválido"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="juan@perez#fintech",
            amount=1000.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-bad-alias-symbols",
        )

    # Alias too short (< 6 chars)
    with pytest.raises(ValueError, match="es inválido"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="abc",
            amount=1000.0,
            currency="ARS",
            concept="TEST",
            idempotency_key="idempotency-bad-alias-short",
        )


def test_transfer_daily_limit_exceeded():
    """Cumulative transfers exceeding daily limit ($1,000,000) are blocked."""
    # First, increase account balance to test the limit restriction
    acc = financial_storage.get_account("acc_001")
    acc.balance = 2000000.0

    # Transfer 600,000 -> succeeds
    res1 = execute_bank_transfer(
        user_id="user_default_01",
        source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234",
        amount=600000.0,
        currency="ARS",
        concept="LIMITE_PARTE_1",
        idempotency_key="idempotency-lim-1",
    )
    assert res1["status"] == "EXECUTED"

    # Second transfer 500,000 -> 600k + 500k = 1.1M > 1.0M limit
    with pytest.raises(ValueError, match="Límite diario de transferencias superado"):
        execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=500000.0,
            currency="ARS",
            concept="LIMITE_PARTE_2",
            idempotency_key="idempotency-lim-2",
        )


def test_transfer_idempotency_key_replay():
    """Re-submitting the same idempotency key returns the original receipt without debiting funds twice."""
    initial_acc = financial_storage.get_account("acc_001")
    initial_balance = initial_acc.balance

    key = "unique-transaction-key-777"
    res1 = execute_bank_transfer(
        user_id="user_default_01",
        source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234",
        amount=20000.0,
        currency="ARS",
        concept="PAGO_PROVEEDOR",
        idempotency_key=key,
    )
    assert res1["status"] == "EXECUTED"
    balance_after_res1 = financial_storage.get_account("acc_001").balance
    assert balance_after_res1 == round(initial_balance - 20000.0, 2)

    # Send exact same request with same idempotency key
    res2 = execute_bank_transfer(
        user_id="user_default_01",
        source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234",
        amount=20000.0,
        currency="ARS",
        concept="PAGO_PROVEEDOR",
        idempotency_key=key,
    )

    assert res2["status"] == "ALREADY_PROCESSED"
    assert res2["transaction_id"] == res1["transaction_id"]
    assert res2["receipt_number"] == res1["receipt_number"]
    # Verify balance was NOT debited a second time
    assert financial_storage.get_account("acc_001").balance == balance_after_res1


def test_transfer_unauthorized_user_rejected():
    """Attempting to transfer from an account owned by someone else is rejected."""
    with pytest.raises(ValueError, match="no es titular de la cuenta"):
        execute_bank_transfer(
            user_id="attacker_user_99",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=1000.0,
            currency="ARS",
            concept="FRAUDE",
            idempotency_key="idempotency-unauth",
        )


def test_register_destination_account():
    """Registering a new account into the client's address book succeeds."""
    res = register_destination_account(
        user_id="user_default_01",
        cbu_or_cvu="0170099220000012345678",
        alias="roberto.freelance",
        holder_name="Roberto Carlos Rossi",
        bank_name="Banco Santander",
    )

    assert res["status"] == "REGISTERED"
    assert res["cbu_or_cvu"] == "0170099220000012345678"
    assert res["alias"] == "roberto.freelance"
    assert res["holder_name"] == "Roberto Carlos Rossi"
    assert res["recipient_id"].startswith("rec_")


def test_schedule_and_cancel_direct_debit():
    """Scheduling and then cancelling a direct debit succeeds."""
    schedule_res = schedule_direct_debit(
        user_id="user_default_01",
        account_id="acc_001",
        service_provider="Telecom Personal",
        customer_reference="CLI-889922",
        max_amount=35000.0,
        frequency="MONTHLY",
    )

    assert schedule_res["status"] == "SCHEDULED"
    assert schedule_res["service_provider"] == "Telecom Personal"
    assert schedule_res["debit_id"].startswith("deb_")
    debit_id = schedule_res["debit_id"]

    # Cancel the debit
    cancel_res = cancel_scheduled_debit(
        user_id="user_default_01",
        debit_id=debit_id,
    )
    assert cancel_res["status"] == "CANCELLED"
    assert cancel_res["debit_id"] == debit_id

    # Trying to cancel again fails
    with pytest.raises(ValueError, match="ya se encuentra cancelado"):
        cancel_scheduled_debit(
            user_id="user_default_01",
            debit_id=debit_id,
        )


def test_read_tools_return_balances_and_seeded_history():
    balances = get_account_balances(user_id="user_default_01")["accounts"]
    assert {a["account_id"]: a["balance"] for a in balances} == {"acc_001": 485250.75, "acc_002": 1520.0}
    assert get_account_balances(user_id="otro_usuario")["accounts"] == []

    txs = get_recent_transactions(user_id="user_default_01", limit=3)["transactions"]
    assert len(txs) == 3
    assert [t["timestamp"] for t in txs] == sorted((t["timestamp"] for t in txs), reverse=True)


def test_read_tools_reflect_executed_transfers():
    execute_bank_transfer(
        user_id="user_default_01", source_account_id="acc_001",
        destination_cbu_or_alias="0170099220000067891234", amount=1000.0,
        currency="ARS", concept="TEST", idempotency_key="read-after-write-1",
    )

    balances = {a["account_id"]: a["balance"] for a in get_account_balances(user_id="user_default_01")["accounts"]}
    latest = get_recent_transactions(user_id="user_default_01", limit=1)["transactions"][0]

    assert balances["acc_001"] == 484250.75
    assert latest["type"] == "DEBIT" and latest["amount"] == 1000.0
    assert "0170099220000067891234" in latest["description"]
