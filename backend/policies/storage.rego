package databricks.governance.storage

# Storage locations / UC volumes. Split out of the former
# `data_and_ai_governance` policy so each resource type has its own policy file
# (and therefore its own policy name in findings, audit rows, and the scan
# filter).

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
	"no_dbfs_prod_storage": "Production data is not stored in DBFS or local volumes",
}

# === Applicability ===
applies contains "no_dbfs_prod_storage" if {
	input.resource.type == "storage"
	input.workspace.environment == "prod"
}

# === Violations ===
violations["no_dbfs_prod_storage"] contains msg if {
	applies["no_dbfs_prod_storage"]
	input.resource.storage_type in ["dbfs", "local_volume"]
	msg := "Production data must not be stored in DBFS or local volumes; only approved external locations may hold prod data."
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

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "BLOCK")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
