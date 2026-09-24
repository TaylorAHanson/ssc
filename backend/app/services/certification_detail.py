"""Build the Data Certification detail view for one data set.

A data set (``DATA_PRODUCT`` asset) is a group of Unity Catalog tables described
by one ODCS contract. The certification table lists them; this module assembles
everything the per-data-set drawer shows when a row is opened:

* **overview** — the contract's own description (purpose, domain, …) plus
  per-category pass/fail rolled up from the last policy run;
* **tables** — each contracted table marked pass / fail / not scanned, with the
  failing checks and data-quality rules that name it;
* **history** — recent sentinel runs for the data set, the runs where its set
  of failing checks changed, and the contract's version history.

Everything is read from what the sentinel and contract sync already persist
(``data_assets``, ``data_contracts``, ``sentinel_findings``); nothing here calls
Databricks.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy.orm import Session

from app.db.data_asset import DataAssetModel
from app.db.data_contract import DataContractModel
from app.db.request import RequestModel
from app.db.sentinel_finding import SentinelFindingModel
from app.providers.databricks.handlers.dataset_handler import contract_table_names

logger = logging.getLogger(__name__)

#: Default number of sentinel runs returned in the history section.
DEFAULT_HISTORY_RUNS = 120

# Category display order; mirrors ``policies/data_certification.rego``
# (rule_category). Categories not listed here sort after these.
CATEGORY_ORDER = ["Structure", "Metadata", "Tagging", "Access Control", "Data Quality"]


def _json(value: Any) -> Any:
    """JSONType columns may hold a JSON string on SQLite; decode if so."""
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def contract_overview(yaml_content: Optional[str]) -> Dict[str, Any]:
    """Pull the human-facing fields out of an ODCS contract.

    Returns ``parse_error`` instead of raising when the YAML is invalid, so the
    drawer can still render the rest of the data set.
    """
    overview: Dict[str, Any] = {
        "data_product": None,
        "domain": None,
        "status": None,
        "odcs_version": None,
        "purpose": None,
        "usage": None,
        "limitations": None,
        "tables": [],
        "parse_error": None,
    }
    if not yaml_content:
        return overview
    try:
        parsed = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as e:
        overview["parse_error"] = str(e)
        return overview
    if not isinstance(parsed, dict):
        overview["parse_error"] = "Contract is not a YAML mapping."
        return overview

    description = parsed.get("description")
    if isinstance(description, dict):
        overview["purpose"] = description.get("purpose")
        overview["usage"] = description.get("usage")
        overview["limitations"] = description.get("limitations")
    elif isinstance(description, str):
        overview["purpose"] = description
    overview["data_product"] = parsed.get("dataProduct")
    overview["domain"] = parsed.get("domain")
    overview["status"] = parsed.get("status")
    overview["odcs_version"] = str(parsed["version"]) if parsed.get("version") is not None else None
    try:
        overview["tables"] = contract_table_names(parsed)
    except (AttributeError, TypeError) as e:
        # A structurally odd contract (e.g. ``schema`` not a list of mappings).
        logger.warning("Could not resolve contract tables: %s", e)
    return overview


def _mentions(message: str, table: str) -> bool:
    # Rego messages quote the asset's full name: "... table 'cat.sch.tbl'."
    return f"'{table}'" in (message or "")


def _dq_rule_belongs_to(rule: Dict[str, Any], table: str) -> bool:
    owner = rule.get("asset")
    if owner:
        return owner == table
    # Scans before ``asset`` was recorded: fall back to the DQ tool's name.
    name = rule.get("table") or ""
    return name == table or name == table.split(".")[-1]


def table_outcomes(
    tables: List[str],
    rule_results: List[Dict[str, Any]],
    dq_failed_rules: List[Dict[str, Any]],
    snapshot: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Split the last run's failures per table.

    Returns ``{"tables": [...], "dataset_checks": [...]}``. A table is
    ``not_scanned`` when the data set has never been scanned, or when a scan
    snapshot exists but doesn't include it (it was added to the contract after
    the last run). Failures whose messages name no contracted table (e.g. an
    invalid contract) land in ``dataset_checks``.
    """
    scanned = bool(rule_results)
    snap_by_name = {s.get("name"): s for s in (snapshot or []) if s.get("name")}
    failed_rules = [r for r in rule_results if not r.get("passed")]

    rows: List[Dict[str, Any]] = []
    attributed: Dict[str, set] = {}
    for table in tables:
        checks = []
        for rule in failed_rules:
            msgs = [m for m in (rule.get("messages") or []) if _mentions(m, table)]
            if msgs:
                checks.append({
                    "id": rule.get("id"),
                    "description": rule.get("description"),
                    "category": rule.get("category") or "Other",
                    "messages": msgs,
                })
                attributed.setdefault(rule.get("id"), set()).update(msgs)
        dq = [r for r in dq_failed_rules if _dq_rule_belongs_to(r, table)]
        snap = snap_by_name.get(table)

        if not scanned or (snapshot is not None and snap is None):
            status = "not_scanned"
        elif checks or dq:
            status = "fail"
        else:
            status = "pass"

        parts = table.split(".")
        rows.append({
            "name": table,
            "catalog": parts[0] if len(parts) == 3 else None,
            "schema_name": parts[1] if len(parts) == 3 else None,
            "table": parts[-1],
            "type": (snap or {}).get("type"),
            "exists": (snap or {}).get("exists"),
            "certified": (snap or {}).get("certified"),
            "tags": (snap or {}).get("tags") or {},
            "status": status,
            "failed_checks": checks,
            "dq_failed_rules": dq,
        })

    dataset_checks = []
    for rule in failed_rules:
        seen = attributed.get(rule.get("id"), set())
        rest = [m for m in (rule.get("messages") or []) if m not in seen]
        if rest or not rule.get("messages"):
            dataset_checks.append({
                "id": rule.get("id"),
                "description": rule.get("description"),
                "category": rule.get("category") or "Other",
                "messages": rest,
            })
    return {"tables": rows, "dataset_checks": dataset_checks}


