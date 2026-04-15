package databricks.governance.compute_and_jobs

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons contains msg if {
    input.resource.type == "cluster"
    input.resource.cluster_type == "interactive"
    input.resource.access_mode == "shared"
    input.workspace.environment == "prod"
    msg := "Shared interactive clusters are disallowed in production; only single-user or job-only clusters are permitted."
}

violation_reasons contains msg if {
    input.resource.type == "cluster"
    not input.resource.policy_id
    msg := "All clusters, warehouses, and serverless compute must be created via cluster/compute policies; unrestricted 'no policy' compute is disabled."
}

violation_reasons contains msg if {
    input.resource.type == "job"
    input.resource.failed_consecutively_days > 30
    msg := "Job has failed consecutively for over 30 days."
}

violation_reasons contains msg if {
    input.resource.type == "job"
    input.resource.idle_days > 90
    msg := "Job has not been run in over 90 days."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "CRITICAL")
