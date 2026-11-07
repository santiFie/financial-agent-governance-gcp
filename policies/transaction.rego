package transaction

import rego.v1

default decision := {"allow": false, "require_hitl": false, "reason": "deny por defecto"}

# Only transfers are governed by amount and recipient. A call without tool_name is treated as a transfer.
is_transfer if not input.tool_name

is_transfer if input.tool_name == "transfer_funds_tool"

# Any other tool call (e.g. register_recipient_tool) moves no funds and is allowed.
decision := {"allow": true, "require_hitl": false, "reason": "Herramienta sin movimiento de fondos"} if {
	not is_transfer
}

fast_path if {
	is_transfer
	input.is_trusted_recipient
	input.amount > 0
	input.amount < 50000
}

decision := {"allow": true, "require_hitl": false, "reason": "Fast-Path"} if {
	fast_path
}

decision := {"allow": true, "require_hitl": true, "reason": "HITL requerido"} if {
	is_transfer
	not fast_path
	input.amount > 0
	input.amount <= 1000000
}
