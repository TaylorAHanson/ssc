package databricks.governance.monitoring_and_logging

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

taggable_resources := ["cluster", "warehouse", "job", "app", "genie_space"]

violation_reasons[msg] {
    input.resource.type in taggable_resources
    not input.resource.tags["cost-center"]
    msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged for cost attribution ('cost-center' and 'owner')."
}

violation_reasons[msg] {
    input.resource.type in taggable_resources
    not input.resource.tags["owner"]
    msg := "Jobs, clusters, warehouses, apps, and Genie spaces must be tagged for cost attribution ('cost-center' and 'owner')."
}

is_violation if {
    count(violation_reasons) > 0
}

# --- Allowlist Exception Logic ---
matching_exceptions := [
    e | e := input.allowlist_records[_];
    e.resource_id == input.resource.id
]

has_approved_exception if {
    is_violation
    some exception in matching_exceptions
    exception.status == "approved"
    is_valid_expiry(exception, input.request_time)
}

has_pending_exception if {
    is_violation
    not has_approved_exception
    some exception in matching_exceptions
    exception.status == "pending"
}

is_valid_expiry(exception, current_time) if {
    not exception.expires_at
}

is_valid_expiry(exception, current_time) if {
    exception.expires_at != null
    exception.expires_at > current_time
}

action = "SKIPPED_ALLOWLIST" if {
    has_approved_exception
}

action = "PENDING_EXCEPTION" if {
    has_pending_exception
}

# Override reason based on exception
reason = exception.justification if {
    has_approved_exception
    some exception in matching_exceptions
    exception.status == "approved"
    is_valid_expiry(exception, input.request_time)
}

reason = "Exception request is pending admin approval." if {
    has_pending_exception
}

severity = "NONE" if {
    has_approved_exception
}

severity = "MEDIUM" if {
    has_pending_exception
}

reason = concat("; ", violation_reasons) if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}

action = "KILL" if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}

severity = "MEDIUM" if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}


import data.databricks.governance.common
import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"


# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved)

action := common.resolve_action(is_violation, has_approved, has_pending, "KILL")
reason := common.resolve_reason(is_violation, has_approved, has_pending, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved, has_pending, "MEDIUM")


import data.databricks.governance.common
import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"


# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved)

action := common.resolve_action(is_violation, has_approved, has_pending, "KILL")
reason := common.resolve_reason(is_violation, has_approved, has_pending, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved, has_pending, "MEDIUM")


import data.databricks.governance.common
import future.keywords.if
import future.keywords.in


# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "MEDIUM")


import data.databricks.governance.common
import future.keywords.if
import future.keywords.in


# --- Apply Common Governance Logic ---
is_violation := common.is_violation(violation_reasons)
has_approved_exception := common.has_approved_exception(input.allowlist_records, input.resource.id, is_violation, input.request_time)
has_pending_exception := common.has_pending_exception(input.allowlist_records, input.resource.id, is_violation, has_approved_exception)

action := common.resolve_action(is_violation, has_approved_exception, has_pending_exception, "KILL")
reason := common.resolve_reason(is_violation, has_approved_exception, has_pending_exception, input.allowlist_records, input.resource.id, input.request_time, violation_reasons)
severity := common.resolve_severity(is_violation, has_approved_exception, has_pending_exception, "MEDIUM")
