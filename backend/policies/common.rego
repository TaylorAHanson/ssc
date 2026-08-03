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

# "No expiry" reaches us two different ways: the key can be absent, or present
# and null. The app always emits the key and sets it to null for a never-expiring
# exception, so null is in fact the common case.
#
# These used to be `not exception.expires_at` plus a clause that required
# `expires_at != null`. In Rego `not` only succeeds on an undefined (or false)
# term, and null is neither — so a null expiry matched NEITHER clause,
# is_valid_expiry came back undefined, and the approved exception was silently
# discarded. Reading through object.get collapses absent and null into one case.
is_valid_expiry(exception, current_time) if {
    object.get(exception, "expires_at", null) == null
}

# Compared as ISO-8601 strings, which sorts correctly because both sides are
# produced by datetime.isoformat() and so share a layout.
is_valid_expiry(exception, current_time) if {
    expiry := object.get(exception, "expires_at", null)
    expiry != null
    expiry > current_time
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

format_reasons(reasons) = formatted if {
    sorted_reasons := sort(reasons)
    formatted := concat(" ", [sprintf("%d. %s", [i + 1, msg]) | some i; msg := sorted_reasons[i]])
}

resolve_reason(is_viol, has_approved, has_pending, allowlist_records, resource_id, request_time, violation_reasons) = format_reasons(violation_reasons) if {
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
