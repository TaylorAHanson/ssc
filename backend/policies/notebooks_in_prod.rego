package databricks.governance.notebooks_in_prod

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Notebook is permitted."

# Only applies to prod workspaces
is_violation if {
    input.workspace.type == "prod"
    input.resource.type == "notebook"
    is_in_restricted_path(input.resource.path)
}

is_in_restricted_path(path) if {
    startswith(path, "/Shared/")
}
is_in_restricted_path(path) if {
    startswith(path, "/Repos/")
}
is_in_restricted_path(path) if {
    startswith(path, "/Workspace/Shared/")
}
is_in_restricted_path(path) if {
    startswith(path, "/Workspace/Repos/")
}

action = "KILL" if {
    is_violation
}

reason = "Notebooks are not allowed in Shared or Repos directories in Production workspaces." if {
    action == "KILL"
}
