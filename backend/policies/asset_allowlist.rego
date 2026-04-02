package databricks.governance.asset_allowlist

import future.keywords.in
import future.keywords.contains
import future.keywords.if

default action := "ALLOW"
default is_violation := false
default reason := "Resource is permitted."
default severity := "NONE"

restricted_environments := ["enterprise", "prod"]
restricted_assets := ["app", "genie_space", "dashboard", "job", "notebook"]

# Identify if the resource violates the baseline rule
is_violation if {
    input.workspace.type in restricted_environments
    input.resource.type in restricted_assets
}

# Find matching allowlist records for this resource
matching_exceptions := [
    e | e := input.allowlist_records[_];
    e.resource_id == input.resource.id
]

# --- Helpers (must not reference `action`; avoids rego_recursion_error) ---

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

# --- Outcomes (exclusive; precedence: approved > pending > kill) ---

action = "SKIPPED_ALLOWLIST" if {
    has_approved_exception
}

action = "PENDING_EXCEPTION" if {
    has_pending_exception
}

action = "KILL" if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}

# --- Reasons ---

reason = exception.justification if {
    has_approved_exception
    some exception in matching_exceptions
    exception.status == "approved"
    is_valid_expiry(exception, input.request_time)
}

reason = "Exception request is pending admin approval." if {
    has_pending_exception
}

reason = sprintf("Unauthorized %s resource in %s workspace. No valid exception found.", [input.resource.type, input.workspace.type]) if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}

# --- Severity (derive from helpers, not `action`, to keep the graph acyclic) ---

severity := "NONE" if {
    has_approved_exception
}

severity := "MEDIUM" if {
    has_pending_exception
}

severity := "CRITICAL" if {
    is_violation
    not has_approved_exception
    not has_pending_exception
}

# Helper to check expiry
is_valid_expiry(exception, current_time) if {
    not exception.expires_at
}

is_valid_expiry(exception, current_time) if {
    exception.expires_at != null
    # Assuming expires_at and current_time are passed as unix timestamps or ISO strings that can be compared
    # For simplicity, if they are ISO strings, simple string comparison works for standard formats
    exception.expires_at > current_time
}
