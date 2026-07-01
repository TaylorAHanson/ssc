package agent.tools

# Agent-tool authorization for the V2 ToolExecutor.
#
# Input shape (built by app.tools.tool_executor.ToolExecutor):
#   {
#     "tool": "github_create_repo",
#     "side_effect_class": "infra",   # read|app_write|data_grant|infra|membership|notify|destructive
#     "is_mutating": true,
#     "policy_ref": null,
#     "args": { ... model-supplied args ... },
#     "user": {"email": "...", "roles": [...], "entitlements": [...]},
#     "approvals": ["manager", ...]   # approvals already gathered for this call
#   }
#   (Only *mutating* tools are evaluated here; the executor skips OPA for reads.)
#
# Output (`decision`):
#   {
#     "allow": bool,                 # hard allow/deny (capability scope, explicit deny)
#     "requires_approval": bool,     # always false — see APPROVAL MODEL below
#     "approval_type": "none",
#     "reason": "..."
#   }
#
# APPROVAL MODEL (graph-authoritative)
# ------------------------------------
# Human-in-the-loop approval is owned ENTIRELY by the workflow graph. A graph
# `gate` node (e.g. `manager_approval`) pauses the request until an approver
# acts, and a step cannot execute until every gate preceding it is satisfied.
# Gate present in the flow => approval required; no gate => no approval. This is
# exactly the "use the graph as the graph" model.
#
# This policy therefore does NOT re-derive approval from `side_effect_class`.
# It previously mapped infra/data_grant/membership -> "manager" and destructive
# -> "admin", which double-gated every workflow (the graph gate AND OPA) and
# forced per-tool exemptions (`execute_workflow`, `github_create_repo`, ...).
# Since the genuinely-mutating provisioning tools live in `app.workflows.tools`
# and are only reachable through the graph (they are not in the chat agent's
# toolset), that second gate was redundant with the graph and is removed.
#
# `side_effect_class` / `is_mutating` remain in the input for audit fidelity and
# for future *hard-deny* rules (e.g. capability scope, destructive-without-
# entitlement) — those belong under `allow`, not as an approval gate.

import future.keywords.if

# Hard allow/deny. Default allow; add explicit deny rules here as needed.
default allow := true

decision := {
	"allow": allow,
	"requires_approval": false,
	"approval_type": "none",
	"reason": reason,
}

reason := sprintf("Tool '%v' (%v) permitted; approvals are governed by the workflow graph.", [input.tool, input.side_effect_class]) if {
	allow
}

reason := sprintf("Tool '%v' (%v) denied by policy.", [input.tool, input.side_effect_class]) if {
	not allow
}
