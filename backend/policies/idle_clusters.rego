package databricks.governance.idle_clusters

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Cluster is active."

is_violation if {
    input.resource.type == "cluster"
    input.resource.state == "RUNNING"
    input.resource.idle_hours > 2
}

# The table says clusters.delete, but usually we just terminate them. We'll use KILL.
action = "KILL" if {
    is_violation
}

reason = "Cluster has been running idle for more than 2 hours without activity." if {
    action == "KILL"
}
