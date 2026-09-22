"""SQL shape + isolation of the batched ADOC failed-rules queries."""
import app.providers.databricks.handlers.dataset_handler as dh
from app.providers.databricks.handlers.dataset_handler import (
    DatasetResourceHandler,
    _ADOC_HISTORY_TABLES,
)

SCHEMA = "enterprise_prd.data_quality"


def _handler():
    return DatasetResourceHandler.__new__(DatasetResourceHandler)


def test_core_query_covers_history_tables_and_excludes_reconciliation():
    sql = _handler()._build_failed_rules_query(SCHEMA, 7, ["cat.sch.tbl"])

    for t in _ADOC_HISTORY_TABLES:
        assert f"{SCHEMA}.{t}" in sql
    # Core query must NOT touch the reconciliation table — that's what keeps a
    # recon schema problem from failing the whole window's fetch.
    assert "adoc_reconciliation_history" not in sql
    assert "assetInfo.assetUid AS assetUid" in sql
    assert "assetUid LIKE '%cat.sch.tbl%'" in sql
    assert "resultPercent < threshold" in sql


def test_reconciliation_query_is_isolated_with_left_and_right_arms():
    sql = _handler()._build_reconciliation_failed_rules_query(SCHEMA, 7, ["cat.sch.tbl"])

    # Only the recon table — no core tables leak into the isolated statement.
    assert f"{SCHEMA}.adoc_reconciliation_history" in sql
    for t in _ADOC_HISTORY_TABLES:
        assert f"{SCHEMA}.{t}" not in sql
    # Both sides aliased into the shared scalar columns.
    assert "assetInfo.leftBackingAssetUid AS assetUid" in sql
    assert "assetInfo.leftAssetName AS assetName" in sql
    assert "assetInfo.rightBackingAssetUid AS assetUid" in sql
    assert "assetInfo.rightAssetName AS assetName" in sql
    # Two arms => the recon table is scanned once per side.
    assert sql.count("adoc_reconciliation_history") == 2
    assert "assetUid LIKE '%cat.sch.tbl%'" in sql


def test_apply_failed_rows_attributes_by_uid_substring():
    handler = _handler()
    asset = {"failed_rules": [], "failed_rule_count": 0}
    group = [(asset, "cat.sch.tbl")]
    rows = [
        # uid contains the full name -> attributed
        ["urn:cat.sch.tbl", "cat.sch.tbl", "rule-A", "RECON", "col", "dim", "0.5", "0.9", "10"],
        # unrelated uid -> ignored
        ["urn:other.tbl", "other.tbl", "rule-B", "RECON", None, None, "0.1", "0.9", "3"],
    ]
    handler._apply_failed_rows(group, rows)

    assert len(asset["failed_rules"]) == 1
    fr = asset["failed_rules"][0]
    assert fr["rule"] == "rule-A"
    assert fr["score"] == 0.5
    assert fr["threshold"] == 0.9
    assert fr["rows_failed"] == 10


def test_reconciliation_failure_does_not_drop_core_assets(monkeypatch):
    """The core guarantee of the de-risk: a failed recon query must NOT leave
    core assets at 'not fetched' (-1). Core succeeds, recon fails → the asset
    is still fetched with its core failure, just without recon signal."""
    monkeypatch.setattr(dh.settings, "DATABRICKS_WAREHOUSE_ID", "wh-1", raising=False)
    monkeypatch.setattr(dh.settings, "DATA_QUALITY_ADOC_SCHEMA", SCHEMA, raising=False)

    def fake_run(self, query):
        if "adoc_reconciliation_history" in query:
            return "FAILED", []  # recon blows up (e.g. bad column / no perms)
        return "SUCCEEDED", [
            ["urn:cat.sch.tbl", "cat.sch.tbl", "core-rule", "DQ",
             "col", "completeness", "0.5", "0.9", "7"],
        ]

    monkeypatch.setattr(DatasetResourceHandler, "_run_dq_statement", fake_run)

    handler = _handler()
    asset = {"failed_rules": [], "failed_rule_count": -1}
    handler._populate_failed_rules_batched([(asset, "cat.sch.tbl", 7)])

    # Fetched (not -1) and the core failure survived despite the recon failure.
    assert asset["failed_rule_count"] == 1
    assert asset["failed_rules"][0]["rule"] == "core-rule"


def test_reconciliation_success_adds_to_core_failures(monkeypatch):
    monkeypatch.setattr(dh.settings, "DATABRICKS_WAREHOUSE_ID", "wh-1", raising=False)
    monkeypatch.setattr(dh.settings, "DATA_QUALITY_ADOC_SCHEMA", SCHEMA, raising=False)

    def fake_run(self, query):
        if "adoc_reconciliation_history" in query:
            return "SUCCEEDED", [
                ["urn:cat.sch.tbl", "cat.sch.tbl", "recon-rule", "RECON",
                 None, None, "0.2", "0.95", "4"],
            ]
        return "SUCCEEDED", [
            ["urn:cat.sch.tbl", "cat.sch.tbl", "core-rule", "DQ",
             "col", "completeness", "0.5", "0.9", "7"],
        ]

    monkeypatch.setattr(DatasetResourceHandler, "_run_dq_statement", fake_run)

    handler = _handler()
    asset = {"failed_rules": [], "failed_rule_count": -1}
    handler._populate_failed_rules_batched([(asset, "cat.sch.tbl", 7)])

    rules = {fr["rule"] for fr in asset["failed_rules"]}
    assert rules == {"core-rule", "recon-rule"}
    assert asset["failed_rule_count"] == 2
