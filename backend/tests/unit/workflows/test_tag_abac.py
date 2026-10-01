"""Tests for ABAC (column mask / row filter) impact detection."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.workflows.tag_abac import abac_impacts, fetch_abac_references, parse_tag_conditions
from app.workflows.tag_plan import ObjectState, build_tag_plan


def _policy(name, policy_type, match=(), when=None, on="main.s"):
    return SimpleNamespace(
        name=name,
        policy_type=f"PolicyType.{policy_type}",
        on_securable_fullname=on,
        match_columns=[SimpleNamespace(alias="c", condition=m) for m in match],
        when_condition=when,
    )


def _provider(policies_by_table, failing=()):
    provider = MagicMock()

    def list_policies(securable_type, name, include_inherited=None):
        if name in failing:
            raise PermissionError("User does not have READ METADATA")
        return iter(policies_by_table.get(name, []))

    provider.client.policies.list_policies.side_effect = list_policies
    return provider


def test_parse_tag_conditions():
    cond = "has_tag('pii') AND has_tag_value('classification', 'restricted') OR HAS_TAG_VALUE ( 'region' , 'eu' )"
    assert parse_tag_conditions(cond) == [("pii", None), ("classification", "restricted"), ("region", "eu")]
    assert parse_tag_conditions(None) == []


def _plan(items, state):
    return build_tag_plan(items, state)


def test_column_tag_used_by_a_column_mask_is_flagged():
    provider = _provider({"main.s.t": [_policy("mask_pii", "POLICY_TYPE_COLUMN_MASK", match=["has_tag('pii')"])]})
    refs = fetch_abac_references(provider, ["main.s.t"])
    plan = _plan(
        [{"table": "main.s.t", "column": "email", "desired_tags": {"pii": "true"}},
         {"table": "main.s.t", "desired_tags": {"pii": "true"}}],  # table-level: masks match columns, not tables
        {"main.s.t::email": ObjectState(display="x", exists=True, column="email"),
         "main.s.t": ObjectState(display="main.s.t", exists=True)},
    )
    assert abac_impacts(plan, refs) == ["`main.s.t.email`.`pii`: column mask 'mask_pii' (on main.s)"]


def test_value_specific_conditions_only_match_that_value_before_or_after():
    provider = _provider({"main.s.t": [_policy("eu_only", "POLICY_TYPE_ROW_FILTER", when="has_tag_value('region', 'eu')")]})
    refs = fetch_abac_references(provider, ["main.s.t"])
    state = {"main.s.t": ObjectState(display="main.s.t", exists=True, tags={"region": "us"})}
    unrelated = _plan([{"table": "main.s.t", "desired_tags": {"region": "apac"}}], state)
    assert abac_impacts(unrelated, refs) == []
    into_eu = _plan([{"table": "main.s.t", "desired_tags": {"region": "eu"}}], state)
    assert abac_impacts(into_eu, refs) == ["`main.s.t`.`region`: row filter 'eu_only' (on main.s)"]


def test_unreadable_policies_are_reported_as_unchecked():
    refs = fetch_abac_references(_provider({}, failing=("main.s.t",)), ["main.s.t", "main.s.u"])
    assert refs.unchecked == ["main.s.t"]
    assert refs.by_table == {}


def test_abac_impact_raises_the_risk_score():
    from app.workflows.tag_risk import calculate_risk_score

    plan = _plan([{"table": "main.s.t", "desired_tags": {"region": "eu"}}],
                 {"main.s.t": ObjectState(display="main.s.t", exists=True)})
    report = calculate_risk_score(plan=plan, environment="prod", findings=[],
                                  abac_impacts=["`main.s.t`.`region`: row filter 'eu_only'"])
    factor = next(f for f in report.factors if f.name == "abac_impact")
    assert factor.count == 1 and factor.contribution == 25.0
