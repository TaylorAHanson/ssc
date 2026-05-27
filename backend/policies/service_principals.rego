package databricks.governance.service_principals

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
	"sp_not_idle": "Service principal has activity in the last 90 days",
	"prod_jobs_use_sp": "Production jobs use service principals, not human users",
}

# === Applicability ===
applies contains "sp_not_idle" if {
	input.resource.type == "service_principal"
}

applies contains "prod_jobs_use_sp" if {
	input.resource.type == "job"
	input.workspace.environment == "prod"
}

# === Violations ===
violations["sp_not_idle"] contains msg if {
	applies["sp_not_idle"]
	input.resource.idle_days > 90
	msg := "Service principals should be deleted if they have no successful login or workload activity in 90 days."
}

violations["prod_jobs_use_sp"] contains msg if {
	applies["prod_jobs_use_sp"]
	input.resource.owner_type == "user"
	msg := "Production automation must use OAuth / workload identities (service principals), not human users."
}

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

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "DELETE")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
