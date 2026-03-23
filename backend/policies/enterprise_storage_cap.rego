package databricks.governance.enterprise_storage_cap

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Storage usage is within limits."
default severity := "NONE"

severity := "HIGH" if {
    is_violation
}

is_violation if {
    input.workspace.type == "enterprise"
    input.resource.type == "user_storage"
    input.resource.usage_gb > 50
}

action = "WARN" if {
    is_violation
}

reason = "User storage usage exceeds 50 GB cap in the Enterprise Hub." if {
    action == "WARN"
}
