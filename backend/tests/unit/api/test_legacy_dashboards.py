"""Admins curate legacy dashboard → metric view mappings; everyone reads them scoped by the view's domain."""

import pytest
from fastapi import HTTPException

from app.api.v1.legacy_dashboards import (
    LegacyDashboardIn,
    LegacyDashboardImport,
    create_legacy_dashboard,
    delete_legacy_dashboard,
    import_legacy_dashboards,
    list_legacy_dashboards,
    update_legacy_dashboard,
)
from app.db.data_asset import DataAssetModel
from app.models.user import User
from app.tools.self_service.search_data_assets import search_metric_views

ADMIN = User(id="admin", email="admin@x.com", full_name="Admin", entitlements=[], roles=["Platform Admin"])


@pytest.fixture
def db(db_session):
    db_session.add_all([
        DataAssetModel(id="c.sc.metric_demand_planning", catalog="c", schema="sc", table_name="metric_demand_planning",
                       type="METRIC_VIEW", domain="Supply Chain", subdomain="Planning & Forecasting",
                       description="End-to-end demand planning"),
        DataAssetModel(id="c.fin.sem_revenue", catalog="c", schema="fin", table_name="sem_revenue",
                       type="METRIC_VIEW", domain="Finance", subdomain="Revenue & Margin"),
        DataAssetModel(id="c.sc.orders", catalog="c", schema="sc", table_name="orders", type="MANAGED",
                       domain="Supply Chain"),
    ])
    db_session.flush()
    return db_session


def _create(db, **fields):
    entry = LegacyDashboardIn(**{"dashboard": "Weekly Demand Review", "metric_view": "c.sc.metric_demand_planning",
                                 **fields})
    return create_legacy_dashboard(entry, db=db, current_user=ADMIN)


def test_create_takes_domain_from_the_metric_view(db):
    out = _create(db, status="migrating", owner=" FPA ", url="")

    assert out["status"] == "Migrating" and out["owner"] == "FPA" and out["url"] is None
    assert (out["domain"], out["subdomain"]) == ("Supply Chain", "Planning & Forecasting")
    assert out["metric_view"] == "metric_demand_planning" and out["in_catalog"]
    assert out["metric_view_description"] == "End-to-end demand planning"
    assert out["updated_by"] == "admin@x.com"


def test_metric_view_can_be_named_by_table_name_but_must_be_a_metric_view(db):
    assert _create(db, metric_view="SEM_REVENUE")["metric_view_id"] == "c.fin.sem_revenue"
    for ref in ("c.sc.orders", "orders", "nope"):
        with pytest.raises(HTTPException) as e:
            _create(db, dashboard=f"D {ref}", metric_view=ref)
        assert e.value.status_code == 422


def test_same_dashboard_twice_on_one_view_is_rejected(db):
    _create(db)
    with pytest.raises(HTTPException) as e:
        _create(db, dashboard="weekly demand review")
    assert e.value.status_code == 409
    assert _create(db, metric_view="sem_revenue")  # same name on another view is fine


def test_url_must_be_http():
    with pytest.raises(ValueError):
        LegacyDashboardIn(dashboard="D", metric_view="mv", url="javascript:alert(1)")


def test_list_filters_by_the_views_domain_and_status(db):
    _create(db)
    _create(db, dashboard="Revenue Tracker", metric_view="sem_revenue", status="Deprecated")

    everyone = User(id="u", email="u@x.com", full_name="U", entitlements=[], roles=["User"])
    assert [r["dashboard"] for r in list_legacy_dashboards(db=db, current_user=everyone)] == [
        "Revenue Tracker", "Weekly Demand Review"]
    assert [r["dashboard"] for r in list_legacy_dashboards(domain="Finance", db=db, current_user=everyone)] == [
        "Revenue Tracker"]
    assert [r["dashboard"] for r in list_legacy_dashboards(subdomain="Planning & Forecasting", db=db,
                                                           current_user=everyone)] == ["Weekly Demand Review"]
    assert [r["dashboard"] for r in list_legacy_dashboards(status="active", db=db, current_user=everyone)] == [
        "Weekly Demand Review"]


def test_update_and_delete(db):
    created = _create(db)
    updated = update_legacy_dashboard(
        created["id"], LegacyDashboardIn(dashboard="Weekly Demand Review", metric_view="sem_revenue",
                                         status="Deprecated"), db=db, current_user=ADMIN)
    assert updated["metric_view_id"] == "c.fin.sem_revenue" and updated["status"] == "Deprecated"

    assert delete_legacy_dashboard(created["id"], db=db, current_user=ADMIN) == {"deleted": created["id"]}
    assert list_legacy_dashboards(db=db, current_user=ADMIN) == []
    with pytest.raises(HTTPException) as e:
        delete_legacy_dashboard(created["id"], db=db, current_user=ADMIN)
    assert e.value.status_code == 404


def test_mapping_to_a_view_that_left_the_catalog_still_lists(db):
    created = _create(db)
    db.query(DataAssetModel).filter(DataAssetModel.id == "c.sc.metric_demand_planning").delete()
    row = list_legacy_dashboards(db=db, current_user=ADMIN)[0]
    assert row["id"] == created["id"] and not row["in_catalog"] and row["domain"] is None
    assert row["metric_view"] == "metric_demand_planning"


def test_import_reports_bad_rows_and_skips_repeats(db):
    _create(db)
    out = import_legacy_dashboards(LegacyDashboardImport(rows=[
        {"dashboard": "Weekly Demand Review", "metric_view": "metric_demand_planning"},  # already exists
        {"dashboard": "Forecast vs Actuals", "metric_view": "metric_demand_planning", "status": "deprecated"},
        {"dashboard": "Forecast vs Actuals", "metric_view": "c.sc.metric_demand_planning"},  # repeat in file
        {"dashboard": "Ghost", "metric_view": "missing_view"},
        {"dashboard": "", "metric_view": "sem_revenue"},
        {"dashboard": "Odd Status", "metric_view": "sem_revenue", "status": "Retired"},
    ]), db=db, current_user=ADMIN)

    assert out["created"] == 1 and out["skipped"] == 2
    assert [e["row"] for e in out["errors"]] == [4, 5, 6]
    assert "missing_view" in out["errors"][0]["error"]
    assert out["errors"][1]["error"].startswith("dashboard:")
    assert out["errors"][2]["error"].startswith("status:")


def test_agent_finds_a_metric_view_by_its_legacy_dashboard_name(db, monkeypatch):
    _create(db, dashboard="Tableau Wafer Capacity")
    monkeypatch.setattr("app.tools.self_service.search_data_assets.get_db", lambda: iter([db]))
    monkeypatch.setattr(db, "close", lambda: None)

    res = search_metric_views._func(query="wafer capacity")

    assert [a["id"] for a in res["assets"]] == ["c.sc.metric_demand_planning"]
    assert res["assets"][0]["legacy_mappings"][0]["dashboard"] == "Tableau Wafer Capacity"
