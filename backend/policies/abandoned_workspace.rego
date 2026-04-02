package databricks.governance.abandoned_workspace

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Workspace is active."
default severity := "NONE"

severity := "HIGH" if {
    is_violation
}

is_violation if {
    input.resource.type == "workspace"
    input.resource.days_since_last_login > 30
    input.resource.days_since_last_query > 30
}

action = "ARCHIVE_FLAG" if {
    is_violation
}

reason = "Workspace has had no logins or queries in over 30 days. Flagged for archival." if {
    action == "ARCHIVE_FLAG"
}
