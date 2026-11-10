"""run_sql answers say which tables they came from, and what the catalog knows of each."""
from unittest.mock import patch

from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.services import sql_provenance as sp


def test_source_tables_are_the_tables_read_not_ctes_or_aliases():
    sql = """
        WITH recent AS (SELECT * FROM main.sales.orders WHERE d > current_date() - 7)
        SELECT r.region, count(*) FROM recent r
        JOIN `main`.`ref`.`regions` g ON g.id = r.region_id
        LEFT JOIN main.sales.ORDERS o2 ON o2.id = r.id
        GROUP BY 1
    """
    assert sp.source_tables(sql) == ["main.ref.regions", "main.sales.orders"]


def test_statements_that_read_no_table_have_no_sources():
    assert sp.source_tables("SELECT 1") == []
    assert sp.source_tables("this is not sql (") == []


def test_sources_carry_trust_signals_and_hidden_tables_stay_anonymous(db_session):
    db_session.add_all([
        DataAssetModel(id="main.sales.orders", catalog="main", schema="sales", table_name="orders", type="MANAGED",
                       tags=["source_system=SAP", "data_owner=Sales Ops"], certified=False,
                       certification_violations=["No description", "No owner tag"],
                       data_quality={"reliability": 0.82, "failed_rule_count": 1, "failed_rules": ["nulls"]}),
        DataAssetModel(id="main.secret.t", catalog="main", schema="secret", table_name="t", type="MANAGED",
                       tags=["hide_from_discover"]),
    ])
    db_session.flush()
    with patch.object(settings, "DISCOVER_HIDDEN_TAG_KEYS", ["hide_from_discover"]):
        out = sp.describe_sources(db_session, ["MAIN.sales.orders", "main.secret.t", "main.raw.unknown"])

    orders, secret, unknown = out
    assert orders["in_catalog"] and orders["name"] == "main.sales.orders"
    assert orders["source_system"] == "SAP" and orders["owner"] == "Sales Ops"
    assert orders["certified"] is False and orders["certification_issues"] == ["No description", "No owner tag"]
    assert orders["data_quality"]["reliability"] == 0.82
    assert orders["discover_path"] == "/discovery?asset=main.sales.orders"
    assert secret == {"name": "main.secret.t", "in_catalog": False}
    assert unknown == {"name": "main.raw.unknown", "in_catalog": False}
