"""What the agent can learn about one table without a new tool.

``search_data_assets`` answers from the local catalog cache (description, owner, quality, SLA);
``get_table_list`` answers live, as the user, and returns the columns for one exact table.
"""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.data_asset import DataAssetModel
from app.tools.self_service.get_table_list import MAX_COLUMNS, get_table_list
from app.tools.self_service.search_data_assets import search_data_assets

HOST = "https://test.azuredatabricks.net"
# 2026-10-20 09:30:00 UTC
UPDATED_MS = int(datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc).timestamp() * 1000)


def _column(name, type_text="string", comment=None):
    return NS(name=name, type_text=type_text, type_name=NS(value=type_text.upper()), comment=comment)


def _table(name, columns=(), **extra):
    return NS(
        name=name, comment=f"About {name}", table_type=NS(value="MANAGED"), catalog_name="main",
        schema_name="sales", owner="owner@example.com", properties={}, columns=list(columns),
        created_at=UPDATED_MS - 86_400_000, updated_at=UPDATED_MS, **extra)


@pytest.fixture
def provider():
    p = MagicMock()
    p.execute_sql = AsyncMock(return_value={"rows": []})
    with patch("app.core.workspaces.uc_client_for", return_value=(p, p.client)):
        yield p


@pytest.mark.asyncio
async def test_an_exact_table_name_returns_its_columns_and_timestamps(provider):
    provider.client.tables.list.return_value = [
        _table("product", [_column("sku", comment="Stock keeping unit"), _column("price", "decimal(10,2)")]),
        _table("shipments", [_column("id")]),
    ]
    out = await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                       name_pattern="product")
    assert out["count"] == 1
    t = out["tables"][0]
    assert t["columns"] == [
        {"name": "sku", "type": "string", "comment": "Stock keeping unit"},
        {"name": "price", "type": "decimal(10,2)", "comment": None},
    ]
    assert t["column_count"] == 2 and "columns_truncated" not in t
    assert t["updated_at"] == "2026-10-20T09:30:00+00:00"
    assert t["created_at"] == "2026-10-19T09:30:00+00:00"


@pytest.mark.asyncio
async def test_a_wide_listing_leaves_columns_out_unless_asked(provider):
    provider.client.tables.list.return_value = [_table("product", [_column("sku")]), _table("shipments", [_column("id")])]

    for pattern in (None, "prod*", "pro?uct"):
        out = await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                           name_pattern=pattern)
        assert all("columns" not in t for t in out["tables"]), pattern
        assert all(t["updated_at"] for t in out["tables"])

    out = await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                       include_columns=True)
    assert all(len(t["columns"]) == 1 for t in out["tables"])

    out = await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                       name_pattern="product", include_columns=False)
    assert "columns" not in out["tables"][0]


@pytest.mark.asyncio
async def test_a_very_wide_table_is_cut_and_says_so(provider):
    provider.client.tables.list.return_value = [
        _table("wide", [_column(f"c{i}") for i in range(MAX_COLUMNS + 20)])]
    t = (await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                      name_pattern="wide"))["tables"][0]
    assert len(t["columns"]) == MAX_COLUMNS
    assert t["column_count"] == MAX_COLUMNS + 20 and t["columns_truncated"] is True


@pytest.mark.asyncio
async def test_missing_columns_and_timestamps_are_tolerated(provider):
    bare = NS(name="t", comment=None, table_type=NS(value="VIEW"), catalog_name="main", schema_name="sales",
              owner=None, properties=None, columns=None, created_at=None, updated_at=None)
    provider.client.tables.list.return_value = [bare]
    t = (await get_table_list.execute(target_host=HOST, catalog_name="main", schema_name="sales",
                                      name_pattern="t"))["tables"][0]
    assert t["columns"] == [] and t["column_count"] == 0
    assert t["updated_at"] is None and t["created_at"] is None


def test_search_returns_the_cached_quality_sla_and_sync_time(db_session):
    db = db_session
    db.add(DataAssetModel(
        id="main.sales.product", catalog="main", schema="sales", table_name="product", type="MANAGED",
        description="Product master", owner="owner@example.com", sla="Refreshed daily by 6am",
        data_quality={"failed_rule_count": 8, "failed_rules": [f"r{i}" for i in range(8)]},
        last_synced_at=datetime(2026, 10, 27, 6, 0)))
    db.add(DataAssetModel(
        id="main.sales.plain", catalog="main", schema="sales", table_name="plain", type="MANAGED",
        description="Product extras", last_synced_at=datetime(2026, 10, 27, 6, 0)))
    db.flush()
    with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([db])), \
            patch.object(db, "close"):
        res = search_data_assets._func(query="product", limit=10)
    by_id = {a["id"]: a for a in res["assets"]}

    product = by_id["main.sales.product"]
    assert product["sla"] == "Refreshed daily by 6am"
    assert product["data_quality"]["failed_rule_count"] == 8
    assert product["data_quality"]["failed_rules"] == ["r0", "r1", "r2", "r3", "r4"]
    assert product["data_quality"]["failed_rules_total"] == 8
    assert product["catalog_synced_at"] == "2026-10-27T06:00:00"

    plain = by_id["main.sales.plain"]
    assert plain["sla"] is None and plain["data_quality"] is None


def _search(db, **kwargs):
    with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([db])), \
            patch.object(db, "close"):
        return search_data_assets._func(**kwargs)


def test_the_exact_table_named_in_the_query_is_found_in_a_large_catalog(db_session):
    # Many assets match some of the words; the one asked for is added last, so an
    # unordered cut-off of the candidates would have dropped it before ranking.
    for i in range(250):
        db_session.add(DataAssetModel(
            id=f"other_{i}.supply_gold.dim_product", catalog=f"other_{i}", schema="supply_gold",
            table_name="dim_product", type="MANAGED", description="Product dimension"))
    db_session.add(DataAssetModel(
        id="solution_builder.supply_gold.dim_product", catalog="solution_builder", schema="supply_gold",
        table_name="dim_product", type="MANAGED", owner="owner@example.com"))
    db_session.flush()

    res = _search(db_session, query="solution_builder.supply_gold.dim_product", limit=5)
    assert res["assets"][0]["id"] == "solution_builder.supply_gold.dim_product"
    assert res["total_matched"] >= 251  # the true count, not the size of the ranked slice

    # Backticks and capital letters still name the same table.
    res = _search(db_session, query="`Solution_Builder`.`Supply_Gold`.`Dim_Product`", limit=1)
    assert res["assets"][0]["id"] == "solution_builder.supply_gold.dim_product"


def test_words_that_name_a_table_rank_it_above_arbitrary_partial_matches(db_session):
    for i in range(250):
        db_session.add(DataAssetModel(
            id=f"c{i}.s.t{i}", catalog=f"c{i}", schema="s", table_name=f"t{i}", type="MANAGED",
            description="Mentions sales once"))
    db_session.add(DataAssetModel(
        id="main.fin.sales_orders", catalog="main", schema="fin", table_name="sales_orders", type="MANAGED"))
    db_session.flush()
    res = _search(db_session, query="sales orders", limit=3)
    assert res["assets"][0]["id"] == "main.fin.sales_orders"
