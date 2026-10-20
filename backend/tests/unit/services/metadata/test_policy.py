"""Key policies, exempt keys, item checks and sensitivity rules (ADR-0006 D4)."""
import logging

import pytest

from app.core.config import settings
from app.services.metadata import policy
from app.services.metadata.types import ChangeItem
from app.workflows.tag_policy import TagPolicy

POLICIES = [
    {"key": "dataset", "applies_to": "table", "allowed_values": "", "pattern": "^[a-z][a-z0-9_]*$",
     "editor_groups": "", "sensitive": False, "required": "true", "description": "Logical dataset."},
    {"key": "data_owner", "applies_to": "catalog, schema, table", "pattern": "", "sensitive": True},
    {"key": "approver_group", "applies_to": "Tables", "sensitive": "true", "required": "false"},
    {"key": "classification", "applies_to": "", "allowed_values": "public, internal, confidential, restricted"},
    {"key": "mv_only", "applies_to": "materialized view"},
    {"key": "col_note", "applies_to": "columns", "required": "yes"},
    {"key": "steward", "editor_groups": "stewards, Data Office", "sensitive": "no"},
    {"key": "Dataset", "applies_to": "column"},
    {"key": "", "applies_to": "table"},
]


@pytest.fixture(autouse=True)
def policy_settings(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_KEY_POLICIES", [dict(p) for p in POLICIES])
    monkeypatch.setattr(settings, "METADATA_KEY_MODE", "open")
    monkeypatch.setattr(settings, "METADATA_EXEMPT_KEYS", ["System.Certification_Status"])
    monkeypatch.setattr(settings, "METADATA_CLAIM_ENABLED", True)
    monkeypatch.setattr(settings, "METADATA_CLAIM_TAG_KEY", "metadata_manager_instance")
    monkeypatch.setattr(settings, "METADATA_OWNER_TAG_KEY", "data_owner")
    monkeypatch.setattr(settings, "METADATA_CLASSIFICATION_KEY", "classification")
    monkeypatch.setattr(settings, "METADATA_CLASSIFICATION_LEVELS", ["public", "internal", "confidential", "restricted"])
    monkeypatch.setattr(settings, "METADATA_PII_KEYS", ["pii", "contains_pii"])


def item(key="dataset", after="orders", *, action="set", before=None, before_exists=None,
         securable_type="table", name="c.s.t", column=None, field="tag"):
    if before_exists is None:
        before_exists = before is not None
    fqn = name.lower() + (f"::{column.lower()}" if column else "")
    return ChangeItem(fqn_key=fqn, name=name, column=column, securable_type=securable_type, field=field,
                      key=key if field == "tag" else None, action=action, before=before,
                      before_exists=before_exists, after=after if action in ("set", "comment_set") else None)


def codes(findings):
    return [f.code for f in findings]


# --- key policies ---------------------------------------------------------------------


def test_key_policies_parse_comma_strings_and_tolerant_booleans():
    pols = {p.key: p for p in policy.key_policies()}

    assert list(pols) == ["dataset", "data_owner", "approver_group", "classification", "mv_only", "col_note", "steward"]
    assert pols["dataset"].required is True and pols["dataset"].sensitive is False
    assert pols["approver_group"].sensitive is True and pols["approver_group"].required is False
    assert pols["approver_group"].applies_to == ["table"]
    assert pols["data_owner"].applies_to == ["catalog", "schema", "table"]
    assert pols["data_owner"].pattern is None
    assert pols["classification"].allowed_values == ["public", "internal", "confidential", "restricted"]
    assert pols["classification"].applies_to == []
    assert pols["mv_only"].applies_to == ["materialized_view"]
    assert pols["col_note"].applies_to == ["column"] and pols["col_note"].required is True
    assert pols["steward"].editor_groups == ["stewards", "Data Office"] and pols["steward"].sensitive is False


def test_policy_for_matches_keys_case_insensitively():
    assert policy.policy_for("DATASET").key == "dataset"
    assert policy.policy_for(" Data_Owner ").key == "data_owner"
    assert policy.policy_for("nope") is None
    assert policy.policy_for("") is None


def test_exempt_keys_always_include_the_claim_key(monkeypatch):
    assert policy.exempt_keys() == ["system.certification_status", "metadata_manager_instance"]
    assert policy.is_exempt("SYSTEM.certification_status")
    assert policy.is_exempt("Metadata_Manager_Instance")
    assert not policy.is_exempt("pii")

    # Catalogs keep other deployments' claim markers when claims are turned off here.
    monkeypatch.setattr(settings, "METADATA_CLAIM_ENABLED", False)

    assert policy.exempt_keys() == ["system.certification_status", "metadata_manager_instance"]


# --- check_item -----------------------------------------------------------------------


def test_a_valid_tag_has_no_findings():
    assert policy.check_item(item("dataset", "orders")) == []
    assert policy.check_item(item("free_form", "anything")) == []


@pytest.mark.parametrize("action", ["set", "unset"])
def test_exempt_keys_are_denied(action):
    found = policy.check_item(item("system.certification_status", "certified", action=action, before="x"))

    assert codes(found) == ["exempt_key"]
    assert found[0].severity == "deny"
    assert found[0].fqn_key == "c.s.t" and found[0].key == "system.certification_status"
    assert found[0].message.startswith("c.s.t: ")


def test_unknown_keys_are_denied_only_in_known_only_mode(monkeypatch):
    assert policy.check_item(item("whatever", "x")) == []

    monkeypatch.setattr(settings, "METADATA_KEY_MODE", "known_only")

    found = policy.check_item(item("whatever", "x"))
    assert codes(found) == ["unknown_key"]
    assert "dataset" in found[0].message
    # Removing a key with no policy is always allowed, so old tags can be cleaned up.
    assert policy.check_item(item("whatever", action="unset", before="x")) == []


@pytest.mark.parametrize("key,securable_type,ok", [
    ("dataset", "table", True),
    ("dataset", "view", True),               # "table" covers every table-like object
    ("dataset", "materialized_view", True),
    ("dataset", "column", False),
    ("dataset", "schema", False),
    ("data_owner", "catalog", True),
    ("data_owner", "column", False),
    ("mv_only", "materialized_view", True),
    ("mv_only", "table", False),             # a specific type matches only itself
    ("col_note", "column", True),
    ("col_note", "table", False),
    ("classification", "column", True),      # blank applies_to = everything
])
def test_applies_to(key, securable_type, ok):
    column = "col" if securable_type == "column" else None
    value = "orders" if key == "dataset" else ("public" if key == "classification" else "x")
    found = policy.check_item(item(key, value, securable_type=securable_type, column=column))

    assert ("not_applicable" not in codes(found)) is ok


def test_not_applicable_names_the_allowed_kinds():
    found = policy.check_item(item("data_owner", "team", securable_type="column", column="c"))

    assert found[0].message == "c.s.t.c: 'data_owner' can only be set on catalogs, schemas and tables and views."


def test_relation_level_applies_to_every_table_like_type(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_KEY_POLICIES", [{"key": "k", "applies_to": "relation"}])

    assert policy.check_item(item("k", "v", securable_type="streaming_table")) == []
    assert codes(policy.check_item(item("k", "v", securable_type="schema", name="c.s"))) == ["not_applicable"]


def test_pattern_must_match_the_whole_value():
    found = policy.check_item(item("dataset", "Orders-2024"))

    assert codes(found) == ["pattern"]
    assert "Logical dataset." in found[0].message


def test_a_key_only_tag_is_checked_as_an_empty_value():
    assert codes(policy.check_item(item("dataset", None))) == ["pattern"]
    assert codes(policy.check_item(item("classification", None))) == ["not_allowed_value"]


def test_allowed_values_are_exact():
    assert policy.check_item(item("classification", "internal")) == []
    found = policy.check_item(item("classification", "Secret"))

    assert codes(found) == ["not_allowed_value"]
    assert "public, internal, confidential, restricted" in found[0].message


def test_governed_values():
    governed = {"Region": ["emea", "amer"], "free": []}

    assert codes(policy.check_item(item("region", "apac"), governed=governed)) == ["governed_value"]
    assert policy.check_item(item("region", "emea"), governed=governed) == []
    # Empty allowed list = any value; key-only tags aren't checked (UC doesn't either).
    assert policy.check_item(item("free", "x"), governed=governed) == []
    assert policy.check_item(item("region", None), governed=governed) == []
    assert policy.check_item(item("region", action="unset", before="apac"), governed=governed) == []


def test_removing_a_required_key_is_denied_where_it_applies():
    assert codes(policy.check_item(item("dataset", action="unset", before="orders"))) == ["required_key"]
    assert policy.check_item(item("dataset", action="unset", before="orders",
                                  securable_type="column", column="c")) == []
    assert codes(policy.check_item(item("col_note", action="unset", before="x",
                                        securable_type="column", column="c"))) == ["required_key"]
    assert policy.check_item(item("approver_group", action="unset", before="g")) == []


def test_several_findings_are_collected():
    found = policy.check_item(item("classification", "Secret"), governed={"classification": ["public"]})

    assert codes(found) == ["not_allowed_value", "governed_value"]


def test_comment_items_have_no_findings():
    assert policy.check_item(item(field="comment", action="comment_set", after="hello")) == []


def test_a_bad_regex_is_logged_and_not_checked(monkeypatch, caplog):
    policy._compile.cache_clear()
    monkeypatch.setattr(settings, "METADATA_KEY_POLICIES", [{"key": "k", "pattern": "([unclosed"}])

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.policy"):
        assert policy.check_item(item("k", "anything")) == []
    assert "not a valid regular expression" in caplog.text


# --- sensitivity ----------------------------------------------------------------------


def test_plain_changes_are_not_sensitive():
    assert policy.sensitive_reasons(item("dataset", "orders")) == []
    assert policy.sensitive_reasons(item(field="comment", action="comment_set", after="x")) == []
    assert policy.sensitive_reasons(item(field="comment", action="comment_clear", before="x")) == []


@pytest.mark.parametrize("key,action", [("data_owner", "set"), ("DATA_OWNER", "set"), ("data_owner", "unset")])
def test_any_change_to_the_owner_key_is_sensitive(key, action):
    reasons = policy.sensitive_reasons(item(key, "team_a", action=action, before="team_b"))

    assert len(reasons) == 1 and "owner tag" in reasons[0]


def test_keys_marked_sensitive():
    assert policy.sensitive_reasons(item("approver_group", "g")) == ["'approver_group' is a sensitive key."]
    assert policy.sensitive_reasons(item("steward", "g")) == []


@pytest.mark.parametrize("before,after,action,sensitive", [
    ("restricted", "internal", "set", True),
    ("Confidential", "PUBLIC", "set", True),
    ("internal", "restricted", "set", False),
    ("internal", "internal", "set", False),
    (None, "public", "set", False),            # no classification before
    ("internal", None, "unset", True),         # removal
    ("internal", "Secret", "set", True),       # known -> unknown
    ("internal", None, "set", True),           # known -> key-only
    ("legacy", "public", "set", False),        # unknown -> known can't be judged
])
def test_classification_downgrades(before, after, action, sensitive):
    reasons = policy.sensitive_reasons(item("classification", after, action=action, before=before,
                                            before_exists=before is not None))

    assert bool(reasons) is sensitive


def test_classification_check_follows_the_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_CLASSIFICATION_KEY", "sensitivity")

    assert policy.sensitive_reasons(item("classification", "public", before="restricted")) == []
    assert policy.sensitive_reasons(item("sensitivity", "public", before="restricted"))

    monkeypatch.setattr(settings, "METADATA_CLASSIFICATION_KEY", "")
    assert policy.sensitive_reasons(item("sensitivity", "public", before="restricted")) == []


@pytest.mark.parametrize("key,before,before_exists,after,action,sensitive", [
    ("pii", "true", True, None, "unset", True),
    ("pii", None, True, None, "unset", True),        # key-only flag removed
    ("PII", "true", True, "false", "set", True),
    ("contains_pii", "yes", True, "No", "set", True),
    ("pii", "true", True, "0", "set", True),
    ("pii", "false", True, "no", "set", False),      # wasn't flagging anything
    ("pii", "false", True, None, "unset", False),
    ("pii", "true", True, "email", "set", False),
    ("pii", None, False, "false", "set", False),     # nothing was flagged before
    ("pii", None, False, "true", "set", False),
])
def test_removing_a_pii_flag(key, before, before_exists, after, action, sensitive):
    reasons = policy.sensitive_reasons(item(key, after, action=action, before=before, before_exists=before_exists))

    assert bool(reasons) is sensitive


def test_reasons_accumulate(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_KEY_POLICIES", [{"key": "classification", "sensitive": True}])

    reasons = policy.sensitive_reasons(item("classification", "public", before="restricted"))

    assert len(reasons) == 2


# --- TagPolicy adapter ----------------------------------------------------------------


def test_tag_policy_adapts_the_settings(monkeypatch):
    policy._compile.cache_clear()
    monkeypatch.setattr(settings, "METADATA_KEY_POLICIES", [dict(p) for p in POLICIES] + [
        {"key": "broken", "pattern": "([", "allowed_values": "a"},
    ])
    tp = policy.tag_policy()

    assert isinstance(tp, TagPolicy)
    assert tp.key_mode == "open"
    assert tp.is_reserved("system.certification_status")
    assert tp.known_keys["dataset"]["applies_to"] == ["table"]
    assert tp.known_keys["dataset"]["pattern"] == "^[a-z][a-z0-9_]*$"
    assert tp.known_keys["dataset"]["required"] is True
    assert tp.known_keys["mv_only"]["applies_to"] == ["table"]
    assert "applies_to" not in tp.known_keys["classification"]
    assert tp.known_keys["broken"]["pattern"] == "(?:a)"
    assert tp.max_tags_per_object

    problems = tp.check([{"table": "c.s.t", "set": {"classification": "secret", "dataset": "ok"}}])
    assert len(problems) == 1 and "classification" in problems[0]


def test_tag_policy_known_only_becomes_strict(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_KEY_MODE", "known_only")
    tp = policy.tag_policy()

    assert tp.key_mode == "strict"
    assert tp.check([{"table": "c.s.t", "set": {"unknown_key": "x"}}])
