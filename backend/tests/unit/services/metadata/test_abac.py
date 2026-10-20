"""Access-policy (ABAC) impact in a metadata preview."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import settings
from app.services.metadata import abac, changes
from app.services.metadata.types import ChangeItem
from app.workflows.tag_abac import fetch_abac_references


def _policy(name, policy_type, match=(), when=None, on="sales_dev.bronze"):
    return SimpleNamespace(
        name=name,
        policy_type=f"PolicyType.{policy_type}",
        on_securable_fullname=on,
        match_columns=[SimpleNamespace(alias="c", condition=m) for m in match],
        when_condition=when,
    )


MASK_PII = _policy("mask_pii", "POLICY_TYPE_COLUMN_MASK", match=["has_tag('pii')"])
EU_ONLY = _policy("eu_only", "POLICY_TYPE_ROW_FILTER", when="has_tag_value('region', 'eu')")


def _provider(policies_by_name, failing=()):
    provider = MagicMock()
    calls = []

    def list_policies(securable_type, name, include_inherited=None):
        calls.append((securable_type, name))
        if name in failing:
            raise PermissionError("User does not have READ METADATA")
        return iter(policies_by_name.get(name, []))

    provider.client.policies.list_policies.side_effect = list_policies
    provider.calls = calls
    return provider


def _tag(name, key, after="true", *, column=None, action="set", before=None):
    fqn = name.lower() + (f"::{column.lower()}" if column else "")
    return ChangeItem(
        fqn_key=fqn, name=name, column=column, securable_type="column" if column else "table",
        field="tag", key=key, action=action, before=before, before_exists=before is not None,
        after=after if action == "set" else None,
    )


def _check(items, provider):
    return abac.check(items, changes._tag_plan(items), provider=provider)


def test_a_tag_a_column_mask_keys_on_is_flagged_and_raises_the_risk():
    items = [_tag("sales_dev.bronze.orders", "pii", column="email")]
    findings, impacts = _check(items, _provider({"sales_dev.bronze.orders": [MASK_PII]}))

    assert impacts == ["`sales_dev.bronze.orders.email`.`pii`: column mask 'mask_pii' (on sales_dev.bronze)"]
    assert [(f.code, f.severity) for f in findings] == [("abac_impact", "warn")]
    assert "column mask 'mask_pii'" in findings[0].message and "`" not in findings[0].message

    risk = changes._risk(items, changes._tag_plan(items), impacts)
    assert any(f["name"] == "abac_impact" for f in risk["factors"])


def test_a_value_condition_only_matches_that_value():
    provider = _provider({"sales_dev.bronze.orders": [EU_ONLY]})
    assert _check([_tag("sales_dev.bronze.orders", "region", "apac", before="us")], provider) == ([], [])
    findings, _ = _check([_tag("sales_dev.bronze.orders", "region", "eu", before="us")], provider)
    assert [f.code for f in findings] == ["abac_impact"]


def test_unreadable_policies_are_reported_not_raised():
    findings, impacts = _check([_tag("sales_dev.bronze.orders", "pii")],
                               _provider({}, failing=("sales_dev.bronze.orders",)))
    assert impacts == []
    assert [(f.code, f.severity) for f in findings] == [("abac_unchecked", "info")]
    assert "READ METADATA" in findings[0].message


def test_many_tables_are_checked_through_their_schemas(monkeypatch):
    monkeypatch.setattr(abac, "RELATION_LIMIT", 2)
    items = [_tag(f"sales_dev.bronze.t{i}", "region", "eu") for i in range(3)]
    items.append(_tag("sales_dev.silver.t9", "region", "eu"))
    provider = _provider({"sales_dev.bronze": [EU_ONLY]})

    findings, impacts = _check(items, provider)

    assert sorted(name for _, name in provider.calls) == ["sales_dev.bronze", "sales_dev.silver"]
    assert len(impacts) == 3 and all("eu_only" in i for i in impacts)
    assert [f.code for f in findings] == ["abac_impact", "abac_schema_level"]
    assert "4 tables or views" in findings[1].message


def test_nothing_is_read_without_tag_items_or_a_provider(monkeypatch):
    provider = _provider({"sales_dev.bronze.orders": [MASK_PII]})
    comment = ChangeItem(fqn_key="sales_dev.bronze.orders", name="sales_dev.bronze.orders", securable_type="table",
                         field="comment", action="set", after="Orders")
    assert _check([comment], provider) == ([], [])
    assert provider.calls == []

    monkeypatch.setattr(settings, "METADATA_ABAC_CHECK", False)
    assert _check([_tag("sales_dev.bronze.orders", "pii", column="email")], None) == ([], [])


def test_a_slow_policy_read_is_reported_as_unchecked():
    release = threading.Event()
    provider = MagicMock()

    def list_policies(securable_type, name, include_inherited=None):
        if name == "main.s.slow":
            release.wait(5)
        return iter([])

    provider.client.policies.list_policies.side_effect = list_policies
    try:
        refs = fetch_abac_references(provider, ["main.s.fast", "main.s.slow"], timeout=0.2)
    finally:
        release.set()
    assert refs.unchecked == ["main.s.slow"]
