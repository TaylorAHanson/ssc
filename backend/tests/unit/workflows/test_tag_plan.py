"""Tests for the TagPlan and diff planning engine."""

from app.workflows.tag_plan import (
    ObjectState,
    build_tag_plan,
)


def test_plan_with_no_changes():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "env": "prod"},
        )
    }
    desired = [{"table": "main.sales.orders", "desired_tags": {"dataset": "orders_ds", "env": "prod"}}]

    plan = build_tag_plan(desired, live_state)
    assert not plan.actionable
    assert plan.statement_count == 0
    assert len(plan.diffs) == 1
    diff = plan.diffs["main.sales.orders"]
    assert diff.changed_keys == []
    assert diff.removed_keys == []
    assert diff.overwritten_keys == []


def test_plan_with_set_and_unset():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "old_key": "old_val", "env": "dev"},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {"dataset": "orders_ds", "env": "prod", "new_key": "new_val"},
        }
    ]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    assert plan.statement_count == 2
    diff = plan.diffs["main.sales.orders"]
    assert "old_key" in diff.removed_keys
    assert "new_key" in diff.changed_keys
    assert "env" in diff.overwritten_keys

    # Check generated statements
    stmts = plan.statements
    assert any("SET TAGS" in s and "'new_key' = 'new_val'" in s for s in stmts)
    assert any("UNSET TAGS ('old_key')" in s for s in stmts)


def test_plan_with_view_object_type():
    live_state = {
        "main.sales.orders_v": ObjectState(
            display="main.sales.orders_v",
            object_type="VIEW",
            exists=True,
            tags={},
        )
    }
    desired = [{"table": "main.sales.orders_v", "desired_tags": {"tier": "gold"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    assert len(plan.statements) == 1
    assert "ALTER VIEW main.sales.orders_v SET TAGS" in plan.statements[0]


def test_plan_with_missing_object():
    live_state = {
        "main.sales.missing": ObjectState(
            display="main.sales.missing",
            object_type="TABLE",
            exists=False,
            tags={},
        )
    }
    desired = [{"table": "main.sales.missing", "desired_tags": {"tier": "gold"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.missing_objects == ["main.sales.missing"]
    assert plan.statement_count == 1


def test_plan_preserves_dataset_tag_when_omitted():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "old_key": "old_val"},
        )
    }
    # Desired tags omit 'dataset', but provide a new tag
    desired = [{"table": "main.sales.orders", "desired_tags": {"owner": "analytics"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    diff = plan.diffs["main.sales.orders"]
    # 'dataset' should be preserved in diff.after and NOT in removed_keys
    assert diff.after.get("dataset") == "orders_ds"
    assert "dataset" not in diff.removed_keys
    assert "old_key" in diff.removed_keys

    # Generated statements must UNSET 'old_key' but NEVER 'dataset'
    stmts = plan.statements
    assert any("UNSET TAGS ('old_key')" in s for s in stmts)
    assert not any("UNSET TAGS" in s and "'dataset'" in s for s in stmts)

