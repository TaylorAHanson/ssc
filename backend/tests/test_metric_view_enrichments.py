from app.workers.tasks.sync_data_assets import (
    derive_metric_view_enrichments,
    is_system_managed_table,
)

YIELD_MV = """
version: 1.1
source: enterprise_dev.gold.wafer_test_results
joins:
  - name: lot
    source: enterprise_dev.silver.lot_master
    on: source.lot_id = lot.lot_id
dimensions:
  - name: facility
    expr: facility_code
  - name: lot_id
    expr: lot_id
    display_name: Wafer Lot
measures:
  - name: first_pass_yield
    display_name: First Pass Yield
    expr: SUM(passed_die) / NULLIF(SUM(tested_die), 0)
    comment: Die passing probe on first test.
  - name: tested_die
    expr: SUM(tested_die)
"""


def test_kpis_come_from_the_definition():
    out = derive_metric_view_enrichments("METRIC_VIEW", YIELD_MV)

    assert [k["name"] for k in out["kpis"]] == ["First Pass Yield", "tested_die"]
    fpy, tested = out["kpis"]
    assert fpy["formula"] == "SUM(passed_die) / NULLIF(SUM(tested_die), 0)"
    assert fpy["aggregation"] == "RATIO"
    assert fpy["description"] == "Die passing probe on first test."
    assert fpy["dimensions"] == ["facility", "Wafer Lot"]
    assert fpy["value"] is None and fpy["trend"] is None
    assert tested["aggregation"] == "SUM"


def test_upstream_tables_are_source_and_joins():
    out = derive_metric_view_enrichments("METRIC_VIEW", YIELD_MV)
    assert [t["fqn"] for t in out["upstream_tables"]] == [
        "enterprise_dev.gold.wafer_test_results",
        "enterprise_dev.silver.lot_master",
    ]


def test_sql_source_is_not_an_upstream_table():
    mv = "source: SELECT * FROM a.b.c WHERE x = 1\nmeasures:\n  - name: n\n    expr: COUNT(1)\n"
    assert derive_metric_view_enrichments("METRIC_VIEW", mv)["upstream_tables"] == []


def test_nothing_is_invented():
    out = derive_metric_view_enrichments("METRIC_VIEW", YIELD_MV)
    assert out["downstream_dashboards"] == []
    assert out["legacy_mappings"] == []


def test_non_metric_views_get_no_enrichment_even_with_metric_in_name():
    assert derive_metric_view_enrichments("MANAGED", YIELD_MV) == {}
    assert derive_metric_view_enrichments("VIEW", None) == {}


def test_missing_or_bad_definition_yields_empty_kpis():
    assert derive_metric_view_enrichments("METRIC_VIEW", None)["kpis"] == []
    assert derive_metric_view_enrichments("METRIC_VIEW", "measures: [unclosed")["kpis"] == []


def test_materialization_tables_are_system_managed():
    assert is_system_managed_table("__materialization_mat_832687bd_yield_by_facility_lot_vm_1")
    assert is_system_managed_table("__832687bd_ad5e_metric_view_mat_45dfd67f_yield_vm")
    assert not is_system_managed_table("yield_by_facility_lot_vm")


def test_kpis_carry_the_measure_identifier_for_live_queries():
    out = derive_metric_view_enrichments("METRIC_VIEW", YIELD_MV)
    assert [k["measure"] for k in out["kpis"]] == ["first_pass_yield", "tested_die"]
