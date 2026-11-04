"""Tests for Metric Views and Domain Hierarchy discovery endpoints & tools."""

from unittest.mock import patch

import pytest

from app.api.v1.data_assets import get_domains_hierarchy, list_data_assets, list_metric_views
from app.db.data_asset import DataAssetModel
from app.services.discover_domains import OTHER_DOMAIN
from app.tools.self_service.search_data_assets import search_metric_views
from tests.factories.discover_domain_factory import add_domains


@pytest.fixture
def db(db_session):
    add_domains(db_session)
    db_session.add_all([
        DataAssetModel(
            id="main.supply_chain.metric_demand_planning",
            catalog="main",
            schema="supply_chain",
            table_name="metric_demand_planning",
            type="METRIC_VIEW",
            tags=["Supply Chain/Planning & Forecasting"],
            description="Governed metric view for demand planning",
            certified=True,
            kpis=[{"name": "Forecast Accuracy", "value": "94.2%"}],
            upstream_tables=[{"fqn": "main.supply_chain.silver_orders"}],
            downstream_dashboards=[{"id": "d1", "name": "Weekly Demand", "type": "dashboard"}],
        ),
        # In Supply Chain through its schema's tag, with no subdomain.
        DataAssetModel(
            id="main.supply_chain.silver_orders",
            catalog="main",
            schema="supply_chain",
            table_name="silver_orders",
            type="TABLE",
            tags=[],
            scope_tag_keys={"catalog": [], "schema": ["supply chain"], "column": []},
            description="Silver orders table",
        ),
        DataAssetModel(
            id="main.finance.metric_revenue_forecast",
            catalog="main",
            schema="finance",
            table_name="metric_revenue_forecast",
            type="METRIC_VIEW",
            tags=["Finance/Revenue & Margin", "pii=no"],
            description="Governed metric view for financial revenue",
            certified=True,
            kpis=[{"name": "Gross Margin", "value": "41.5%"}],
        ),
        # A key=value tag whose value looks like a path is not a domain.
        DataAssetModel(
            id="main.raw.logs",
            catalog="main",
            schema="raw",
            table_name="logs",
            type="MANAGED",
            tags=["uc_volume=/Volumes/main/raw/logs"],
        ),
    ])
    db_session.flush()
    return db_session


def test_get_domains_hierarchy(db):
    result = get_domains_hierarchy(db=db)

    assert [r["domain"] for r in result] == ["Finance", "Supply Chain", OTHER_DOMAIN]
    sc = result[1]
    assert (sc["metric_view_count"], sc["table_count"], sc["dashboard_count"]) == (1, 1, 1)
    assert sc["icon"] == {"name": "TRUCK_TRAILER", "color": "#137DAE"} and sc["subtitle"] == "Plan, source and deliver"
    assert [(s["name"], s["tag_key"], s["metric_views_count"], s["tables_count"]) for s in sc["subdomains"]] == [
        ("Planning & Forecasting", "Supply Chain/Planning & Forecasting", 1, 0)]

    other = result[-1]
    assert other["label"] == "Other" and other["table_count"] == 1 and not other["has_subdomains"]


def test_everything_is_other_until_domains_are_synced(db):
    from app.db.discover_domain import DiscoverDomainModel

    db.query(DiscoverDomainModel).delete()
    result = get_domains_hierarchy(db=db)
    assert [(r["domain"], r["metric_view_count"], r["table_count"]) for r in result] == [(OTHER_DOMAIN, 2, 2)]


def test_get_metric_views(db):
    result = list_metric_views(domain="supply chain", db=db)
    assert [r["table_name"] for r in result] == ["metric_demand_planning"]
    assert result[0]["domains"] == ["Supply Chain"]
    assert result[0]["subdomains"] == ["Supply Chain/Planning & Forecasting"]
    assert result[0]["kpis"] is not None

    # A subdomain can be named within its domain.
    assert [r["table_name"] for r in list_metric_views(domain="Finance", subdomain="revenue & margin", db=db)] == [
        "metric_revenue_forecast"]
    assert list_metric_views(domain="Finance", subdomain="Planning & Forecasting", db=db) == []


def test_data_assets_filter_by_domain_including_other(db):
    assert {a["id"] for a in list_data_assets(domain="Supply Chain", db=db)} == {
        "main.supply_chain.metric_demand_planning", "main.supply_chain.silver_orders"}
    assert [a["id"] for a in list_data_assets(domain=OTHER_DOMAIN, db=db)] == ["main.raw.logs"]
    assert list_data_assets(domain="Nonexistent", db=db) == []


def test_search_metric_views_tool(db):
    with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([db])):
        res = search_metric_views._func(query="demand", domain="Supply Chain")
    assert [r["table_name"] for r in res["assets"]] == ["metric_demand_planning"]
    assert res["assets"][0]["subdomains"] == ["Supply Chain/Planning & Forecasting"]
