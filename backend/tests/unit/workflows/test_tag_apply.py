"""Tests for the Tag Apply Engine (direct Unity Catalog SQL execution)."""

from unittest.mock import MagicMock

from app.workflows.tag_apply import apply_tag_plan
from app.workflows.tag_plan import ObjectState, build_tag_plan


def test_apply_success():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={},
        )
    }
    desired = [{"table": "main.sales.orders", "desired_tags": {"tier": "gold"}}]
    plan = build_tag_plan(desired, live_state)

    mock_provider = MagicMock()
    mock_provider.client.statement_execution.execute_statement.return_value = MagicMock(status=MagicMock(state=MagicMock(value="SUCCEEDED")))

    result = apply_tag_plan(
        provider=mock_provider,
        plan=plan,
        request_id="req-123",
        actor="test@example.com",
        environment="dev",
    )

    assert result.status == "applied"
    assert result.applied_count == 1
    assert result.failed_count == 0
    assert len(result.outcomes) == 1
    assert result.outcomes[0].status == "applied"


def test_apply_ignores_missing_tag_error_on_unset():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"old_tag": "val"},
        )
    }
    desired = [{"table": "main.sales.orders", "desired_tags": {}}]
    plan = build_tag_plan(desired, live_state)

    mock_provider = MagicMock()
    # Mock statement execution raising exception that tag was already removed / does not exist
    mock_provider.client.statement_execution.execute_statement.side_effect = Exception(
        "TAG_NOT_FOUND: Tag 'old_tag' does not exist on table"
    )

    result = apply_tag_plan(
        provider=mock_provider,
        plan=plan,
        request_id="req-123",
        actor="test@example.com",
        environment="dev",
    )

    # Missing tag on UNSET is treated as benign NOOP
    assert result.status == "noop"
    assert result.noop_count == 1
    assert result.failed_count == 0
    assert result.outcomes[0].status == "noop"


def test_apply_handles_view_vs_table_mismatch_retry():
    live_state = {
        "main.sales.orders_v": ObjectState(
            display="main.sales.orders_v",
            object_type="TABLE",  # Cached as table
            exists=True,
            tags={},
        )
    }
    desired = [{"table": "main.sales.orders_v", "desired_tags": {"tier": "gold"}}]
    plan = build_tag_plan(desired, live_state)

    mock_provider = MagicMock()
    # First call fails because it's actually a VIEW
    # Second retry with ALTER VIEW succeeds
    def execute_side_effect(statement, **kwargs):
        if "ALTER TABLE" in statement:
            raise Exception("TABLE_OR_VIEW_NOT_MATCH: main.sales.orders_v is a VIEW, not a TABLE")
        return MagicMock(status=MagicMock(state=MagicMock(value="SUCCEEDED")))

    mock_provider.client.statement_execution.execute_statement.side_effect = execute_side_effect

    result = apply_tag_plan(
        provider=mock_provider,
        plan=plan,
        request_id="req-123",
        actor="test@example.com",
        environment="dev",
    )

    assert result.status == "applied"
    assert result.applied_count == 1
    assert result.failed_count == 0
    assert "ALTER VIEW" in result.outcomes[0].sql
