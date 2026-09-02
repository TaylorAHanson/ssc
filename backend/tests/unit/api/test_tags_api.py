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

        res = await create_tag_change(payload=payload, db=db_session, current_user=mock_admin_user)
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
