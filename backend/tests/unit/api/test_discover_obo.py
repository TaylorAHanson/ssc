"""Discover's table-details and lineage routes read Unity Catalog as the user.

They used to build a client from the app's service principal, so any signed-in
user saw columns and lineage for tables only the SP could access.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.v1.data_assets import get_databricks_table_details, get_databricks_table_lineage


def _req(token="user-token"):
    return SimpleNamespace(state=SimpleNamespace(token=token))


@pytest.fixture
def user_client():
    client = MagicMock()
    with patch("app.core.workspaces.uc_client_for", return_value=(MagicMock(), client)) as uc, \
            patch("app.providers.databricks.DatabricksProvider",
                  side_effect=AssertionError("must not build an app-SP client")):
        yield client, uc


def test_lineage_runs_as_the_user_with_encoded_query(user_client):
    client, uc = user_client
    client.api_client.do.return_value = {
        "upstreams": [{"tableInfo": {"name": "main.raw.orders", "catalog_name": "main"}}],
        "downstreams": [],
    }

    out = get_databricks_table_lineage("main.sales.orders", _req("tok-1"))

    uc.assert_called_once_with("tok-1")
    client.api_client.do.assert_called_once_with(
        "GET", "/api/2.0/lineage-tracking/table-lineage",
        query={"table_name": "main.sales.orders", "include_entity_lineage": "true"},
    )
    assert [u["name"] for u in out["upstreams"]] == ["main.raw.orders"]
    assert out["error"] is None


def test_lineage_permission_error_is_the_users_own(user_client):
    client, _ = user_client
    client.api_client.do.side_effect = RuntimeError("User does not have SELECT on Table 'main.hr.salaries'")

    out = get_databricks_table_lineage("main.hr.salaries", _req())

    assert out["error_kind"] == "permission_denied"
    assert out["upstreams"] == [] and out["downstreams"] == []


def test_table_details_run_as_the_user(user_client):
    client, uc = user_client
    client.tables.get.return_value = SimpleNamespace(
        columns=[SimpleNamespace(name="id", type_text="bigint", comment=None, nullable=False, position=0)],
        table_type=SimpleNamespace(value="MANAGED"), comment="c", data_source_format=None,
        owner="o", created_at=1, updated_at=2,
    )
    client.entity_tag_assignments.list.return_value = [SimpleNamespace(tag_key="tier", tag_value="gold")]

    out = get_databricks_table_details("main.sales.orders", _req("tok-2"))

    uc.assert_called_once_with("tok-2")
    client.tables.get.assert_called_once_with(full_name="main.sales.orders")
    assert out["columns"][0]["name"] == "id" and out["tags"] == {"tier": "gold"}
    assert out["error"] is None


def test_table_details_permission_error_is_the_users_own(user_client):
    client, _ = user_client
    client.tables.get.side_effect = RuntimeError("User does not have USE SCHEMA on Schema 'main.hr'")

    out = get_databricks_table_details("main.hr.salaries", _req())

    assert out["error_kind"] == "permission_denied"
    assert out["columns"] == []


def test_no_user_token_on_a_deployed_target_never_falls_back_to_the_sp():
    uc_provider = MagicMock()
    with patch("app.core.workspaces.get_uc_provider", return_value=uc_provider), \
            patch("app.providers.databricks_mcp.sp_fallback_allowed", return_value=False):
        lineage = get_databricks_table_lineage("main.sales.orders", _req(token=None))
        details = get_databricks_table_details("main.sales.orders", _req(token=None))

    assert "signed-in user" in lineage["error"] and lineage["upstreams"] == []
    assert "signed-in user" in details["error"] and details["columns"] == []
    uc_provider.client.api_client.do.assert_not_called()
    uc_provider.client.tables.get.assert_not_called()


def test_invalid_table_name_is_rejected_before_any_call(user_client):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        get_databricks_table_lineage("not-qualified", _req())
    user_client[1].assert_not_called()
