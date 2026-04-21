package databricks.governance.data_certification

import data.databricks.governance.common
import future.keywords.if
import future.keywords.in
import future.keywords.contains

default action := "ALLOW"
default is_violation := false
default reason := "Data product complied with policies."
default severity := "NONE"

# This policy enforces the Data Certification Checklist.
# It applies to data products containing one or more tables/views.

# 1. Data Quality
violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    not asset.tags["reliability_window"]
    msg := sprintf("The 'reliability_window' tag is required for %v '%v'.", [asset.type, asset.name])
}

violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    asset.tags["reliability_window"]
    asset.failed_rule_count < 0
    msg := sprintf("Failed to fetch data quality rule history within the reliability window for %v '%v'.", [asset.type, asset.name])
}

violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    asset.tags["reliability_window"]
    asset.failed_rule_count > 0
    msg := sprintf("Failed data quality rule count is %v within the reliability window for %v '%v'. Must be 0.", [asset.failed_rule_count, asset.type, asset.name])
}

# 2. Metadata exists
violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    not asset.catalog_description
    msg := sprintf("Catalog description is missing for %v '%v'.", [asset.type, asset.name])
}

violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    not asset.schema_description
    msg := sprintf("Schema description is missing for %v '%v'.", [asset.type, asset.name])
}

violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    asset.all_columns_have_descriptions == false
    msg := sprintf("One or more columns are missing descriptions in %v '%v'.", [asset.type, asset.name])
}

# 3. Access Control exists
violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    asset.rbac_defined == false
    msg := sprintf("RBAC (Role-Based Access Control) must be defined for %v '%v'.", [asset.type, asset.name])
}

# 4. Tagging & Classification
required_tags := {"owner_group", "approver_group", "domain", "slo_sla"}
violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    tag := required_tags[_]
    not asset.tags[tag]
    msg := sprintf("Required tag '%v' is missing from %v '%v'.", [tag, asset.type, asset.name])
}

violation_reasons contains msg if {
    input.resource.type == "data_product"
    some asset in input.resource.assets
    not asset.tags["data_classification"]
    msg := sprintf("Data classification tag (e.g., PII / No PII) must be defined for %v '%v'.", [asset.type, asset.name])
}

# --- Apply Common Governance Logic ---
is_violation := count(violation_reasons) > 0

sorted_reasons := sort(violation_reasons)
formatted_reasons := [sprintf("%d. %s", [i + 1, msg]) | some i; msg := sorted_reasons[i]]

is_currently_certified if {
    count(input.resource.assets) > 0
    count([asset | some asset in input.resource.assets; asset.tags["system.certification_status"] == "certified"]) == count(input.resource.assets)
}

action := "UNCERTIFY" if {
    input.resource.type == "data_product"
    is_violation
    is_currently_certified
} else := "CERTIFY" if {
    input.resource.type == "data_product"
    not is_violation
    not is_currently_certified
} else := "KEEP_UNCERTIFIED" if {
    input.resource.type == "data_product"
    is_violation
    not is_currently_certified
} else := "KEEP_CERTIFIED" if {
    input.resource.type == "data_product"
    not is_violation
    is_currently_certified
} else := "ALLOW"

reason := concat(" ", formatted_reasons) if {
    input.resource.type == "data_product"
    is_violation
} else := "Data product meets all technical certification requirements." if {
    input.resource.type == "data_product"
} else := "Policy does not apply to this resource type."

severity := "HIGH" if {
    is_violation
} else := "NONE"
