"""Tests for the tag-change engine and the Tag Management quick search."""

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.services.tag_change import engine, search
from app.workflows.tag_policy import get_default_policy
from app.workflows.tag_sql import build_tag_sql


def _provider(rows_for):
    """A provider whose metadata queries answer from ``rows_for(statement)``."""
    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = (
        lambda statement, **kw: MagicMock(result=MagicMock(data_array=rows_for(statement)))
    )
    return provider


def _uc(statement):
    if "information_schema.tables" in statement:
        return [["sales", "orders", "BASE TABLE"]]
    if "information_schema.columns" in statement:
        return [["sales", "orders", "email"]]
    return []


# --- engine -----------------------------------------------------------------------

def test_gitops_mode_blocks_column_changes_with_a_clear_reason():
    targets = [engine.TagTarget(table="main.sales.orders", column="email", desired_tags={"pii": "true"})]
    evaluation = engine.evaluate(_provider(_uc), get_default_policy(), targets, local_mode=False)
    assert engine.GITOPS_COLUMNS_UNSUPPORTED in evaluation.violations
    assert evaluation.changes == [{"table": "main.sales.orders", "column": "email", "set": {"pii": "true"}, "unset": []}]


def test_local_mode_allows_column_changes():
    targets = [engine.TagTarget(table="main.sales.orders", column="email", desired_tags={"pii": "true"})]
    evaluation = engine.evaluate(_provider(_uc), get_default_policy(), targets, local_mode=True)
    assert evaluation.valid
    assert evaluation.plan.statements == [
        "ALTER TABLE `main`.`sales`.`orders` ALTER COLUMN `email` SET TAGS ('pii' = 'true');"
    ]


def test_gitops_preview_shows_the_statements_that_will_be_committed():
    targets = [engine.TagTarget(table="main.sales.orders", desired_tags={"tier": "gold"})]
    evaluation = engine.evaluate(_provider(_uc), get_default_policy(), targets, local_mode=False)
    assert evaluation.plan_dict()["statements"] == ["ALTER TABLE main.sales.orders SET TAGS ('tier' = 'gold');"]


def test_unsafe_names_never_reach_sql():
    targets = [engine.TagTarget(table="main.sales.orders;DROP", desired_tags={"a": "b"})]
    with pytest.raises(engine.TagChangeError):
        engine.evaluate(_provider(_uc), get_default_policy(), targets, local_mode=True)


def test_target_limit():
    targets = [engine.TagTarget(table=f"main.s.t{i}", desired_tags={}) for i in range(engine.MAX_TARGETS + 1)]
    with pytest.raises(engine.TagChangeError):
        engine.validate_targets(targets)


def test_gitops_sql_refuses_column_changes():
    with pytest.raises(ValueError):
        build_tag_sql([{"table": "main.s.t", "column": "c", "set": {"a": "b"}, "unset": []}])


def test_scope_label_summarises_ad_hoc_changes():
    one = [engine.TagTarget(table="main.s.a", desired_tags={})]
    many = one + [engine.TagTarget(table="main.s.b", desired_tags={}), engine.TagTarget(table="main.s.a", column="c", desired_tags={})]
    assert engine.scope_label(one) == "main.s.a"
    assert engine.scope_label(many) == "main.s.a + 1 more"
    assert engine.scope_label(many, dataset_id="orders") == "orders"


def test_ad_hoc_local_submit_records_a_request(db_session):
    targets = [engine.TagTarget(table="main.sales.orders", column="email", desired_tags={"pii": "true"})]
    provider = _provider(_uc)
    evaluation = engine.evaluate(provider, get_default_policy(), targets, local_mode=True)
    submitted = engine.submit(
        db_session, provider, evaluation, scope=engine.scope_label(targets), dataset_id=None,
        actor_email="admin@example.com",
    )
    assert submitted.execution_mode == "local"
    assert submitted.request.title == "Tag change: main.sales.orders (Local)"
    ctx = submitted.request.state_context
    assert ctx["dataset_id"] is None
    assert ctx["changes"][0]["column"] == "email"
    assert ctx["apply_result"]["outcomes"][0]["column"] == "email"


