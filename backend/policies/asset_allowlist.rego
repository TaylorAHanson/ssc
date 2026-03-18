package databricks.governance.asset_allowlist

import future.keywords.in
import future.keywords.contains
import future.keywords.if

default action := "ALLOW"
default is_violation := false
default reason := "Resource is permitted."

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

# ALLOW: If there is an approved exception that hasn't expired
action = "SKIPPED_ALLOWLIST" if {
    is_violation
    some exception in matching_exceptions
    exception.status == "approved"
    
    # Check expiry
    # If expires_at is null/missing, it's valid forever. Otherwise it must be in the future.
    is_valid_expiry(exception, input.request_time)
}

# Provide reason for SKIPPED_ALLOWLIST
reason = exception.justification if {
    action == "SKIPPED_ALLOWLIST"
    some exception in matching_exceptions
    exception.status == "approved"
    is_valid_expiry(exception, input.request_time)
}

# REPRIEVE: If there is a pending request, spare it temporarily
action = "PENDING_EXCEPTION" if {
    is_violation
    not action == "SKIPPED_ALLOWLIST"
    some exception in matching_exceptions
    exception.status == "pending"
}

reason = "Exception request is pending admin approval." if {
    action == "PENDING_EXCEPTION"
}

# KILL: If it's a violation and neither skipped nor pending
action = "KILL" if {
    is_violation
    not action == "SKIPPED_ALLOWLIST"
    not action == "PENDING_EXCEPTION"
}

reason = sprintf("Unauthorized %s resource in %s workspace. No valid exception found.", [input.resource.type, input.workspace.type]) if {
    action == "KILL"
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