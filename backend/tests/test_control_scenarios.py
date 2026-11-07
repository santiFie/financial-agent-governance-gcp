"""
Control scenarios (tests/data/control_scenarios.json): direct transfers that the baseline policy must send to HITL.
They contrast with the 3.1 attack, which succeeds only because it stays under the Fast-Path conditions.
Offline: the PDP answer is mocked, so these tests check what the PEP sends to the PDP and how it reacts.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp_financial_action_server.storage import financial_storage

from app.core.security_context import token_vault
from app.graph.nodes.pep import pep_node

DATA = Path(__file__).parent / "data"
CONTROLS = json.loads((DATA / "control_scenarios.json").read_text(encoding="utf-8"))
ATTACKS = json.loads((DATA / "attack_3_1_scenarios.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def clean_state():
    financial_storage.reset_state()
    with token_vault._lock:
        token_vault._vault.clear()
    yield
    financial_storage.reset_state()


def test_scenario_ids_are_unique_across_both_files():
    ids = [s["id"] for s in CONTROLS + ATTACKS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("scenario", CONTROLS, ids=[s["id"] for s in CONTROLS])
def test_control_scenario_goes_to_hitl_with_the_expected_pdp_input(scenario):
    expected = scenario["expected_pdp_input"]
    state = {
        "session_id": "sess_control",
        "user_id": "user_default_01",
        "pending_tool_calls": [
            {
                "name": "transfer_funds_tool",
                "args": {
                    "source_account": "acc_001",
                    "destination_account": "0290021110000045678912",
                    "amount": expected["amount"],
                },
            }
        ],
    }
    hitl = MagicMock(status_code=200)
    hitl.json.return_value = {"result": {"allow": True, "require_hitl": True, "reason": "HITL requerido"}}

    with (
        patch("httpx.Client.post", return_value=hitl) as post,
        patch("app.graph.nodes.pep.interrupt", return_value={"decision": "REJECTED"}) as interrupt,
    ):
        pep_node(state)

    assert post.call_args.kwargs["json"]["input"] == {"user_id": "user_default_01", **expected}
    interrupt.assert_called_once()
    assert scenario["expected_execution_path"] == "HITL"
