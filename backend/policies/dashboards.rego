package databricks.governance.dashboards

# Lakeview / AI-BI dashboards. Split out of the former `dashboards_and_sql`
# policy so each resource type has its own policy file (and therefore its own
# policy name in findings, audit rows, and the scan filter).

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
	"embedded_creds_not_shared_all": "Dashboards with embedded credentials are not shared with ALL_USERS",
}

# === Applicability ===
applies contains "embedded_creds_not_shared_all" if {
	input.resource.type == "dashboard"
}

# === Violations ===
violations["embedded_creds_not_shared_all"] contains msg if {
	applies["embedded_creds_not_shared_all"]
	input.resource.uses_embedded_credentials == true
	"ALL_USERS" in input.resource.shared_with
	msg := "Dashboards with embedded credentials must not be shared with 'everyone' (ALL_USERS); they may only be shared with specific groups whose access matches the credential scope."
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
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
