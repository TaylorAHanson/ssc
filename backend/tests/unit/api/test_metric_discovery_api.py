"""Tests for Metric Views, Domain Hierarchy, and Legacy Mapping discovery endpoints & tools."""

from unittest.mock import MagicMock, patch
import pytest
from app.api.v1.data_assets import get_domains_hierarchy, get_legacy_mappings, list_metric_views
from app.db.data_asset import DataAssetModel
from app.tools.self_service.search_data_assets import search_metric_views


@pytest.fixture
def sample_assets():
    return [
        DataAssetModel(
            id="sc_plan_view",
            catalog="main",
            schema="supply_chain",
            table_name="metric_demand_planning",
            type="METRIC_VIEW",
            domain="Supply Chain",
            subdomain="Planning & Forecasting",
            description="Governed metric view for demand planning",
            certified=True,
            kpis=[{"name": "Forecast Accuracy", "value": "94.2%"}],
            upstream_tables=["main.supply_chain.silver_orders"],
            downstream_dashboards=[{"id": "d1", "name": "Weekly Demand", "type": "dashboard"}],
            legacy_mappings=[{
                "dashboard": "Tableau Demand v2",
                "status": "Active",
                "owner": "Demand Planning",
                "source": "Tableau Server",
            }],
        ),
        DataAssetModel(
            id="sc_orders_table",
            catalog="main",
            schema="supply_chain",
            table_name="silver_orders",
            type="TABLE",
            domain="Supply Chain",
            subdomain="Planning & Forecasting",
            description="Silver orders table",
            certified=False,
        ),
        DataAssetModel(
            id="fin_revenue_view",
            catalog="main",
            schema="finance",
            table_name="metric_revenue_forecast",
            type="METRIC_VIEW",
            domain="Finance",
            subdomain="Revenue & Margin",
            description="Governed metric view for financial revenue",
            certified=True,
            kpis=[{"name": "Gross Margin", "value": "41.5%"}],
            downstream_dashboards=[],
            legacy_mappings=[],
        ),
    ]


def test_get_domains_hierarchy(sample_assets):
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = sample_assets

    result = get_domains_hierarchy(db=mock_db)
    assert len(result) >= 2
    domains = [r["domain"] for r in result]
    assert "Supply Chain" in domains
    assert "Finance" in domains

    sc_domain = next(r for r in result if r["domain"] == "Supply Chain")
    assert sc_domain["metric_view_count"] >= 1
    assert sc_domain["subdomain_count"] >= 1
    assert any(sd["name"] == "Planning & Forecasting" for sd in sc_domain["subdomains"])


def test_get_metric_views(sample_assets):
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [sample_assets[0], sample_assets[2]]

    result = list_metric_views(domain="Supply Chain", db=mock_db)
    assert len(result) == 2
    assert result[0]["table_name"] == "metric_demand_planning"
    assert result[0]["subdomain"] == "Planning & Forecasting"
    assert result[0]["kpis"] is not None


def test_get_legacy_mappings(sample_assets):
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [sample_assets[0]]

    result = get_legacy_mappings(db=mock_db)
    assert len(result) >= 1
    assert any(m["dashboard"] == "Tableau Demand v2" for m in result)
    assert any(m["status"] == "Active" for m in result)


def test_search_metric_views_tool(sample_assets):
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.limit.return_value.all.return_value = [sample_assets[0], sample_assets[2]]

    with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([mock_db])):
        res = search_metric_views._func(
            query="demand",
            domain="Supply Chain",
        )
        assert "assets" in res
        assert res["count"] >= 1
        assert any(r["table_name"] == "metric_demand_planning" for r in res["assets"])
