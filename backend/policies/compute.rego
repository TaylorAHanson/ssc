package databricks.governance.compute

# Clusters / all-purpose + job compute. Split out of the former
# `compute_and_jobs` policy so each resource type has its own policy file (and
# therefore its own policy name in findings, audit rows, and the scan filter).

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

# === Rule catalog ===
rule_metadata := {
	"no_shared_interactive_prod": "Shared interactive clusters disallowed in production",
	# DISABLED: "cluster_uses_policy" — flags every cluster created without a
	# compute policy, which is most of the estate today and drowns out the rest
	# of the findings. Re-enable once compute policies are rolled out; the rule
	# body below is intact.
	# "cluster_uses_policy": "Compute is created via a cluster/compute policy",
}

# === Applicability ===
applies contains "no_shared_interactive_prod" if {
	input.resource.type == "cluster"
	input.workspace.environment == "prod"
}

# DISABLED with the rule above.
# applies contains "cluster_uses_policy" if {
# 	input.resource.type == "cluster"
# }

# === Violations ===
violations["no_shared_interactive_prod"] contains msg if {
	applies["no_shared_interactive_prod"]
	input.resource.cluster_type == "interactive"
	input.resource.access_mode == "shared"
	msg := "Shared interactive clusters are disallowed in production; only single-user or job-only clusters are permitted."
}

# DISABLED with the rule above.
# violations["cluster_uses_policy"] contains msg if {
# 	applies["cluster_uses_policy"]
# 	not input.resource.policy_id
# 	msg := "All clusters, warehouses, and serverless compute must be created via cluster/compute policies; unrestricted 'no policy' compute is disabled."
# }

# === Structured per-rule results ===
rule_results contains result if {
	some rule_id
	applies[rule_id]
	rule_violations := object.get(violations, rule_id, set())
	result := {
		"id": rule_id,
		"description": rule_metadata[rule_id],
		"passed": count(rule_violations) == 0,
		"messages": sort([m | some m in rule_violations]),
	}
}

# === Backwards-compat ===
violation_reasons contains msg if {
	some r in rule_results
	msg := r.messages[_]
}

# === Common governance logic ===
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
