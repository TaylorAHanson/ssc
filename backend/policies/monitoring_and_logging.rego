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

violation_reasons contains msg if {
    input.resource.type in taggable_resources
    not input.resource.tags["cost-center"]
    msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged for cost attribution ('cost-center' and 'owner')."
}

violation_reasons contains msg if {
    input.resource.type in taggable_resources
    not input.resource.tags["owner"]
    msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged for cost attribution ('cost-center' and 'owner')."
}

# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "MEDIUM")
