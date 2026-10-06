"""Tests for Tag Management API routes."""

from unittest.mock import MagicMock, patch
import pytest

from app.api.v1.tags import (
    TableDesiredTags,
    TagChangeCreate,
    create_tag_change,
    get_tag_change_detail,
    get_tag_manager_mode,
    list_tag_changes,
    preview_tag_change,
)
from app.core.config import settings
from app.models.user import User


@pytest.fixture
def mock_admin_user():
    return User(
        id="admin@example.com",
        email="admin@example.com",
        full_name="Admin User",
        roles=["Platform Admin"],
        is_active=True,
    )


def test_get_tag_mode(mock_admin_user):
    with patch.object(settings, "GOVERNANCE_TAGS_LOCAL_MODE", True):
        res = get_tag_manager_mode(current_user=mock_admin_user)
        assert res.local_mode is True
        assert res.environment == (settings.ENVIRONMENT or "dev")


@pytest.mark.asyncio
async def test_preview_tag_change(mock_admin_user):
    mock_provider = MagicMock()
    # Mock live state statement execution
    mock_provider.client.statement_execution.execute_statement.return_value = MagicMock(
        status=MagicMock(state=MagicMock(value="SUCCEEDED")),
        result=MagicMock(data_array=[["sales", "orders", "TABLE"]]),
    )

    with patch("app.api.v1.tags._get_provider", return_value=mock_provider):
        payload = TagChangeCreate(
            dataset_id="orders_ds",
            dataset_name="Orders Dataset",
            tables=[
                TableDesiredTags(
                    table="main.sales.orders",
                    desired_tags={"dataset": "orders_ds", "data_owner": "sales_team", "reliability_window": "24h", "tier": "gold"},
                )
            ],
        )

        res = await preview_tag_change(payload=payload, current_user=mock_admin_user)
        assert res.valid is True
        assert res.plan["statement_count"] >= 1
        assert res.risk["score"] >= 0
        assert res.risk["band"] in ("low", "medium", "high", "critical")
        assert isinstance(res.lint["findings"], list)


@pytest.mark.asyncio
async def test_create_tag_change_local_mode(db_session, mock_admin_user):
    mock_provider = MagicMock()
    mock_provider.client.statement_execution.execute_statement.return_value = MagicMock(
        status=MagicMock(state=MagicMock(value="SUCCEEDED")),
        result=MagicMock(data_array=[]),
    )

    with patch("app.api.v1.tags._get_provider", return_value=mock_provider), \
         patch.object(settings, "GOVERNANCE_TAGS_LOCAL_MODE", True):

        payload = TagChangeCreate(
            dataset_id="orders_ds",
            dataset_name="Orders Dataset",
            tables=[
                TableDesiredTags(
                    table="main.sales.orders",
                    desired_tags={"dataset": "orders_ds", "data_owner": "sales_team", "reliability_window": "24h", "tier": "gold"},
                )
            ],
        )

        res = create_tag_change(payload=payload, db=db_session, current_user=mock_admin_user)
        assert res.execution_mode == "local"
        assert res.status == "completed"
        assert res.applied_count >= 1

        # Check detail endpoint
        detail = get_tag_change_detail(change_id=res.id, db=db_session, current_user=mock_admin_user)
        assert detail.id == res.id
        assert detail.execution_mode == "local"
        assert detail.plan is not None
        assert detail.risk is not None
        assert detail.outcomes is not None

        # Check list endpoint
        all_changes = list_tag_changes(db=db_session, current_user=mock_admin_user)
        assert any(c.id == res.id for c in all_changes)


def _metadata_provider():
    def execute(statement, **kwargs):
        if "information_schema.tables" in statement:
            rows = [["sales", "orders", "BASE TABLE"]]
        elif "information_schema.columns" in statement and "ORDER BY ordinal_position" in statement:
            rows = [["id", "bigint"], ["email", "string"]]
        elif "information_schema.columns" in statement:
            rows = [["sales", "orders", "email"]]
        elif "information_schema.column_tags" in statement and statement.startswith("SELECT column_name"):
            rows = [["email", "pii", "true"]]
        elif "information_schema.column_tags" in statement:
            rows = [["sales", "orders", "email", "pii", "true"]]
        else:
            rows = []
        return MagicMock(result=MagicMock(data_array=rows), status=MagicMock(state=MagicMock(value="SUCCEEDED")))

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = execute
    return provider


