package databricks.governance.over_provisioned_warehouses

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Warehouse is correctly provisioned."
default severity := "NONE"

severity := "MEDIUM" if {
    is_violation
}

is_violation if {
    input.resource.type == "warehouse"
    has_bad_auto_stop(input.resource.auto_stop_mins)
    input.resource.utilization_percent < 5
    input.resource.queue_depth == 0
}

has_bad_auto_stop(null)
has_bad_auto_stop(mins) if {
    mins > 120
}

action = "STOP_AND_RECONFIGURE" if {
    is_violation
}

reason = "Warehouse is under-utilized (< 5%) with high auto-stop time. Stopping and reconfiguring." if {
    action == "STOP_AND_RECONFIGURE"
}