# --- search -----------------------------------------------------------------------

def test_parse_query():
    parsed = search.parse_query("Orders main.sales.* classification=conf* data_owner=* !approver_group")
    assert parsed.terms == ["orders", "main.sales.*"]
    assert [(f.key, f.value, f.negate) for f in parsed.tag_filters] == [
        ("classification", "conf*", False),
        ("data_owner", None, False),
        ("approver_group", None, True),
    ]


@pytest.fixture
def cached_assets(db_session):
    for fqn, typ, tags in [
        ("main.sales.orders", "MANAGED", ["dataset", "data_owner"]),
        ("main.sales.order_items", "MANAGED", []),
        ("main.sales.orders_v", "VIEW", []),
        ("main.finance.ledger", "MANAGED", []),
        ("main.information_schema.tables", "VIEW", []),
        ("main.sales.product", "DATA_PRODUCT", []),
    ]:
        c, s, t = fqn.split(".")
        db_session.add(DataAssetModel(id=fqn, catalog=c, schema=s, table_name=t, type=typ, tags=tags))
    db_session.flush()
    return db_session


def test_name_search_uses_the_cache_and_skips_untaggable_entries(cached_assets):
    result = search.search(cached_assets, None, "sales")
    assert [o.fqn for o in result.objects] == ["main.sales.order_items", "main.sales.orders", "main.sales.orders_v"]
    assert result.objects[1].tag_keys == ["data_owner", "dataset"]


def test_words_must_all_match_and_underscores_are_literal(cached_assets):
    assert [o.fqn for o in search.search(cached_assets, None, "order_ sales").objects] == ["main.sales.order_items"]


def test_glob_matches_a_whole_schema(cached_assets):
    assert len(search.search(cached_assets, None, "main.finance.*").objects) == 1


def test_missing_tag_filter_excludes_tagged_tables(cached_assets):
    provider = _provider(lambda s: [["main", "sales", "orders", "team-a"]] if "data_owner" in s else [])
    result = search.search(cached_assets, provider, "sales !data_owner")
    assert "main.sales.orders" not in [o.fqn for o in result.objects]
    assert result.filters == ["missing 'data_owner'"]


def test_tag_value_filter_includes_tables_not_yet_in_the_cache(cached_assets):
    rows = [["main", "sales", "orders", "Restricted"], ["main", "new", "fresh", "restricted"], ["main", "x", "y", "public"]]
    provider = _provider(lambda s: rows if "classification" in s else [])
    result = search.search(cached_assets, provider, "classification=restricted")
    assert [(o.fqn, o.in_cache) for o in result.objects] == [("main.new.fresh", False), ("main.sales.orders", True)]
    assert result.datasets == []


def test_pasted_full_name_is_offered_even_if_not_cached(cached_assets):
    result = search.search(cached_assets, None, "main.brand.New_Table")
    assert [(o.fqn, o.in_cache) for o in result.objects] == [("main.brand.New_Table", False)]


def test_empty_query_returns_nothing(cached_assets):
    assert search.search(cached_assets, None, "   ").objects == []


def test_cached_tag_names_stored_as_a_json_string_are_decoded(db_session):
    db_session.add(DataAssetModel(id="main.s.t", catalog="main", schema="s", table_name="t", type="MANAGED",
                                  tags='["data_set", "domain"]'))
    db_session.flush()
    assert search.search(db_session, None, "main.s.t").objects[0].tag_keys == ["data_set", "domain"]


# --- Unity Catalog governed tags (tag policies) -----------------------------------

