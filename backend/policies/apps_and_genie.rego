package databricks.governance.apps_and_genie

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons contains msg if {
    input.resource.type == "app"
    input.workspace.type == "enterprise"
    input.workspace.environment == "prod"
    msg := "Apps and Genie spaces must not be hosted in enterprise prod unless they are on a centrally managed allowlist with documented risk review."
}

violation_reasons contains msg if {
    input.resource.type == "genie_space"
    input.workspace.type == "enterprise"
    input.workspace.environment == "prod"
    msg := "Apps and Genie spaces must not be hosted in enterprise prod unless they are on a centrally managed allowlist with documented risk review."
}

violation_reasons contains msg if {
    input.resource.type == "app"
    input.resource.idle_days > 30
    msg := "Apps must be stopped if no one has accessed the app in over 30 days."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "HIGH")