def category_summary(rule_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-category pass/fail counts for the last run, in rego category order."""
    buckets: Dict[str, Dict[str, int]] = {}
    for r in rule_results:
        cat = r.get("category") or "Other"
        b = buckets.setdefault(cat, {"passed": 0, "failed": 0})
        b["passed" if r.get("passed") else "failed"] += 1
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    return [
        {"category": cat, "status": "fail" if b["failed"] else "pass", **b}
        for cat, b in sorted(buckets.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))
    ]


def _run_history(db: Session, dataset_id: str, limit: int) -> List[Dict[str, Any]]:
    """Recent data_certification checks for this data set, newest first.

    One row per sentinel run: a run that evaluated the data set in several
    workspaces fails if any of them failed, with the union of failing checks.
    """
    rows = (
        db.query(SentinelFindingModel, RequestModel.created_at)
        .join(RequestModel, RequestModel.id == SentinelFindingModel.request_id)
        .filter(
            SentinelFindingModel.resource_id == dataset_id,
            SentinelFindingModel.kind == "check",
            SentinelFindingModel.resource_type == "data_product",
            SentinelFindingModel.policy == "data_certification",
        )
        .order_by(RequestModel.created_at.desc())
        .limit(limit * 4)  # headroom for multi-workspace runs, trimmed below
        .all()
    )

    runs: Dict[str, Dict[str, Any]] = {}
    for finding, run_at in rows:
        data = finding.data or {}
        run = runs.get(finding.request_id)
        if run is None:
            if len(runs) >= limit:
                continue
            run = runs[finding.request_id] = {
                "request_id": finding.request_id,
                "run_at": run_at,
                "total_count": 0,
                "_failed": {},
            }
        results = data.get("rule_results") or []
        run["total_count"] = max(run["total_count"], len(results))
        for r in results:
            if not r.get("passed"):
                run["_failed"][r.get("id")] = {
                    "id": r.get("id"),
                    "description": r.get("description"),
                    "category": r.get("category") or "Other",
                }

    out = []
    for run in runs.values():
        failed = run.pop("_failed")
        run["failed_checks"] = sorted(failed.values(), key=lambda r: r["id"] or "")
        run["failed_count"] = len(failed)
        run["passed"] = not failed
        out.append(run)
    out.sort(key=lambda r: r["run_at"] or datetime.min, reverse=True)
    return out


def run_changes(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Runs where the set of failing checks changed, newest first.

    ``runs`` is newest-first (as returned by the history query). The oldest run
    in the window is reported as ``kind="first"`` so the timeline has a starting
    point even when nothing changed since.
    """
    changes: List[Dict[str, Any]] = []
    prev: Optional[Dict[str, Dict[str, Any]]] = None
    for run in reversed(runs):
        current = {c["id"]: c for c in run["failed_checks"]}
        if prev is None:
            changes.append({
                "kind": "first",
                "request_id": run["request_id"],
                "run_at": run["run_at"],
                "failed_count": run["failed_count"],
                "newly_failing": list(current.values()),
                "resolved": [],
            })
        else:
            newly = [c for k, c in current.items() if k not in prev]
            resolved = [c for k, c in prev.items() if k not in current]
            if newly or resolved:
                changes.append({
                    "kind": "change",
                    "request_id": run["request_id"],
                    "run_at": run["run_at"],
                    "failed_count": run["failed_count"],
                    "newly_failing": newly,
                    "resolved": resolved,
                })
        prev = current
    changes.reverse()
    return changes


def build_certification_detail(
    db: Session, dataset_id: str, history_runs: int = DEFAULT_HISTORY_RUNS
) -> Optional[Dict[str, Any]]:
    """Everything the certification drawer shows for ``dataset_id``.

    Returns ``None`` when the data set has neither a contract nor an asset row.
    """
    versions = (
        db.query(DataContractModel)
        .filter(DataContractModel.dataset_id == dataset_id)
        .order_by(DataContractModel.version.desc())
        .all()
    )
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
    if not versions and not asset:
        return None

    active = next((v for v in versions if v.is_active), versions[0] if versions else None)
    overview = contract_overview(active.yaml_content if active else None)

    rule_results = _json(asset.certification_rule_results) if asset else None
    rule_results = rule_results if isinstance(rule_results, list) else []
    dq = _json(asset.data_quality) if asset else None
    dq = dq if isinstance(dq, dict) else {}
    dq_failed = dq.get("failed_rules") if isinstance(dq.get("failed_rules"), list) else []
    snapshot = _json(asset.certification_assets) if asset else None
    snapshot = snapshot if isinstance(snapshot, list) else None
    violations = _json(asset.certification_violations) if asset else None

    # Contracted tables first (contract order). With no parseable contract,
    # fall back to whatever the last scan saw.
    tables = list(overview["tables"])
    if not tables and snapshot:
        tables = [s["name"] for s in snapshot if s.get("name")]
    outcomes = table_outcomes(tables, rule_results, dq_failed, snapshot)

    runs = _run_history(db, dataset_id, history_runs)

    return {
        "dataset_id": dataset_id,
        "name": (asset.table_name if asset and asset.table_name else dataset_id),
        "certified": bool(asset.certified) if asset else False,
        "last_policy_run": asset.last_synced_at if asset and rule_results else None,
        "overview": overview,
        "categories": category_summary(rule_results),
        "tables": outcomes["tables"],
        "dataset_checks": outcomes["dataset_checks"],
        "rule_results": rule_results,
        "certification_violations": violations if isinstance(violations, list) else None,
        "data_quality": {
            "failed_rule_count": dq.get("failed_rule_count"),
            "failed_rules": dq_failed,
        },
        "contract": {
            "version": active.version if active else None,
            "created_at": active.created_at if active else None,
            "created_by": active.created_by if active else None,
        },
        "history": {
            "runs": runs,
            "changes": run_changes(runs),
            "contract_versions": [
                {
                    "version": v.version,
                    "created_at": v.created_at,
                    "created_by": v.created_by,
                    "is_active": bool(v.is_active),
                }
                for v in versions
            ],
        },
    }
