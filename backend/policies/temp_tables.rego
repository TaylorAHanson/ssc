package databricks.governance.temp_tables

import future.keywords.if
import future.keywords.in

default action := "ALLOW"
default is_violation := false
default reason := "Table is permitted."
default severity := "NONE"

severity := "MEDIUM" if {
    is_violation
}

is_violation if {
    input.resource.type == "table"
    is_temp_table(input.resource.name)
    input.resource.days_old > 7
}

is_temp_table(name) if {
    endswith(name, "_temp")
}
is_temp_table(name) if {
    endswith(name, "_test")
}

action = "DROP" if {
    is_violation
}

reason = "Temporary tables older than 7 days are automatically dropped." if {
    action == "DROP"
}
