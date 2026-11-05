package transaction

import rego.v1

default decision := {"allow": false, "require_hitl": false, "reason": "deny por defecto"}

fast_path if {
	input.is_trusted_recipient
	input.amount > 0
	input.amount < 50000
}

decision := {"allow": true, "require_hitl": false, "reason": "Fast-Path"} if {
	fast_path
}

decision := {"allow": true, "require_hitl": true, "reason": "HITL requerido"} if {
	not fast_path
	input.amount > 0
	input.amount <= 1000000
}
