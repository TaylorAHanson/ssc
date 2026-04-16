package databricks.governance.data_certification

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Resource complied with policies."
default severity := "NONE"

# This policy enforces the Data Certification Checklist.
# It applies to datasets (e.g. tables) that are certified or seeking certification.

# 1. Data Quality
violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    input.resource.tdq_score < input.resource.tdq_threshold
    msg := sprintf("TDQ (Technical Data Quality) score must be at least %v%% for a certified dataset.", [input.resource.tdq_threshold])
}

violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    input.resource.bdq_score < input.resource.bdq_threshold
    msg := sprintf("BDQ (Business Data Quality) score must be at least %v%% for a certified dataset.", [input.resource.bdq_threshold])
}

# 2. Metadata exists
violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    not input.resource.catalog_description
    msg := "Catalog description is missing for the certified dataset."
}

violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    not input.resource.schema_description
    msg := "Schema description is missing for the certified dataset."
}

violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    input.resource.all_columns_have_descriptions == false
    msg := "One or more columns are missing descriptions in the certified dataset."
}

# 3. Access Control exists
violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    input.resource.rbac_defined == false
    msg := "RBAC (Role-Based Access Control) must be defined for a certified dataset."
}

violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    input.resource.abac_needed == true
    input.resource.abac_defined == false
    msg := "ABAC (Attribute-Based Access Control) is marked as needed but is not defined for the certified dataset."
}

# 4. Tagging & Classification
required_tags := {"Owner group", "Approver group", "Domain", "SLO/SLA"}
violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    tag := required_tags[_]
    not input.resource.tags[tag]
    msg := sprintf("Required tag '%v' is missing from the certified dataset.", [tag])
}

violation_reasons contains msg if {
    input.resource.type == "table"
    input.resource.certification_eligible == true
    not input.resource.data_classification
    msg := "Data classification (e.g., PII / No PII) must be defined for a certified dataset."
}

# --- Apply Common Governance Logic ---
# Exceptions do not apply to data certification since it represents a target state.
is_violation := count(violation_reasons) > 0

sorted_reasons := sort(violation_reasons)
formatted_reasons := [sprintf("%d. %s", [i + 1, msg]) | some i; msg := sorted_reasons[i]]

action := "UNCERTIFY" if {
    is_violation
} else := "CERTIFY" if {
    input.resource.certification_eligible
} else := "ALLOW"

reason := concat(" ", formatted_reasons) if {
    is_violation
} else := "Dataset meets all certification requirements." if {
    input.resource.certification_eligible
} else := "Dataset is not seeking certification."

severity := "HIGH" if {
    is_violation
} else := "NONE"
