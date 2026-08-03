package databricks.governance.apps

# Databricks Apps. Split out of the former `apps_and_genie` policy so each
# resource type has its own policy file (and therefore its own policy name in
# findings, audit rows, and the scan filter).

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
	"no_apps_enterprise_prod": "Apps not hosted in enterprise prod without allowlist",
	"app_not_idle": "App has been accessed in the last 30 days",
}

# === Applicability ===
applies contains "no_apps_enterprise_prod" if {
	input.resource.type == "app"
	input.workspace.type == "enterprise"
	input.workspace.environment == "prod"
}

applies contains "app_not_idle" if {
	input.resource.type == "app"
}

# === Violations ===
violations["no_apps_enterprise_prod"] contains msg if {
	applies["no_apps_enterprise_prod"]
	msg := "Apps must not be hosted in enterprise prod unless they are on a centrally managed allowlist with documented risk review."
}

violations["app_not_idle"] contains msg if {
	applies["app_not_idle"]
	input.resource.idle_days > 30
	msg := "Apps must be stopped if no one has accessed the app in over 30 days."
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
