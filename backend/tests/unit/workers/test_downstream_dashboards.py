"""Downstream dashboards come from one system.access.table_lineage query per batch.

Replaced per-view calls to the rate-limited Catalog Explorer lineage endpoint
(see docs/METRIC_VIEW_LINEAGE.md).
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from databricks.sdk.errors import PermissionDenied

from app.core.config import settings
from app.services import workspace_cache
from app.workers.tasks import sync_data_assets
from app.workers.tasks.sync_data_assets import _fetch_downstream_dashboards

FINANCE_WS = {
    "workspace_id": "222", "workspace_name": "finance-prod",
    "workspace_url": "https://finance.cloud.databricks.com/", "status": "RUNNING",
}


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


def _cache(monkeypatch, *workspaces, targets=None):
    """Workspace cache rows, and the configured target hosts (none by default)."""
    monkeypatch.setattr(sync_data_assets, "_workspaces_by_id", lambda: {w["workspace_id"]: w for w in workspaces})
    monkeypatch.setattr(sync_data_assets, "_target_workspace_hosts", lambda: dict(targets or {}))


def _run(provider, fqns):
    return asyncio.run(_fetch_downstream_dashboards(provider, fqns))


def _row(fqn, dash_id, workspace_id, last_read="2026-09-01T00:00:00"):
    return {"source_table_full_name": fqn, "entity_id": dash_id, "workspace_id": workspace_id, "last_read": last_read}


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


def test_home_dashboards_are_named_and_linked_most_recent_first(monkeypatch):
    _cache(monkeypatch, FINANCE_WS)
    rows = [
        _row("main.kpi.revenue", "d1", "111", "2026-09-01T00:00:00"),
        _row("main.kpi.revenue", "d2", "222", "2026-09-20T00:00:00"),
        _row("main.kpi.margin", "d1", "111", "2026-09-10T00:00:00"),
    ]
    provider = _provider(rows=rows, dashboards=[("d1", "Weekly Revenue")])

    out = _run(provider, ["main.kpi.revenue", "main.kpi.margin", "main.kpi.unused"])

    revenue = out["main.kpi.revenue"]
    assert [d["id"] for d in revenue] == ["d2", "d1"]
    assert revenue[1] == {
        "id": "d1", "type": "dashboard", "name": "Weekly Revenue", "description": None,
        "url": "https://ws.example.com/dashboardsv3/d1/published", "workspace_id": "111",
        "updated_at": "2026-09-01T00:00:00",
    }
    assert [d["id"] for d in out["main.kpi.margin"]] == ["d1"]
    assert out["main.kpi.unused"] == []


def test_other_workspace_dashboard_links_there_and_is_titled_by_its_workspace(monkeypatch):
    _cache(monkeypatch, FINANCE_WS)

    out = _run(_provider(rows=[_row("main.kpi.revenue", "d2", 222)]), ["main.kpi.revenue"])

    assert out["main.kpi.revenue"] == [{
        "id": "d2", "type": "dashboard",
        "name": "Dashboard in finance-prod",
        "description": "Lives in the finance-prod workspace. Open it there to see its name.",
        "url": "https://finance.cloud.databricks.com/dashboardsv3/d2/published?o=222",
        "workspace_name": "finance-prod", "workspace_id": "222",
        "updated_at": "2026-09-01T00:00:00",
    }]


def test_workspace_missing_from_the_cache_gets_no_link_and_no_raw_ids(monkeypatch):
    _cache(monkeypatch)

    (entry,) = _run(_provider(rows=[_row("main.kpi.revenue", "d9", "1526721948438476")]), ["main.kpi.revenue"])["main.kpi.revenue"]

    assert entry["name"] == "Dashboard in another workspace"
    assert entry["url"] is None and entry["workspace_name"] is None
    assert "1526721948438476" not in entry["name"] + entry["description"]
    assert "d9" not in entry["name"]
    assert entry["id"] == "d9"


def test_workspace_that_is_not_running_gets_no_link(monkeypatch):
    _cache(monkeypatch, {**FINANCE_WS, "status": "CANCELLED"})

    (entry,) = _run(_provider(rows=[_row("main.kpi.revenue", "d2", "222")]), ["main.kpi.revenue"])["main.kpi.revenue"]

    assert entry["url"] is None
    assert entry["description"] == "Lives in the finance-prod workspace."


def test_configured_target_workspace_names_its_dashboards(monkeypatch):
    _cache(monkeypatch, FINANCE_WS, targets={"finance.cloud.databricks.com": "https://finance.cloud.databricks.com"})
    remote = MagicMock()

    def _get(dash_id):
        if dash_id == "d3":
            raise PermissionDenied("no access")
        return SimpleNamespace(display_name="Finance KPIs")

    remote.lakeview.get.side_effect = _get
    built = []
    monkeypatch.setattr(sync_data_assets, "_target_workspace_client", lambda host: built.append(host) or remote)
    rows = [_row("main.kpi.revenue", "d2", "222"), _row("main.kpi.revenue", "d3", "222"), _row("main.kpi.margin", "d2", "222")]

    out = _run(_provider(rows=rows), ["main.kpi.revenue", "main.kpi.margin"])

    named, unnamed = sorted(out["main.kpi.revenue"], key=lambda d: d["id"])
    assert named["name"] == "Finance KPIs"
    assert named["description"] == "Lives in the finance-prod workspace."
    assert named["url"].endswith("/dashboardsv3/d2/published?o=222")
    assert unnamed["name"] == "Dashboard in finance-prod"
    assert out["main.kpi.margin"][0]["name"] == "Finance KPIs"
    assert built == ["https://finance.cloud.databricks.com"]
    assert remote.lakeview.get.call_count == 2  # once per dashboard, not per view


def test_unreachable_target_workspace_never_fails_the_sync(monkeypatch, caplog):
    _cache(monkeypatch, FINANCE_WS, targets={"finance.cloud.databricks.com": "https://finance.cloud.databricks.com"})

    def _boom(host):
        raise ValueError("Databricks client initialization failed")

    monkeypatch.setattr(sync_data_assets, "_target_workspace_client", _boom)

    with caplog.at_level(logging.INFO, logger=sync_data_assets.logger.name):
        (entry,) = _run(_provider(rows=[_row("main.kpi.revenue", "d2", "222")]), ["main.kpi.revenue"])["main.kpi.revenue"]

    assert entry["name"] == "Dashboard in finance-prod" and entry["url"]
    assert "1 workspace(s) failed" in caplog.text


def test_unconfigured_workspace_is_not_contacted(monkeypatch):
    _cache(monkeypatch, FINANCE_WS)
    called = []
    monkeypatch.setattr(sync_data_assets, "_target_workspace_client", lambda host: called.append(host))

    _run(_provider(rows=[_row("main.kpi.revenue", "d2", "222")]), ["main.kpi.revenue"])

    assert called == []


def test_duplicate_rows_and_unrequested_views_are_ignored(monkeypatch):
    _cache(monkeypatch)
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


def test_dashboard_listing_failure_falls_back_to_the_workspace_cache(monkeypatch):
    _cache(monkeypatch, {**FINANCE_WS, "workspace_id": "1"})
    provider = _provider(rows=[_row("main.kpi.revenue", "d1", "1")])
    provider.client.lakeview.list.side_effect = RuntimeError("boom")

    out = _run(provider, ["main.kpi.revenue"])

    assert out["main.kpi.revenue"][0]["name"] == "Dashboard in finance-prod"


def test_unreadable_workspace_cache_still_lists_the_dashboard(monkeypatch):
    def _broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(workspace_cache, "cached_by_id", _broken)
    monkeypatch.setattr(sync_data_assets, "_target_workspace_hosts", lambda: {})

    (entry,) = _run(_provider(rows=[_row("main.kpi.revenue", "d2", "222")]), ["main.kpi.revenue"])["main.kpi.revenue"]

    assert entry["name"] == "Dashboard in another workspace" and entry["url"] is None


def test_large_view_lists_are_queried_in_batches(monkeypatch):
    monkeypatch.setattr(sync_data_assets, "_LINEAGE_BATCH", 2)
    provider = _provider()

    _run(provider, ["a.b.c1", "a.b.c2", "a.b.c3", "a.b.c4", "a.b.c5"])

    assert provider.execute_sql.await_count == 3
