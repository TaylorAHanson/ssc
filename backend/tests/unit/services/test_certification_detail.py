"""Data Certification detail: per-table attribution and run history.

The drawer's red/green table list is derived from the last run's rego messages,
so the load-bearing tests are that a failure lands on exactly the table it
names, and that history reports only the runs where the failing set changed.
"""
import uuid
from datetime import datetime, timedelta

from app.db.data_asset import DataAssetModel
from app.db.data_contract import DataContractModel
from app.db.request import RequestModel
from app.db.sentinel_finding import SentinelFindingModel
from app.services.certification_detail import (
    build_certification_detail,
    category_summary,
    contract_overview,
    run_changes,
    table_outcomes,
)

T1 = "cat.sch.orders"
T2 = "cat.sch.customers"

CONTRACT = """
apiVersion: v3.1.0
kind: DataContract
domain: sales
dataProduct: Customer Analytics
version: 1.2.0
status: active
description:
  purpose: Track customers.
  usage: Internal analytics.
servers:
  - id: prod
    type: databricks
    catalog: cat
    schema: sch
schema:
  - name: orders
    physicalName: orders
  - name: customers
    physicalName: customers
"""


def _rule(rule_id, passed, category="Tagging", messages=None):
    return {
        "id": rule_id,
        "description": f"{rule_id} description",
        "category": category,
        "passed": passed,
        "messages": messages or [],
    }


LAST_RUN = [
    _rule("required_tags", False, messages=[f"Required tag 'data_owner' is missing from table '{T1}'."]),
    _rule("column_descriptions", True, category="Metadata"),
    _rule("yaml_valid", False, category="Structure", messages=["Data Contract YAML is invalid and could not be parsed."]),
]


# ---------------------------------------------------------------------------
# Contract overview
# ---------------------------------------------------------------------------
def test_overview_reads_description_and_qualifies_tables():
    ov = contract_overview(CONTRACT)
    assert ov["data_product"] == "Customer Analytics"
    assert ov["domain"] == "sales"
    assert ov["purpose"] == "Track customers."
    assert ov["odcs_version"] == "1.2.0"
    assert ov["tables"] == [T1, T2]
    assert ov["parse_error"] is None


def test_overview_reports_invalid_yaml_instead_of_raising():
    ov = contract_overview("schema: [unclosed")
    assert ov["parse_error"]
    assert ov["tables"] == []


# ---------------------------------------------------------------------------
# Per-table attribution
# ---------------------------------------------------------------------------
def test_failure_lands_only_on_the_table_it_names():
    out = table_outcomes([T1, T2], LAST_RUN, [], snapshot=None)
    by_name = {t["name"]: t for t in out["tables"]}

    assert by_name[T1]["status"] == "fail"
    assert [c["id"] for c in by_name[T1]["failed_checks"]] == ["required_tags"]
    assert by_name[T2]["status"] == "pass"
    assert by_name[T2]["failed_checks"] == []


def test_table_name_prefix_does_not_match_a_longer_name():
    """'cat.sch.orders' must not claim a message about 'cat.sch.orders_v2'."""
    rules = [_rule("required_tags", False, messages=["Required tag 'x' is missing from table 'cat.sch.orders_v2'."])]
    out = table_outcomes([T1], rules, [], snapshot=None)
    assert out["tables"][0]["status"] == "pass"
    assert [c["id"] for c in out["dataset_checks"]] == ["required_tags"]


def test_unattributed_failures_are_dataset_level():
    out = table_outcomes([T1, T2], LAST_RUN, [], snapshot=None)
    assert [c["id"] for c in out["dataset_checks"]] == ["yaml_valid"]


def test_dq_failures_attach_by_owning_asset():
    dq = [{"rule": "tdq_nulls", "table": "orders", "asset": T1}]
    out = table_outcomes([T1, T2], [_rule("dq_zero_failed", True, "Data Quality")], dq, snapshot=None)
    by_name = {t["name"]: t for t in out["tables"]}
    assert by_name[T1]["status"] == "fail"
    assert by_name[T1]["dq_failed_rules"] == dq
    assert by_name[T2]["status"] == "pass"


def test_never_scanned_tables_are_not_scanned():
    out = table_outcomes([T1], [], [], snapshot=None)
    assert out["tables"][0]["status"] == "not_scanned"


