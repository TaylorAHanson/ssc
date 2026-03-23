package databricks.governance.orphan_volumes

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Volume is active."
default severity := "NONE"

severity := "MEDIUM" if {
    is_violation
}

is_violation if {
    input.resource.type == "volume"
    input.resource.days_since_last_access > 60
}

action = "KILL" if {
    is_violation
}

reason = "Unattached or unused storage volume has not been accessed in over 60 days." if {
    action == "KILL"
}
