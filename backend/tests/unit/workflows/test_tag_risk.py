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
