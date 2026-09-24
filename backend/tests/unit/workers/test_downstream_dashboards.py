"""Downstream dashboards come from one system.access.table_lineage query per batch.

Replaced per-view calls to the rate-limited Catalog Explorer lineage endpoint
(see docs/METRIC_VIEW_LINEAGE.md).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.workers.tasks import sync_data_assets
from app.workers.tasks.sync_data_assets import _fetch_downstream_dashboards


def _provider(rows=(), dashboards=(), sql_error=None):
    provider = MagicMock()
    provider.client.config.host = "https://ws.example.com/"
    provider.client.lakeview.list.return_value = [
        SimpleNamespace(dashboard_id=i, display_name=n) for i, n in dashboards
    ]
    provider.execute_sql = AsyncMock(
        side_effect=sql_error, return_value=None if sql_error else {"rows": list(rows)},
    )
    return provider


def _run(provider, fqns):
    return asyncio.run(_fetch_downstream_dashboards(provider, fqns))


def test_query_filters_metric_view_dashboard_reads_with_quoted_names(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ASSET_LINEAGE_LOOKBACK_DAYS", 30)
    provider = _provider()

    out = _run(provider, ["main.kpi.revenue", "main.kpi.o'brien"])

    assert out == {"main.kpi.revenue": [], "main.kpi.o'brien": []}
    sql = provider.execute_sql.call_args.args[0]
    assert "FROM system.access.table_lineage" in sql
    assert "source_type = 'METRIC_VIEW'" in sql and "entity_type = 'DASHBOARD_V3'" in sql
    assert "INTERVAL 30 DAYS" in sql
    assert "IN ('main.kpi.revenue', 'main.kpi.o''brien')" in sql
    provider.client.lakeview.list.assert_not_called()  # nothing to resolve


def test_resolves_names_links_and_labels_other_workspace_dashboards():
    rows = [
        {"source_table_full_name": "main.kpi.revenue", "entity_id": "d1", "workspace_id": "111", "last_read": "2026-09-01T00:00:00"},
        {"source_table_full_name": "main.kpi.revenue", "entity_id": "d2", "workspace_id": "222", "last_read": "2026-09-20T00:00:00"},
        {"source_table_full_name": "main.kpi.margin", "entity_id": "d1", "workspace_id": "111", "last_read": "2026-09-10T00:00:00"},
    ]
    provider = _provider(rows=rows, dashboards=[("d1", "Weekly Revenue")])

    out = _run(provider, ["main.kpi.revenue", "main.kpi.margin", "main.kpi.unused"])

    revenue = out["main.kpi.revenue"]
    assert [d["id"] for d in revenue] == ["d2", "d1"]  # most recently read first
    assert revenue[1] == {
        "id": "d1", "name": "Weekly Revenue", "type": "dashboard", "description": None,
        "url": "https://ws.example.com/dashboardsv3/d1/published", "updated_at": "2026-09-01T00:00:00",
    }
    assert revenue[0]["name"] == "Dashboard d2"
    assert revenue[0]["url"] is None
    assert revenue[0]["description"] == "In workspace 222, not visible from this one"
    assert [d["id"] for d in out["main.kpi.margin"]] == ["d1"]
    assert out["main.kpi.unused"] == []


def test_duplicate_rows_and_unrequested_views_are_ignored():
    rows = [
        {"source_table_full_name": "main.kpi.revenue", "entity_id": "d1", "last_read": "2026-09-02"},
        {"source_table_full_name": "main.kpi.revenue", "entity_id": "d1", "last_read": "2026-09-01"},
        {"source_table_full_name": "other.x.y", "entity_id": "d9", "last_read": "2026-09-01"},
        {"source_table_full_name": "main.kpi.revenue", "entity_id": None},
    ]
    out = _run(_provider(rows=rows), ["main.kpi.revenue"])

    assert [d["id"] for d in out["main.kpi.revenue"]] == ["d1"]
    assert set(out) == {"main.kpi.revenue"}


def test_unreadable_system_table_degrades_to_no_dashboards(caplog):
    provider = _provider(sql_error=PermissionError("User does not have SELECT on system.access.table_lineage"))

    out = _run(provider, ["main.kpi.revenue"])

    assert out == {"main.kpi.revenue": []}
    assert "USE CATALOG on system" in caplog.text
    provider.client.lakeview.list.assert_not_called()


def test_dashboard_listing_failure_falls_back_to_ids():
    provider = _provider(rows=[{"source_table_full_name": "main.kpi.revenue", "entity_id": "d1", "workspace_id": "1"}])
    provider.client.lakeview.list.side_effect = RuntimeError("boom")

    out = _run(provider, ["main.kpi.revenue"])

    assert out["main.kpi.revenue"][0]["name"] == "Dashboard d1"


def test_large_view_lists_are_queried_in_batches(monkeypatch):
    monkeypatch.setattr(sync_data_assets, "_LINEAGE_BATCH", 2)
    provider = _provider()

    _run(provider, ["a.b.c1", "a.b.c2", "a.b.c3", "a.b.c4", "a.b.c5"])

    assert provider.execute_sql.await_count == 3