def test_table_added_after_last_scan_is_not_scanned():
    snapshot = [{"name": T1, "type": "table", "exists": True, "certified": False, "tags": {"domain": "sales"}}]
    out = table_outcomes([T1, T2], LAST_RUN, [], snapshot=snapshot)
    by_name = {t["name"]: t for t in out["tables"]}
    assert by_name[T1]["tags"] == {"domain": "sales"}
    assert by_name[T2]["status"] == "not_scanned"


def test_category_summary_follows_rego_order():
    cats = category_summary(LAST_RUN)
    assert [c["category"] for c in cats] == ["Structure", "Metadata", "Tagging"]
    assert {c["category"]: c["status"] for c in cats} == {
        "Structure": "fail", "Metadata": "pass", "Tagging": "fail",
    }


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def _run(request_id, failed_ids, at):
    return {
        "request_id": request_id,
        "run_at": at,
        "passed": not failed_ids,
        "failed_count": len(failed_ids),
        "total_count": 5,
        "failed_checks": [{"id": i, "description": i, "category": "Tagging"} for i in failed_ids],
    }


def test_changes_report_only_runs_where_failures_changed():
    t0 = datetime(2026, 9, 1)
    runs_oldest_first = [
        _run("r1", ["a", "b"], t0),
        _run("r2", ["a", "b"], t0 + timedelta(hours=1)),
        _run("r3", ["a"], t0 + timedelta(hours=2)),
        _run("r4", ["a", "c"], t0 + timedelta(hours=3)),
    ]
    changes = run_changes(list(reversed(runs_oldest_first)))

    assert [c["request_id"] for c in changes] == ["r4", "r3", "r1"]
    assert changes[-1]["kind"] == "first"
    assert [c["id"] for c in changes[1]["resolved"]] == ["b"]
    assert [c["id"] for c in changes[0]["newly_failing"]] == ["c"]


def _seed_run(db, request_id, at, rule_results, dataset_id="customer_analytics", workspace="default"):
    if not db.get(RequestModel, request_id):
        db.add(RequestModel(id=request_id, type="enforcement_sentinel", title="scan", created_at=at))
    db.add(SentinelFindingModel(
        id=str(uuid.uuid4()),
        request_id=request_id,
        kind="check",
        workspace=workspace,
        resource_id=dataset_id,
        resource_type="data_product",
        policy="data_certification",
        data={"rule_results": rule_results},
        created_at=at,
    ))


def test_build_detail_end_to_end(db_session):
    ds = "customer_analytics"
    db_session.add(DataContractModel(id="v1", dataset_id=ds, yaml_content=CONTRACT, version=1, is_active=False))
    db_session.add(DataContractModel(id="v2", dataset_id=ds, yaml_content=CONTRACT, version=2, is_active=True))
    db_session.add(DataAssetModel(
        id=ds, catalog="2 tables", schema="", table_name=ds, type="DATA_PRODUCT",
        certified=False, certification_rule_results=LAST_RUN,
        data_quality={"failed_rule_count": 0, "failed_rules": []},
        last_synced_at=datetime(2026, 9, 2),
    ))
    t0 = datetime(2026, 9, 1)
    _seed_run(db_session, "r1", t0, [_rule("required_tags", True)])
    _seed_run(db_session, "r2", t0 + timedelta(hours=1), LAST_RUN)
    # Same run evaluated in a second workspace: still one run in history.
    _seed_run(db_session, "r2", t0 + timedelta(hours=1), LAST_RUN, workspace="other")
    _seed_run(db_session, "r-other", t0, LAST_RUN, dataset_id="someone_else")
    db_session.flush()

    detail = build_certification_detail(db_session, ds)

    assert detail["overview"]["data_product"] == "Customer Analytics"
    assert detail["contract"]["version"] == 2
    assert [v["version"] for v in detail["history"]["contract_versions"]] == [2, 1]
    assert {t["name"]: t["status"] for t in detail["tables"]} == {T1: "fail", T2: "pass"}
    runs = detail["history"]["runs"]
    assert [r["request_id"] for r in runs] == ["r2", "r1"]
    assert runs[0]["failed_count"] == 2 and runs[1]["passed"]
    assert [c["kind"] for c in detail["history"]["changes"]] == ["change", "first"]


def test_build_detail_unknown_dataset_is_none(db_session):
    assert build_certification_detail(db_session, "nope") is None
