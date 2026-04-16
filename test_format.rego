package test
import future.keywords.if

violation_reasons := {"A", "B", "C"}
is_violation := count(violation_reasons) > 0

sorted_reasons := sort(violation_reasons)
formatted_reasons := [sprintf("%d. %s", [i + 1, msg]) | some i; msg := sorted_reasons[i]]

reason := concat(" ", formatted_reasons) if {
    is_violation
} else := "OK"
