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
	"assets_declared": "Data Contract declares at least one table or view",
	"table_exists": "Referenced tables/views are visible to the governance scanner",
	"reliability_window_tag": "Tables carry a 'reliability_window' tag",
	"dq_history_fetched": "Data quality rule history is fetchable within the reliability window",
	"dq_zero_failed": "Zero failed data quality rules within the reliability window",
	"catalog_description": "Catalog has a description",
	"schema_description": "Schema has a description",
	"column_descriptions": "All columns have descriptions",
	"required_tags": "Required tags present (dataset, reliability_window, data_owner, approver_group, access_group)",
	"access_controls_defined": "Tables have access controls (grants) defined",
}

# === Rule -> category map (single source of truth for the exec report + UI) ===
# Categories group the checklist into the high-level buckets leadership cares
# about: is the contract structurally sound, is metadata documented, are the
# governance tags present, and does the data pass its quality rules.
rule_category := {
	"yaml_valid": "Structure",
	"assets_declared": "Structure",
	"table_exists": "Structure",
	"catalog_description": "Metadata",
	"schema_description": "Metadata",
	"column_descriptions": "Metadata",
	"required_tags": "Tagging",
	"reliability_window_tag": "Tagging",
	"access_controls_defined": "Access Control",
	"dq_history_fetched": "Data Quality",
	"dq_zero_failed": "Data Quality",
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

# Every other rule below is scoped by `some asset in input.resource.assets`, so
# a product with no assets can't fail any of them and would otherwise certify on
# a vacuous 100%. That is not hypothetical: contract generation skips tables it
# can't read (see draft_odcs.fetch_datasets_metadata) and emits an empty
# `schema: []`, which discovery turns into an empty asset list. An empty
# contract is the *least* verified thing here, so it must never be the
# best-scoring one.
#
# Gated on valid YAML so an unparseable contract reports only the parse failure
# rather than also reporting the empty asset list that failure necessarily implies.
violations["assets_declared"] contains msg if {
	applies["assets_declared"]
	# Read both fields through object.get: a bare `input.resource.invalid_yaml`
	# is *undefined* rather than false when the key is absent, which would make
	# this whole body undefined and silently skip the rule.
	object.get(input.resource, "invalid_yaml", false) != true
	count(object.get(input.resource, "assets", [])) == 0
	msg := "Data Contract declares no tables or views, so no certification checks could be performed against it."
}

violations["table_exists"] contains msg if {
	applies["table_exists"]
	some asset in input.resource.assets
	asset.table_exists == false
	# Deliberately hedged: Unity Catalog's information_schema filters invisible
	# objects out silently, so "absent" and "no BROWSE grant" are indistinguishable
	# from here. Naming both keeps a permissions gap from reading as a missing table.
	msg := sprintf("Table or view '%v' was not found in Unity Catalog — it either does not exist, or the governance service principal lacks BROWSE on its catalog.", [asset.name])
}

violations["reliability_window_tag"] contains msg if {
	applies["reliability_window_tag"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	not asset.tags["reliability_window"]
	msg := sprintf("The 'reliability_window' tag is required for %v '%v'.", [asset.type, asset.name])
}

# DQ rules are intentionally NOT gated on the reliability_window tag. Discovery
# evaluates data quality even when the tag is absent (using a default lookback
# window), so DQ failures surface in the SAME scan as the missing-tag finding
# rather than only appearing on a later run once the tag is added. The missing
# tag itself is still reported separately by the reliability_window_tag rule.
violations["dq_history_fetched"] contains msg if {
	applies["dq_history_fetched"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	asset.failed_rule_count < 0
	msg := sprintf("Failed to fetch data quality rule history within the reliability window for %v '%v'.", [asset.type, asset.name])
}

violations["dq_zero_failed"] contains msg if {
	applies["dq_zero_failed"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
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

# Access controls (RBAC) must be defined on each table. We only flag this when
# the grants were actually READABLE (asset.rbac_readable == true); if the SP
# lacked MANAGE/ownership/workspace-admin to run SHOW GRANTS the check is skipped
# rather than failed, so a permission gap never false-flags every table.
violations["access_controls_defined"] contains msg if {
	applies["access_controls_defined"]
	some asset in input.resource.assets
	asset.table_exists != false
	asset.type == "table"
	asset.rbac_readable == true
	asset.rbac_defined == false
	msg := sprintf("No access controls (grants) are defined on %v '%v'.", [asset.type, asset.name])
}

# === Structured per-rule results (consumed by the checklist UI) ===
rule_results contains result if {
	some rule_id
	applies[rule_id]
	rule_violations := object.get(violations, rule_id, set())
	result := {
		"id": rule_id,
		"description": rule_metadata[rule_id],
		"category": object.get(rule_category, rule_id, "Other"),
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
