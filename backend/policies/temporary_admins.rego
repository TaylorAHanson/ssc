package databricks.governance.temporary_admins

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "User privileges are valid."
default severity := "NONE"

severity := "HIGH" if {
    is_violation
}

is_violation if {
    input.resource.type == "user_grant"
    input.resource.role == "admin"
    input.resource.is_temporary
    input.resource.ttl_expired
}

action = "REVOKE_ADMIN" if {
    is_violation
}

reason = "Temporary admin privileges have exceeded their TTL and are revoked." if {
    action == "REVOKE_ADMIN"
}
