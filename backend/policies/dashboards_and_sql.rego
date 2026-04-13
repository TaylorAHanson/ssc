package databricks.governance.dashboards_and_sql

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons[msg] {
    input.resource.type == "dashboard"
    input.resource.uses_embedded_credentials == true
    "ALL_USERS" in input.resource.shared_with
    msg := "Dashboards with embedded credentials must not be shared with 'everyone' (ALL_USERS); they may only be shared with specific groups whose access matches the credential scope."
}

violation_reasons[msg] {
    input.resource.type == "sql_warehouse"
    not input.resource.policy_id
    input.workspace.environment == "prod"
    msg := "Production SQL warehouses must use compute policies; ad-hoc personal warehouses are disabled in prod."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "CRITICAL")
