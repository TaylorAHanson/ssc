"""UC owner lookup uses information_schema (BROWSE), not tables.get (USE CATALOG)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import RetryableError
from app.providers.databricks import DatabricksProvider


def _provider(rows=None):
    with patch("app.providers.databricks.client._build_workspace_client", return_value=MagicMock()):
        provider = DatabricksProvider(host="https://h", token="t", config={"warehouse_id": "wh"})
    provider.execute_sql = AsyncMock(return_value={"rows": rows or []})
    provider.client.tables.get = MagicMock(side_effect=AssertionError("must not call tables.get"))
    provider.client.catalogs.get = MagicMock(side_effect=AssertionError("must not call catalogs.get"))
    provider.client.schemas.get = MagicMock(side_effect=AssertionError("must not call schemas.get"))
    return provider


def _query(provider, index: int = -1) -> str:
    return provider.execute_sql.call_args_list[index].args[0]


@pytest.mark.asyncio
async def test_table_owner_comes_from_information_schema_not_sdk_get():
    provider = _provider(rows=[
        {"owner": "data-owners@corp.com", "table_type": "METRIC_VIEW"},
        {"tag_name": "access_group", "tag_value": "supply-chain-readers"},
    ])
    # First execute_sql is the owner query; second is get_asset_tags.
    provider.execute_sql = AsyncMock(side_effect=[
        {"rows": [{"owner": "data-owners@corp.com", "table_type": "METRIC_VIEW"}]},
        {"rows": [
            {"tag_name": "access_group", "tag_value": "supply-chain-readers"},
            {"tag_name": "approver_group", "tag_value": "sc-approvers"},
        ]},
    ])

    result = await provider.find_object_owner(
        "table", "enterprise_dev.collection_aibi.supply_demand_metric",
    )

    assert result["found"] is True
    assert result["owner"] == "data-owners@corp.com"
    assert result["object_type"] == "METRIC_VIEW"
    assert result["access_group"] == "supply-chain-readers"
    assert result["approver_group"] == "sc-approvers"
    owner_sql = provider.execute_sql.call_args_list[0].args[0]
    assert "system.information_schema.tables" in owner_sql
    assert "table_catalog" in owner_sql
    assert "supply_demand_metric" in owner_sql
    provider.client.tables.get.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_and_schema_use_information_schema():
    provider = _provider(rows=[{"owner": "alice@corp.com"}])
    cat = await provider.find_object_owner("catalog", "enterprise_dev")
    assert cat["found"] is True
    assert "information_schema.catalogs" in _query(provider, 0)

    provider.execute_sql = AsyncMock(return_value={"rows": [{"owner": "bob@corp.com"}]})
    schema = await provider.find_object_owner("schema", "enterprise_dev.collection_aibi")
    assert schema["found"] is True
    assert "information_schema.schemata" in _query(provider, 0)


@pytest.mark.asyncio
async def test_metric_view_alias_and_case_insensitive_type():
    provider = _provider(rows=[{"owner": "mv-owner", "table_type": "METRIC_VIEW"}])
    result = await provider.find_object_owner(
        "METRIC_VIEW", "Cat.Sch.mv",
    )
    assert result["found"] is True
    sql = _query(provider, 0)
    assert "information_schema.tables" in sql
    assert "LOWER(table_catalog) = 'cat'" in sql


@pytest.mark.asyncio
async def test_missing_object_is_not_a_use_catalog_error():
    provider = _provider(rows=[])
    result = await provider.find_object_owner(
        "table", "enterprise_dev.collection_aibi.missing",
    )
    assert result["found"] is False
    assert "USE CATALOG" not in result["message"]
    assert "BROWSE" in result["message"]


@pytest.mark.asyncio
async def test_quote_in_name_is_escaped():
    provider = _provider(rows=[])
    await provider.find_object_owner("table", "main.sales.x' OR '1'='1")
    sql = _query(provider)
    assert "LOWER(table_name) = 'x'' or ''1''=''1'" in sql
    assert "OR '1'='1'" not in sql


@pytest.mark.asyncio
async def test_warehouse_blip_is_retryable():
    provider = _provider()
    provider.execute_sql = AsyncMock(side_effect=RetryableError("SQL execution timed out"))
    with pytest.raises(RetryableError):
        await provider.find_object_owner("table", "a.b.c")
