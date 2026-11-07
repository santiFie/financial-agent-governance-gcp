package transaction_test

import rego.v1

import data.transaction

test_fast_path_trusted_under_threshold if {
	d := transaction.decision with input as {"amount": 1000, "is_trusted_recipient": true}
	d.allow
	not d.require_hitl
}

test_hitl_trusted_over_threshold if {
	d := transaction.decision with input as {"amount": 60000, "is_trusted_recipient": true}
	d.allow
	d.require_hitl
}

test_hitl_untrusted_recipient if {
	d := transaction.decision with input as {"amount": 1000, "is_trusted_recipient": false}
	d.allow
	d.require_hitl
}

test_deny_over_upper_bound if {
	d := transaction.decision with input as {"amount": 2000000, "is_trusted_recipient": true}
	not d.allow
}

test_deny_non_positive_amount if {
	d := transaction.decision with input as {"amount": 0, "is_trusted_recipient": true}
	not d.allow
	d2 := transaction.decision with input as {"amount": -5, "is_trusted_recipient": true}
	not d2.allow
}

test_deny_without_input if {
	d := transaction.decision with input as {}
	not d.allow
}

test_allow_non_transfer_tool if {
	d := transaction.decision with input as {"tool_name": "register_recipient_tool", "user_id": "u1"}
	d.allow
	not d.require_hitl
}

test_transfer_tool_name_keeps_amount_rules if {
	d := transaction.decision with input as {"tool_name": "transfer_funds_tool", "amount": 60000, "is_trusted_recipient": true}
	d.allow
	d.require_hitl
	d2 := transaction.decision with input as {"tool_name": "transfer_funds_tool", "amount": 1000, "is_trusted_recipient": true}
	not d2.require_hitl
}

test_deny_transfer_tool_without_amount if {
	d := transaction.decision with input as {"tool_name": "transfer_funds_tool"}
	not d.allow
}
