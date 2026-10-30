"""
Concurrency and atomicity tests for financial storage and transfer operations.
"""
import concurrent.futures
import pytest
from mcp_financial_action_server.storage import financial_storage
from mcp_financial_action_server.server import execute_bank_transfer


@pytest.fixture(autouse=True)
def clean_storage():
    """Reset storage to seed state before each test."""
    financial_storage.reset_state()


def test_concurrent_transfers_balance_consistency():
    """
    Spawns multiple threads executing concurrent transfers against the same account.
    Verifies that the final balance reflects exact deductions without lost updates.
    """
    initial_acc = financial_storage.get_account("acc_001")
    initial_balance = initial_acc.balance

    num_threads = 20
    transfer_amount = 1000.0  # Total = 20,000

    def run_transfer(idx: int):
        return execute_bank_transfer(
            user_id="user_default_01",
            source_account_id="acc_001",
            destination_cbu_or_alias="0170099220000067891234",
            amount=transfer_amount,
            currency="ARS",
            concept=f"CONCURRENCY_TEST_{idx}",
            idempotency_key=f"concurrent-key-{idx}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(run_transfer, i) for i in range(num_threads)]
        results = [f.result() for f in futures]

    assert len(results) == num_threads
    for r in results:
        assert r["status"] == "EXECUTED"

    final_balance = financial_storage.get_account("acc_001").balance
    expected_balance = round(initial_balance - (num_threads * transfer_amount), 2)
    assert final_balance == expected_balance


def test_concurrent_transfers_limited_balance_no_overdraft():
    """
    When account has exactly $5,000 and 10 threads attempt to debit $1,000 concurrently,
    exactly 5 must succeed and 5 must fail. The final balance must be exactly $0.00.
    """
    acc = financial_storage.get_account("acc_001")
    acc.balance = 5000.0

    num_threads = 10
    amount = 1000.0

    def run_transfer(idx: int):
        try:
            res = execute_bank_transfer(
                user_id="user_default_01",
                source_account_id="acc_001",
                destination_cbu_or_alias="0170099220000067891234",
                amount=amount,
                currency="ARS",
                concept=f"RACE_TEST_{idx}",
                idempotency_key=f"race-key-{idx}",
            )
            return ("SUCCESS", res)
        except ValueError as e:
            return ("FAILED", str(e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(run_transfer, i) for i in range(num_threads)]
        results = [f.result() for f in futures]

    successes = [r for r in results if r[0] == "SUCCESS"]
    failures = [r for r in results if r[0] == "FAILED"]

    assert len(successes) == 5
    assert len(failures) == 5
    for f in failures:
        assert "Fondos insuficientes" in f[1]

    final_balance = financial_storage.get_account("acc_001").balance
    assert final_balance == 0.0
