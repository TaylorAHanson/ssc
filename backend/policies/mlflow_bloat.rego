package databricks.governance.mlflow_bloat

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "MLflow experiment is active or linked."
default severity := "NONE"

severity := "LOW" if {
    is_violation
}

is_violation if {
    input.workspace.type == "domain"
    input.resource.type == "mlflow_experiment"
    not input.resource.has_linked_model
    input.resource.days_since_last_run > 30
}

action = "ARCHIVE" if {
    is_violation
}

reason = "MLflow experiment is unlinked to any model and hasn't been run in over 30 days." if {
    action == "ARCHIVE"
}
