package databricks.governance.tag_compliance

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Resource is fully tagged."

# Applies to all workspaces, resources that can be tagged (cluster, warehouse, job)
taggable_resources := ["cluster", "warehouse", "job"]

is_violation if {
    input.resource.type in taggable_resources
    missing_required_tags
}

missing_required_tags if {
    not input.resource.tags["cost-center"]
}

missing_required_tags if {
    not input.resource.tags["owner"]
}

# The expected kill action in the table is to stop the compute, or in a generic sense we issue a "KILL" 
# which the Sentinel interprets and uses the correct API based on resource type.
action = "KILL" if {
    is_violation
}

reason = "Missing required tags. All clusters, warehouses, and jobs must have 'cost-center' and 'owner' tags." if {
    action == "KILL"
}
