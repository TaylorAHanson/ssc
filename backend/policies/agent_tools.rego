package agent.tools

# Agent-tool authorization for the V2 ToolExecutor.
#
# Input shape (built by app.tools.tool_executor.ToolExecutor):
#   {
#     "tool": "execute_workflow",
#     "side_effect_class": "infra",   # read|app_write|data_grant|infra|membership|notify|destructive
#     "is_mutating": true,
#     "policy_ref": null,
#     "args": { ... model-supplied args ... },
#     "user": {"email": "...", "roles": [...], "entitlements": [...]},
#     "approvals": ["manager", ...]   # approvals already gathered for this call
#   }
#
# Output (`decision`):
#   {
#     "allow": bool,                 # hard allow/deny (capability scope, explicit deny)
#     "requires_approval": bool,     # gate before the mutating call may execute
#     "approval_type": "none|manager|admin",
#     "reason": "..."
#   }
#
# NOTE (V2 staging): this package currently enforces *approval gates* keyed on
# side_effect_class. Hard capability scoping (the workflow's allowed_tools) lands
# with the Workflow object (M3); until then `allow` stays true. The ToolExecutor
# runs this in SHADOW mode by default (logs, never blocks) until
# AGENT_TOOL_OPA_ENFORCE is flipped on.

import future.keywords.if
import future.keywords.in

# Approval requirement per side-effect class.
_approval_type_by_class := {
	"read": "none",
	"app_write": "none",
	"notify": "none",
	"data_grant": "manager",
	"infra": "manager",
	"membership": "manager",
	"destructive": "admin",
}

# The workflow entry tool (`execute_workflow`) creates a *governed request* and
# hands off to the durable graph, which runs its own HITL approval gates on the
# real infra/data mutations. Approval therefore happens in-graph, not at chat
# entry; gating initiation here would deadlock every workflow. It still carries
# its `infra` side_effect_class for audit fidelity.
#
# Otherwise: resolve approval type by side-effect class. Unknown *mutating*
# classes fail safe to "manager"; non-mutating/unknown falls through to "none".
approval_type := "none" if {
	input.tool == "execute_workflow"
} else := t if {
	t := _approval_type_by_class[input.side_effect_class]
} else := "manager" if {
	input.is_mutating
} else := "none"

requires_approval if {
	approval_type != "none"
	not approval_satisfied
}

requires_approval := false if {
	approval_type == "none"
}

requires_approval := false if {
	approval_type != "none"
	approval_satisfied
}

approval_satisfied if {
	approval_type != "none"
	approval_type in input.approvals
}

# Hard allow/deny. Default allow; explicit deny rules can be added here later
# (e.g. capability scope from the active workflow, destructive-without-entitlement).
default allow := true

decision := {
	"allow": allow,
	"requires_approval": requires_approval,
	"approval_type": approval_type,
	"reason": reason,
}

reason := sprintf("Mutating tool '%v' (%v) requires '%v' approval.", [input.tool, input.side_effect_class, approval_type]) if {
	approval_type != "none"
	not approval_satisfied
}

reason := sprintf("Tool '%v' (%v) approved (%v approval present).", [input.tool, input.side_effect_class, approval_type]) if {
	approval_type != "none"
	approval_satisfied
}

reason := sprintf("Tool '%v' (%v) permitted without approval.", [input.tool, input.side_effect_class]) if {
	approval_type == "none"
}