def test_table_columns_lists_columns_with_their_tags(mock_admin_user):
    from app.api.v1.tags import get_table_columns

    with patch("app.api.v1.tags._get_provider", return_value=_metadata_provider()):
        res = get_table_columns(table="main.sales.orders", current_user=mock_admin_user)
    assert [(c.column, c.data_type, c.tags) for c in res.columns] == [
        ("id", "bigint", {}),
        ("email", "string", {"pii": "true"}),
    ]


def test_object_tags_rejects_unsafe_names(mock_admin_user):
    from fastapi import HTTPException
    from app.api.v1.tags import ObjectsRequest, get_object_tags

    with pytest.raises(HTTPException) as exc:
        get_object_tags(payload=ObjectsRequest(tables=["main.sales.orders; DROP"]), current_user=mock_admin_user)
    assert exc.value.status_code == 400


def test_object_tags_reads_live_type(mock_admin_user):
    from app.api.v1.tags import ObjectsRequest, get_object_tags

    with patch("app.api.v1.tags._get_provider", return_value=_metadata_provider()):
        res = get_object_tags(payload=ObjectsRequest(tables=["main.sales.orders"]), current_user=mock_admin_user)
    assert res.tables[0].object_type == "TABLE"
    assert res.tables[0].exists is True


def test_object_tags_returns_a_schemas_description(mock_admin_user):
    from app.api.v1.tags import ObjectsRequest, get_object_tags

    def execute(statement, **kwargs):
        rows = {
            "information_schema.schemata": [["main", "sales", "Sales data"]],
            "information_schema.schema_tags": [["main", "sales", "domain", "sales"]],
        }
        found = next((r for k, r in rows.items() if k in statement), [])
        return MagicMock(result=MagicMock(data_array=found))

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = execute
    with patch("app.api.v1.tags._get_provider", return_value=provider):
        res = get_object_tags(payload=ObjectsRequest(tables=["main.sales"]), current_user=mock_admin_user)
    t = res.tables[0]
    assert (t.object_type, t.exists, t.comment, t.tags) == ("SCHEMA", True, "Sales data", {"domain": "sales"})


def test_columns_are_refused_for_a_schema(mock_admin_user):
    from fastapi import HTTPException
    from app.api.v1.tags import get_table_columns

    with pytest.raises(HTTPException) as exc:
        get_table_columns(table="main.sales", current_user=mock_admin_user)
    assert exc.value.status_code == 400


def test_key_usage_refuses_the_reserved_key(mock_admin_user):
    from fastapi import HTTPException
    from app.api.v1.tags import get_key_usage

    with pytest.raises(HTTPException) as exc:
        get_key_usage(key="system.certification_status", current_user=mock_admin_user)
    assert exc.value.status_code == 400


def test_key_usage_lists_objects_and_columns(mock_admin_user):
    from app.api.v1.tags import get_key_usage

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.return_value = MagicMock(
        result=MagicMock(data_array=[["main", "sales", None, None], ["main", "sales", "orders", "email"]])
    )
    with patch("app.api.v1.tags._get_provider", return_value=provider):
        res = get_key_usage(key=" domains ", current_user=mock_admin_user)
    assert res.key == "domains"
    assert [(o.fqn, o.object_type) for o in res.objects] == [("main.sales", "SCHEMA")]
    assert [(c.table, c.column) for c in res.columns] == [("main.sales.orders", "email")]


@pytest.mark.asyncio
async def test_preview_without_a_dataset_covers_a_column(mock_admin_user):
    with patch("app.api.v1.tags._get_provider", return_value=_metadata_provider()), \
         patch.object(settings, "GOVERNANCE_TAGS_LOCAL_MODE", True), \
         patch("app.services.tag_change.engine.request_agent_review") as review:
        review.return_value = MagicMock(to_dict=lambda: {"available": False})
        payload = TagChangeCreate(
            tables=[TableDesiredTags(table="main.sales.orders", column="email", desired_tags={"pii": "true", "classification": "restricted"})],
        )
        res = await preview_tag_change(payload=payload, current_user=mock_admin_user)
    assert res.valid is True
    assert res.plan["diffs"][0]["label"] == "main.sales.orders.email"
    assert res.plan["statements"] == [
        "ALTER TABLE `main`.`sales`.`orders` ALTER COLUMN `email` SET TAGS ('classification' = 'restricted');"
    ]
