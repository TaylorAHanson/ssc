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
# Each rule is defined with:
#   - an `applies` membership predicate that says whether the rule was
#     considered for this input,
#   - a `violations[<rule_id>]` set that collects any failure messages.
# A single `rule_results` aggregation builds the structured checklist that
# the UI renders (one entry per applicable rule with pass/fail + messages).
# `violation_reasons` is derived from `rule_results` for backwards compat
# with the shared action/reason/severity helpers in common.rego.

# === Rule catalog (id -> human-readable description) ===
rule_metadata := {
	"yaml_valid": "Data Contract YAML is valid and parseable",
	"table_exists": "Referenced tables/views exist and are accessible",
	"reliability_window_tag": "Tables carry a 'reliability_window' tag",
	"dq_history_fetched": "Data quality rule history is fetchable within the reliability window",
	"dq_zero_failed": "Zero failed data quality rules within the reliability window",
	"catalog_description": "Catalog has a description",
	"schema_description": "Schema has a description",
	"column_descriptions": "All columns have descriptions",
	"required_tags": "Required tags present (dataset, reliability_window, data_owner, approver_group, access_group)",
}

# === Applicability ===
# Every rule in this policy applies whenever the resource is a data_product.
policy_applies if input.resource.type == "data_product"

applies contains rule_id if {
	policy_applies
	some rule_id, _ in rule_metadata
}

# === Per-rule violation conditions ===

violations["yaml_valid"] contains msg if {
	applies["yaml_valid"]
	input.resource.invalid_yaml == true
	msg := "Data Contract YAML is invalid and could not be parsed."
}

violations["table_exists"] contains msg if {
	applies["table_exists"]
	some asset in input.resource.assets
	asset.table_exists == false
	msg := sprintf("Table or view '%v' does not exist or cannot be accessed.", [asset.name])
}

violations["reliability_window_tag"] contains msg if {
	applies["reliability_window_tag"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	not asset.tags["reliability_window"]
	msg := sprintf("The 'reliability_window' tag is required for %v '%v'.", [asset.type, asset.name])
}

violations["dq_history_fetched"] contains msg if {
	applies["dq_history_fetched"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	asset.tags["reliability_window"]
	asset.failed_rule_count < 0
	msg := sprintf("Failed to fetch data quality rule history within the reliability window for %v '%v'.", [asset.type, asset.name])
}

violations["dq_zero_failed"] contains msg if {
	applies["dq_zero_failed"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	asset.tags["reliability_window"]
	asset.failed_rule_count > 0
	msg := sprintf("Failed data quality rule count is %v within the reliability window for %v '%v'. Must be 0.", [asset.failed_rule_count, asset.type, asset.name])
}

violations["catalog_description"] contains msg if {
	applies["catalog_description"]
	some asset in input.resource.assets
	asset.table_exists != false
	not asset.catalog_description
	msg := sprintf("Catalog description is missing for %v '%v'.", [asset.type, asset.name])
}

violations["schema_description"] contains msg if {
	applies["schema_description"]
	some asset in input.resource.assets
	asset.table_exists != false
	not asset.schema_description
	msg := sprintf("Schema description is missing for %v '%v'.", [asset.type, asset.name])
}

violations["column_descriptions"] contains msg if {
	applies["column_descriptions"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.all_columns_have_descriptions == false
	missing_cols_str := concat(", ", asset.missing_column_descriptions)
	msg := sprintf("The following columns are missing descriptions in %v '%v': %v.", [asset.type, asset.name, missing_cols_str])
}

required_tags := {"dataset", "reliability_window", "data_owner", "approver_group", "access_group"}

violations["required_tags"] contains msg if {
	applies["required_tags"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	tag := required_tags[_]
	not asset.tags[tag]
	msg := sprintf("Required tag '%v' is missing from %v '%v'.", [tag, asset.type, asset.name])
}

# === Structured per-rule results (consumed by the checklist UI) ===
rule_results contains result if {
	some rule_id
	applies[rule_id]
	rule_violations := object.get(violations, rule_id, set())
	result := {
		"id": rule_id,
		"description": rule_metadata[rule_id],
		"passed": count(rule_violations) == 0,
		"messages": sort([m | some m in rule_violations]),
	}
}

# === Backwards-compat: violation_reasons derived from rule_results ===
violation_reasons contains msg if {
	some r in rule_results
	msg := r.messages[_]
}

# === Common governance logic (action / reason / severity) ===
sorted_reasons := sort(violation_reasons)
formatted_reasons := [sprintf("%d. %s", [i + 1, msg]) | some i; msg := sorted_reasons[i]]

is_currently_certified if {
	count(input.resource.assets) > 0
	count([asset | some asset in input.resource.assets; asset.tags["system.certification_status"] == "certified"]) == count(input.resource.assets)
}

is_violation := count(violation_reasons) > 0

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
