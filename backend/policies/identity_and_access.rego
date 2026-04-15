package databricks.governance.identity_and_access

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons contains msg if {
    input.resource.type == "personal_access_token"
    input.workspace.type == "enterprise"
    input.workspace.environment == "prod"
    not input.resource.is_break_glass
    msg := "Personal access tokens (PATs) are disabled in enterprise prod except for break-glass use."
}

violation_reasons contains msg if {
    input.resource.type == "grant"
    input.resource.principal_type == "user"
    input.workspace.environment == "prod"
    msg := "All data access is granted to groups, not individual users. Direct object grants to individuals are disallowed in production catalogs."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "CRITICAL")
