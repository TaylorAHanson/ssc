package databricks.governance.stale_jobs

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Job is active or scheduled."
default severity := "NONE"

severity := "MEDIUM" if {
    is_violation
}

# Thresholds
# All environments: > 45 days idle if unscheduled
# Development environments: relaxed thresholds, doubled to 90 days.

is_violation if {
    input.resource.type == "job"
    not input.resource.has_schedule
    
    threshold_days := get_threshold(input.workspace.type)
    input.resource.days_since_last_run > threshold_days
}

get_threshold(workspace_type) = 90 if {
    workspace_type in ["dev", "test"]
} else = 45

# For jobs, we can issue a PAUSE or KILL. Let's issue PAUSE to give the handler a specific action, or just KILL to be uniform. 
# The table says "jobs.pause or jobs.delete". We'll use "PAUSE" as the primary action for stale jobs as a softer remediation.
action = "PAUSE" if {
    is_violation
}

reason = sprintf("Unscheduled job has not been run in over %v days.", [get_threshold(input.workspace.type)]) if {
    action == "PAUSE"
}
