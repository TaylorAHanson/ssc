package databricks.governance.jobs

# Workflow jobs. Split out of the former `compute_and_jobs` policy so each
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
	"job_not_long_failing": "Job has not failed consecutively for >30 days",
	"job_not_idle": "Job has been run in the last 90 days",
}

# === Applicability ===
applies contains "job_not_long_failing" if {
	input.resource.type == "job"
}

applies contains "job_not_idle" if {
	input.resource.type == "job"
}

# === Violations ===
violations["job_not_long_failing"] contains msg if {
	applies["job_not_long_failing"]
	input.resource.failed_consecutively_days > 30
	msg := "Job has failed consecutively for over 30 days."
}

violations["job_not_idle"] contains msg if {
	applies["job_not_idle"]
	input.resource.idle_days > 90
	msg := "Job has not been run in over 90 days."
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
