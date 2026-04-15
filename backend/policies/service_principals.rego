package databricks.governance.service_principals

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons contains msg if {
    input.resource.type == "service_principal"
    input.resource.idle_days > 90
    msg := "Service principals should be deleted if they have no successful login or workload activity in 90 days."
}

violation_reasons contains msg if {
    input.resource.type == "job"
    input.resource.owner_type == "user"
    input.workspace.environment == "prod"
    msg := "Production automation must use OAuth / workload identities (service principals), not human users."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "DELETE")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
