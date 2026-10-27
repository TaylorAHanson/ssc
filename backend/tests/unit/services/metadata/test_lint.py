"""Typo and hygiene warnings in a metadata preview."""

from app.core.config import settings
from app.services.metadata import lint
from app.services.metadata.types import ChangeItem, Facet, KeyInfo, KeyPolicy


def _tag(key, after, *, name="sales_dev.bronze.orders", action="set"):
    return ChangeItem(
        fqn_key=name.lower(), name=name, securable_type="table", field="tag", key=key,
        action=action, after=after if action == "set" else None,
    )


def _key(key, *values, count=5, allowed=None):
    return KeyInfo(
        key=key, count=count, values=[Facet(value=v, count=n) for v, n in values],
        policy=KeyPolicy(key=key, allowed_values=allowed or []) if allowed else None,
    )


KEYS = [
    _key("classification", ("internal", 9), ("restricted", 4), allowed=["internal", "restricted"]),
    _key("domain", ("sales-eng", 6), ("finance", 3)),
]


def _codes(items, keys=KEYS):
    return [(f.code, f.severity, f.key) for f in lint.check(items, keys)]


def test_a_near_miss_of_a_known_key_is_a_warning_that_names_the_key():
    findings = lint.check([_tag("clasification", "internal")], KEYS)

    assert [(f.code, f.severity, f.key) for f in findings] == [("near_miss_key", "warn", "clasification")]
    assert "classification" in findings[0].message and findings[0].fqn_key == "sales_dev.bronze.orders"


def test_a_key_that_only_differs_by_case_is_flagged():
    assert _codes([_tag("Classification", "internal")]) == [("near_miss_key", "warn", "Classification")]


def test_a_known_key_and_a_brand_new_unrelated_key_are_not_flagged():
    assert _codes([_tag("classification", "internal"), _tag("owner_team", "x")]) == []


def test_a_near_miss_of_an_established_value_suggests_it():
    findings = lint.check([_tag("domain", "sales-egn")], KEYS)

    assert [(f.code, f.severity) for f in findings] == [("near_miss_value", "warn")]
    assert "'sales-eng'" in findings[0].message


def test_a_value_that_only_differs_by_case_is_flagged():
    assert _codes([_tag("domain", "Finance")]) == [("case_collision", "warn", "domain")]


def test_a_value_nobody_uses_yet_is_not_a_warning():
    assert _codes([_tag("domain", "logistics")]) == []


def test_stray_whitespace_and_odd_characters_are_flagged():
    codes = {c for c, _, _ in _codes([_tag("domain", " finance "), _tag("domain", "fin\u2014ance")])}
    assert {"whitespace", "non_ascii"} <= codes


def test_a_key_with_a_fixed_list_of_values_is_left_to_that_list():
    assert _codes([_tag("classification", "internl")]) == []


def test_each_distinct_key_and_value_is_reported_once_however_many_objects_carry_it():
    items = [_tag("clasification", "internal", name=f"sales_dev.bronze.t{i}") for i in range(40)]
    assert len(lint.check(items, KEYS)) == 1


def test_removals_and_descriptions_are_not_checked():
    comment = ChangeItem(fqn_key="a.b.c", name="a.b.c", securable_type="table", field="comment",
                         key="", action="comment_set", after="Orders")
    assert lint.check([_tag("clasification", None, action="unset"), comment], KEYS) == []


def test_nothing_is_checked_without_a_vocabulary():
    assert lint.check([_tag("clasification", "internal")], []) == []


def test_a_long_list_of_findings_is_cut_off_with_a_note():
    items = [_tag(f"classificatio{c}", "x") for c in "abcdefghijklmnopqrstuvwxyz"]
    findings = lint.check(items, KEYS)

    assert len(findings) == lint.MAX_FINDINGS + 1
    assert (findings[-1].code, findings[-1].severity) == ("lint_more", "info")


def test_the_check_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_LINT_CHECK", False)
    assert lint.known_keys(db=None) == []


def test_a_failure_reading_the_keys_is_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(lint.search, "list_keys", boom)
    assert lint.known_keys(db=None) == []
