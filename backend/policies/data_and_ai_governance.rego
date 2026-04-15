package databricks.governance.data_and_ai_governance

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

violation_reasons contains msg if {
    input.resource.type == "catalog_access"
    input.resource.catalog_environment != input.workspace.environment
    msg := "Cross-environment access (e.g., dev accessing prod catalogs) is prohibited."
}

violation_reasons contains msg if {
    input.resource.type == "storage"
    input.resource.storage_type in ["dbfs", "local_volume"]
    input.workspace.environment == "prod"
    msg := "Production data must not be stored in DBFS or local volumes; only approved external locations may hold prod data."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "BLOCK")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "CRITICAL")
