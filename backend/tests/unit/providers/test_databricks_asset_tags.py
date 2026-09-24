"""get_asset_tags escapes every interpolated value.

The asset name and tag names can come from the agent or a request form, and the
query runs as the service principal to pick the approver group, so a quote in
either must not be able to change the query.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers.databricks import DatabricksProvider


def _provider(rows=None):
    with patch("app.providers.databricks.client._build_workspace_client", return_value=MagicMock()):
        provider = DatabricksProvider(host="https://h", token="t", config={"warehouse_id": "wh"})
    provider.execute_sql = AsyncMock(return_value={"rows": rows or []})
    return provider


def _query(provider) -> str:
    return provider.execute_sql.call_args.args[0]


def test_normal_table_name_builds_expected_predicate():
    provider = _provider(rows=[{"tag_name": "approver_group", "tag_value": "finance"}])

    tags = asyncio.run(provider.get_asset_tags("table", "main.sales.orders", ["approver_group"]))

    assert tags == {"approver_group": "finance"}
    query = _query(provider)
    assert "FROM system.information_schema.table_tags" in query
    assert "catalog_name = 'main' AND schema_name = 'sales' AND table_name = 'orders'" in query
    assert "tag_name IN ('approver_group')" in query


def test_quote_in_asset_name_cannot_break_out_of_the_literal():
    provider = _provider()

    asyncio.run(provider.get_asset_tags(
        "table", "main.sales.x' OR '1'='1", ["approver_group"],
    ))

    query = _query(provider)
    assert "table_name = 'x'' OR ''1''=''1'" in query
    assert "OR '1'='1'" not in query


def test_quote_in_tag_names_is_escaped():
    provider = _provider()

    asyncio.run(provider.get_asset_tags("catalog", "main", ["a', (SELECT 1) --"]))

    assert "tag_name IN ('a'', (SELECT 1) --')" in _query(provider)


def test_each_asset_type_uses_its_own_tags_view():
    cases = {
        "catalog": ("main", "catalog_tags"),
        "schema": ("main.sales", "schema_tags"),
        "view": ("main.sales.v", "table_tags"),
        "volume": ("main.sales.vol", "volume_tags"),
    }
    for asset_type, (name, view) in cases.items():
        provider = _provider()
        asyncio.run(provider.get_asset_tags(asset_type, name, ["t"]))
        assert f"information_schema.{view}" in _query(provider), asset_type
