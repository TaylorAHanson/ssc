package databricks.governance.dangling_sps

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Service Principal is active."

is_violation if {
    input.resource.type == "service_principal"
    input.resource.days_since_last_login > 90
}

# Suspend the SP and revoke tokens
action = "SUSPEND" if {
    is_violation
}

reason = "Service Principal has not logged in for over 90 days." if {
    action == "SUSPEND"
}
