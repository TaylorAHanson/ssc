package databricks.governance.common

import future.keywords.if
import future.keywords.in
import future.keywords.contains

# --- Core Violation Check ---
is_violation(violation_reasons) = true if {
    count(violation_reasons) > 0
} else = false

# --- Allowlist Exception Logic ---
matching_exceptions(allowlist_records, resource_id) := [
    e | e := allowlist_records[_];
    e.resource_id == resource_id
]

has_approved_exception(allowlist_records, resource_id, is_viol, request_time) = true if {
    is_viol
    some exception in matching_exceptions(allowlist_records, resource_id)
    exception.status == "approved"
    is_valid_expiry(exception, request_time)
} else = false

has_pending_exception(allowlist_records, resource_id, is_viol, has_approved) = true if {
    is_viol
    not has_approved
    some exception in matching_exceptions(allowlist_records, resource_id)
    exception.status == "pending"
} else = false

is_valid_expiry(exception, current_time) if {
    not exception.expires_at
}

is_valid_expiry(exception, current_time) if {
    exception.expires_at != null
    exception.expires_at > current_time
}

# --- Final Actions ---
resolve_action(is_viol, has_approved, has_pending, default_action) = "SKIPPED_ALLOWLIST" if {
    has_approved
}

resolve_action(is_viol, has_approved, has_pending, default_action) = "PENDING_EXCEPTION" if {
    has_pending
}

resolve_action(is_viol, has_approved, has_pending, default_action) = default_action if {
    is_viol
    not has_approved
    not has_pending
}

resolve_action(is_viol, has_approved, has_pending, default_action) = "ALLOW" if {
    not is_viol
}

# --- Final Reasons ---
resolve_reason(is_viol, has_approved, has_pending, allowlist_records, resource_id, request_time, violation_reasons) = exception.justification if {
    has_approved
    some exception in matching_exceptions(allowlist_records, resource_id)
    exception.status == "approved"
    is_valid_expiry(exception, request_time)
}

resolve_reason(is_viol, has_approved, has_pending, allowlist_records, resource_id, request_time, violation_reasons) = "Exception request is pending admin approval." if {
    has_pending
}

resolve_reason(is_viol, has_approved, has_pending, allowlist_records, resource_id, request_time, violation_reasons) = concat("; ", violation_reasons) if {
    is_viol
    not has_approved
    not has_pending
}

resolve_reason(is_viol, has_approved, has_pending, allowlist_records, resource_id, request_time, violation_reasons) = "Resource complied with policies." if {
    not is_viol
}

# --- Final Severity ---
resolve_severity(is_viol, has_approved, has_pending, default_severity) = "NONE" if {
    has_approved
}

resolve_severity(is_viol, has_approved, has_pending, default_severity) = "MEDIUM" if {
    has_pending
}

resolve_severity(is_viol, has_approved, has_pending, default_severity) = default_severity if {
    is_viol
    not has_approved
    not has_pending
}

resolve_severity(is_viol, has_approved, has_pending, default_severity) = "NONE" if {
    not is_viol
}