def _with_tag_policies(provider, policies, failing=()):
    from types import SimpleNamespace
    from databricks.sdk.errors import NotFound

    def get_tag_policy(key):
        if key in failing:
            raise RuntimeError("permission denied")
        if key not in policies:
            raise NotFound(f"Tag policy with name {key} not found")
        return SimpleNamespace(values=[SimpleNamespace(name=v) for v in policies[key]], description="")

    provider.client.tag_policies.get_tag_policy.side_effect = get_tag_policy
    return provider


def test_values_outside_a_governed_tags_allowed_list_are_blocked_before_apply():
    provider = _with_tag_policies(_provider(_uc), {"test": ["123", "456"]})
    targets = [engine.TagTarget(table="main.sales.orders", desired_tags={"test": "test", "free": "x"})]
    evaluation = engine.evaluate(provider, get_default_policy(), targets, local_mode=True)
    assert evaluation.violations == [
        "main.sales.orders: 'test' isn't an allowed value for the governed tag 'test'. Allowed values: 123, 456."
    ]


def test_allowed_governed_values_and_ungoverned_keys_pass():
    provider = _with_tag_policies(_provider(_uc), {"test": ["123", "456"], "pii": []})
    targets = [engine.TagTarget(table="main.sales.orders", column="email", desired_tags={"test": "456", "pii": "anything"})]
    assert engine.evaluate(provider, get_default_policy(), targets, local_mode=True).valid


def test_unreadable_tag_policy_is_a_warning_not_a_block():
    provider = _with_tag_policies(_provider(_uc), {}, failing=("test",))
    targets = [engine.TagTarget(table="main.sales.orders", desired_tags={"test": "x"})]
    evaluation = engine.evaluate(provider, get_default_policy(), targets, local_mode=True)
    assert evaluation.valid
    assert "'test'" in evaluation.warnings[0]


def test_abac_impacts_and_assign_reminder_reach_the_preview_warnings():
    from types import SimpleNamespace
    provider = _with_tag_policies(_provider(_uc), {"pii": []})
    provider.client.policies.list_policies.side_effect = lambda *a, **k: iter([SimpleNamespace(
        name="mask_pii", policy_type="POLICY_TYPE_COLUMN_MASK", on_securable_fullname="main.sales",
        match_columns=[SimpleNamespace(alias="c", condition="has_tag('pii')")], when_condition=None)])
    targets = [engine.TagTarget(table="main.sales.orders", column="email", desired_tags={"pii": "true"})]
    evaluation = engine.evaluate(provider, get_default_policy(), targets, local_mode=True)
    assert any("ASSIGN" in w and "'pii'" in w for w in evaluation.warnings)
    assert any("mask_pii" in w for w in evaluation.warnings)
    assert any(f.name == "abac_impact" and f.count == 1 for f in evaluation.risk.factors)


def test_governed_key_search_puts_prefix_matches_first_and_caches(monkeypatch):
    from types import SimpleNamespace
    from app.workflows import tag_governed

    monkeypatch.setattr(tag_governed, "_keys_cache", {"at": 0.0, "keys": None})
    provider = MagicMock()
    provider.client.tag_policies.list_tag_policies.return_value = [
        SimpleNamespace(tag_key=k, values=[], description="") for k in ["customer_pii", "pii", "pii_level", "region"]
    ]
    assert [t.key for t in tag_governed.search_governed_keys(provider, "PII")] == ["pii", "pii_level", "customer_pii"]
    assert tag_governed.search_governed_keys(provider, "") == []
    tag_governed.search_governed_keys(provider, "reg")
    assert provider.client.tag_policies.list_tag_policies.call_count == 1


def test_reserved_system_tags_are_refused_not_silently_dropped():
    targets = [engine.TagTarget(table="main.sales.orders", desired_tags={"system.certification_status": "certified"})]
    evaluation = engine.evaluate(_provider(_uc), get_default_policy(), targets, local_mode=True)
    assert not evaluation.valid
    assert "'system.certification_status' is a reserved tag" in evaluation.violations[0]
    assert evaluation.plan.statements == []
