package databricks.governance.monitoring_and_logging

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

taggable_resources := {"job", "cluster", "warehouse", "app", "genie_space"}

# === Rule catalog ===
# Originally one rego rule covered both cost-center and owner with the same
# message; we split them so each tag is a separately auditable rule.
rule_metadata := {
	"cost_center_tag": "Resource is tagged with 'cost-center' for cost attribution",
	"owner_tag": "Resource is tagged with 'owner' for accountability",
}

# === Applicability ===
applies contains "cost_center_tag" if {
	input.resource.type in taggable_resources
}

applies contains "owner_tag" if {
	input.resource.type in taggable_resources
}

# === Violations ===
violations["cost_center_tag"] contains msg if {
	applies["cost_center_tag"]
	not input.resource.tags["cost-center"]
	msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged with 'cost-center' for cost attribution."
}

violations["owner_tag"] contains msg if {
	applies["owner_tag"]
	not input.resource.tags["owner"]
	msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged with 'owner' for accountability."
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

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "MEDIUM")
