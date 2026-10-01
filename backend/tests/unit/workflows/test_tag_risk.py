"""Tests for Deterministic Tag Risk Scoring."""

from app.workflows.tag_lint import LintFinding
from app.workflows.tag_plan import ObjectState, build_tag_plan
from app.workflows.tag_policy import TagPolicy
from app.workflows.tag_risk import calculate_risk_score


def test_low_risk_simple_add():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders"},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {"dataset": "orders", "notes": "weekly snapshot"},
        }
    ]
    plan = build_tag_plan(desired, live_state)
    report = calculate_risk_score(plan=plan, environment="dev", findings=[], vocabulary={})

    assert report.band == "low"
    assert report.score < 30
    assert report.environment == "dev"


def test_risk_score_with_access_control_and_removals_in_prod():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={
                "dataset": "orders",
                "access_group": "sales_restricted",
                "approver_group": "sales_leads",
                "certified_status": "gold",
            },
        )
    }
    # Modifying access_group, unsetting approver_group and certified_status
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {
                "dataset": "orders",
                "access_group": "public_sales",
            },
        }
    ]
    plan = build_tag_plan(desired, live_state)
    report = calculate_risk_score(plan=plan, environment="prod", findings=[], vocabulary={})

    assert report.multiplier == 1.25  # Prod multiplier
    assert report.score > 30

    factor_names = [f.name for f in report.factors if f.count > 0]
    assert "access_control_change" in factor_names
    assert "removal" in factor_names
    assert "overwrite" in factor_names


def test_lowering_a_column_classification_is_scored_as_a_downgrade():
    live_state = {
        "main.sales.orders::email": ObjectState(
            display="main.sales.orders.email", exists=True, column="email",
            tags={"classification": "restricted", "pii": "true"},
        ),
    }
    desired = [{"table": "main.sales.orders", "column": "email", "desired_tags": {"classification": "internal"}}]
    plan = build_tag_plan(desired, live_state)
    report = calculate_risk_score(plan=plan, environment="dev", findings=[])

    factor = next(f for f in report.factors if f.name == "sensitivity_downgrade")
    assert factor.count == 2
    assert any("restricted` → `internal" in d for d in factor.details)
    assert any("PII flag removed" in d for d in factor.details)


def test_raising_a_classification_is_not_a_downgrade():
    live_state = {
        "main.sales.orders": ObjectState(display="main.sales.orders", exists=True, tags={"classification": "internal"}),
    }
    desired = [{"table": "main.sales.orders", "desired_tags": {"classification": "restricted"}}]
    report = calculate_risk_score(plan=build_tag_plan(desired, live_state), environment="dev", findings=[])
    assert next(f for f in report.factors if f.name == "sensitivity_downgrade").count == 0


def test_certified_tables_are_detected_from_system_tags():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders", exists=True, all_tags={"system.certification_status": "certified"},
        ),
    }
    plan = build_tag_plan([{"table": "main.sales.orders", "desired_tags": {"tier": "gold"}}], live_state)
    report = calculate_risk_score(plan=plan, environment="dev", findings=[])
    assert next(f for f in report.factors if f.name == "certified_object").count == 1
