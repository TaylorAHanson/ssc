"""
Enforcement Sentinel — discovery + remediation engine for the V2 workflow.

This is the real governance pipeline that the ``sentinel_discover`` /
``sentinel_enforce`` tools (``app/workflows/tools.py``) delegate to. It was
originally a ``python-statemachine`` state machine
(``app/state_machines/enforcement_sentinel/state_machine.py``); that file was
removed in the V2 (LangGraph) refactor and the tools were left as stubs that
always reported "0 violations". This module restores the behaviour, adapted to
the V2 model:

  * The graph sequences ``discover -> enforce -> notify``; each tool gets the
    originating request id injected by the executor (``_request_id``) and
    persists its results onto ``RequestModel.state_context`` so the existing
    Enforcement Sentinel UI (which reads ``state_context.violations`` /
    ``.checks``) renders the compliance report — the V2 poller does not copy
    graph state back to ``state_context``.

Pipeline:
  1. discover  — instantiate the per-resource-type handlers, list every
                 resource in the target workspace, evaluate each against all
                 OPA (.rego) policies in a single namespace call per resource,
                 and record both PASS and VIOLATION checks.
  2. enforce   — for each violation, resolve a remediation step from
                 (mode, severity, action) and call the typed handler
                 (warn / kill / certify / uncertify), writing an audit row.
"""

import asyncio
import glob
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from app.core.config import settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def _gather_bounded(factories: List[Callable[[], Awaitable[_T]]], limit: int) -> List[_T]:
    """Run coroutine *factories* concurrently, at most ``limit`` in flight.

    Each item is a zero-arg callable returning a coroutine (a factory, not a
    coroutine, so nothing starts until the semaphore admits it). Results are
    returned in input order. Bounds fan-out so a large workspace doesn't spawn
    an unbounded number of OPA subprocesses / SDK calls at once.
    """
    if limit <= 1:
        return [await factory() for factory in factories]
    sem = asyncio.Semaphore(limit)

    async def _run(factory: Callable[[], Awaitable[_T]]) -> _T:
        async with sem:
            return await factory()

    return await asyncio.gather(*[_run(f) for f in factories])


# ---------------------------------------------------------------------------
# Severity-based gating (ported from the deleted ``remediation.py``)
# ---------------------------------------------------------------------------
NON_REMEDIATION_ACTIONS = frozenset(
    {"SKIPPED_ALLOWLIST", "PENDING_EXCEPTION", "ALLOW", "KEEP_UNCERTIFIED", "KEEP_CERTIFIED"}
)

# High-impact automated changes; the MEDIUM tier blocks these (warn instead).
DESTRUCTIVE_ACTIONS = frozenset(
    {"KILL", "DROP", "SUSPEND", "REVOKE_ADMIN", "ARCHIVE", "ARCHIVE_FLAG", "STOP_AND_RECONFIGURE"}
)


def normalize_severity(raw: Any) -> str:
    """Normalize OPA severity to a known tier; missing values default to HIGH (fail-safe).

    Severity tiers are HIGH / MEDIUM / LOW / NONE. The former ``CRITICAL`` tier was
    collapsed into HIGH (it drove no distinct behavior once destructive actions
    became manual-only); we still map any stray ``CRITICAL`` from old audit rows or
    yet-unmigrated policies up to HIGH defensively.
    """
    if raw is None or raw == "":
        return "HIGH"
    s = str(raw).strip().upper()
    if s == "CRITICAL":
        return "HIGH"
    if s in {"HIGH", "MEDIUM", "LOW", "NONE"}:
        return s
    logger.warning("Unknown severity %r from policy; treating as HIGH", raw)
    return "HIGH"


def determine_intended_step(severity_raw: Any, action: str) -> str:
    """What action *should* be taken based on severity + action string, ignoring mode."""
    severity = normalize_severity(severity_raw)
    if action in NON_REMEDIATION_ACTIONS:
        return "skip"
    if action == "WARN":
        return "warn"
    if action == "CERTIFY":
        return "certify"
    if action == "UNCERTIFY":
        return "uncertify"
    if severity in {"NONE", "LOW"}:
        return "warn"
    if severity == "MEDIUM" and action in DESTRUCTIVE_ACTIONS:
        return "warn"
    if action == "KILL" and severity == "HIGH":
        return "kill"
    # HIGH non-KILL actions (PAUSE, DROP, …): no typed handler yet — notify owner.
    if severity == "HIGH":
        return "warn"
    if severity == "MEDIUM":
        return "warn"
    return "skip"


def resolve_automated_step(severity_raw: Any, action: str) -> str:
    """Decide what the automated enforcement phase actually executes for one violation.

    Returns one of: ``skip``, ``warn``, ``certify``, ``uncertify``.

    There is no enforcement *mode* and no dry-run: the sentinel never performs
    destructive actions automatically. Safe, reversible actions (certify,
    uncertify, warn) execute on every run; a destructive intent (``kill``) is
    downgraded to ``warn`` so the owner is notified while the destructive action
    remains available only via a human "Review & Act". The true, un-downgraded
    intent is still recorded as ``intended_action`` (see :func:`determine_intended_step`)
    so the manual escalation and audit trail stay accurate.
    """
    intended = determine_intended_step(severity_raw, action)
    if intended == "kill":
        return "warn"
    return intended


def warn_prefix(severity: str, action: str) -> str:
    """Short prefix for demoted or unmapped remediation warnings."""
    return f"[{normalize_severity(severity)}/{action}]"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _all_handler_classes():
    from app.providers.databricks.handlers import (
        AppResourceHandler,
        ClusterResourceHandler,
        DashboardResourceHandler,
        DatasetResourceHandler,
        GenieSpaceResourceHandler,
        JobResourceHandler,
        LakebaseResourceHandler,
        NotebookResourceHandler,
        ServicePrincipalResourceHandler,
        SqlWarehouseResourceHandler,
        VolumeResourceHandler,
    )

    return [
        AppResourceHandler,
        ClusterResourceHandler,
        JobResourceHandler,
        SqlWarehouseResourceHandler,
        DashboardResourceHandler,
        GenieSpaceResourceHandler,
        LakebaseResourceHandler,
        ServicePrincipalResourceHandler,
        NotebookResourceHandler,
        VolumeResourceHandler,
        DatasetResourceHandler,
    ]


def _handlers_by_type(workspace_client) -> Dict[str, Any]:
    from app.providers.databricks.handlers import (
        AppResourceHandler,
        ClusterResourceHandler,
        DashboardResourceHandler,
        DatasetResourceHandler,
        GenieSpaceResourceHandler,
        JobResourceHandler,
        LakebaseResourceHandler,
        NotebookResourceHandler,
        ServicePrincipalResourceHandler,
        SqlWarehouseResourceHandler,
        VolumeResourceHandler,
    )

    return {
        "app": AppResourceHandler(workspace_client),
        "cluster": ClusterResourceHandler(workspace_client),
        "job": JobResourceHandler(workspace_client),
        "sql_warehouse": SqlWarehouseResourceHandler(workspace_client),
        "dashboard": DashboardResourceHandler(workspace_client),
        "genie_space": GenieSpaceResourceHandler(workspace_client),
        "lakebase": LakebaseResourceHandler(workspace_client),
        "service_principal": ServicePrincipalResourceHandler(workspace_client),
        "notebook": NotebookResourceHandler(workspace_client),
        "storage": VolumeResourceHandler(workspace_client),
        "table": DatasetResourceHandler(workspace_client),
        "data_product": DatasetResourceHandler(workspace_client),
    }


def _new_workspace_client():
    """Build a Databricks workspace client from the configured service principal."""
    from app.providers.databricks.client import DatabricksProvider

    provider = DatabricksProvider(
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
    )
    return provider.client


def _persist_state_context(db, request, updates: Dict[str, Any]) -> None:
    """Merge ``updates`` into the request's ``state_context`` and commit.

    The V2 poller treats graph state as checkpointer-owned and never copies it
    back to the request row, so the sentinel tools persist their output here
    directly — that is what the Enforcement Sentinel report UI reads.
    """
    ctx = dict(request.state_context or {})
    ctx.update(updates)
    request.state_context = ctx
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(request, "state_context")
    db.add(request)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to persist state_context: %s", e)
        db.rollback()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
async def run_discovery(db, request) -> Dict[str, Any]:
    """Discover workspace resources and evaluate them against OPA policies.

    Persists ``violations`` + ``checks`` onto ``request.state_context`` and
    returns a result dict (also written to graph context via ``writes_context``)
    containing a human-readable ``summary`` for the notify step.
    """
    from app.db.allowlist import AllowlistModel

    state_ctx = request.state_context or {}
    workspace_name = state_ctx.get("workspace", "ws-enterprise-prod")
    environment = state_ctx.get("environment", "prod")
    dataset_id = state_ctx.get("dataset_id")
    requested_policies = state_ctx.get("policies", []) or []
    workspace_type = "enterprise" if "enterprise" in workspace_name else "domain"

    # 1. Allowlist context (exceptions that suppress violations).
    allowlist_records: List[Dict[str, Any]] = []
    try:
        for entry in (
            db.query(AllowlistModel).filter(AllowlistModel.workspace == workspace_name).all()
        ):
            allowlist_records.append(
                {
                    "resource_id": entry.resource_id,
                    "status": entry.status,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                    "justification": entry.justification,
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Sentinel: failed to load allowlist for %s: %s", workspace_name, e)

    # 2. Build the workspace client (fail-soft: a missing/blocked workspace is an
    #    environmental condition, not a sentinel bug — record an empty scan).
    try:
        workspace_client = _new_workspace_client()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to initialize DatabricksProvider: %s", e)
        summary = (
            f"Sentinel scan of '{workspace_name}' could not start: unable to reach "
            f"Databricks ({e}). No resources were evaluated."
        )
        _persist_state_context(
            db, request, {"violations": [], "checks": [], "scan_error": str(e)}
        )
        return {
            "violations": [],
            "checks": [],
            "violation_count": 0,
            "pass_count": 0,
            "total_resources_scanned": 0,
            "summary": summary,
        }

    # 3. Discover resources. A single dataset request scopes to the dataset
    #    handler; otherwise every resource type is scanned.
    from app.providers.databricks.handlers import DatasetResourceHandler

    if dataset_id:
        handler_classes = [DatasetResourceHandler]
    else:
        handler_classes = _all_handler_classes()

    # Discover each resource type concurrently. The handler ``.discover()``
    # methods are async but wrap *blocking* SDK calls (no internal to_thread), so
    # awaiting them on the event loop would serialize on the first blocking call.
    # We offload each to a worker thread (running its own loop) so the network
    # I/O genuinely overlaps; concurrency is bounded by SENTINEL_SCAN_CONCURRENCY.
    limit = max(1, settings.SENTINEL_SCAN_CONCURRENCY)

    def _discover_one(handler_class):
        try:
            return list(asyncio.run(handler_class(workspace_client).discover()) or [])
        except Exception as e:  # noqa: BLE001 - one handler failing shouldn't abort the scan
            logger.warning("Sentinel: %s.discover() failed: %s", handler_class.__name__, e)
            return []

    handler_results = await _gather_bounded(
        [(lambda hc=hc: asyncio.to_thread(_discover_one, hc)) for hc in handler_classes],
        limit,
    )

    discovered_resources: List[Dict[str, Any]] = []
    for handler_class, resources in zip(handler_classes, handler_results):
        if dataset_id and handler_class is DatasetResourceHandler:
            resources = [
                r
                for r in resources
                if r.get("dataset_id") == dataset_id or r.get("id") == dataset_id
            ]
        discovered_resources.extend(resources)

    # Single timestamp for the whole run so every product's "Last Policy Run"
    # equals the run's date on the Sentinel page (the request's created_at),
    # rather than a per-product datetime.utcnow() that drifts by the discovery +
    # evaluation duration and looks mismatched across the two views.
    scan_time = getattr(request, "created_at", None) or datetime.utcnow()

    _refresh_data_asset_quality(db, discovered_resources, scan_time)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to commit DataAsset quality updates: %s", e)
        db.rollback()

    # 4. Evaluate every resource against the policy namespace.
    from app.providers.opa.client import OpaProvider

    opa_provider = OpaProvider(settings.opa_provider_config())

    policy_files = glob.glob(os.path.join("policies", "*.rego"))
    all_policy_names = {os.path.basename(p).replace(".rego", "") for p in policy_files}
    if requested_policies:
        policy_files = [p for p in policy_files if any(req in p for req in requested_policies)]
        allowed_policy_names = {os.path.basename(p).replace(".rego", "") for p in policy_files}
    else:
        allowed_policy_names = all_policy_names

    violations: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []

    def _input_for(resource: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "workspace": {"name": workspace_name, "type": workspace_type, "environment": environment},
            "resource": resource,
            "request_time": datetime.now(timezone.utc).isoformat(),
            "allowlist_records": allowlist_records,
        }

    # Phase 1: evaluate every resource against the policy namespace concurrently
    # (bounded). With a remote OPA server these are genuinely-async HTTP calls,
    # so they overlap; with the local binary the semaphore caps concurrent
    # subprocess spawns. We collect results, then process them serially below so
    # DB writes never interleave (the only await is the OPA call).
    async def _eval(resource: Dict[str, Any]):
        input_data = _input_for(resource)
        try:
            return resource, input_data, await opa_provider.evaluate_namespace(input_data)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Sentinel: OPA evaluation failed for resource %s: %s", resource.get("id"), e
            )
            return resource, input_data, None

    eval_results = await _gather_bounded(
        [(lambda r=r: _eval(r)) for r in discovered_resources], limit
    )

    # Phase 2: process results serially (build checks/violations, write DB cache).
    for resource, input_data, namespace_results in eval_results:
        if namespace_results is None:
            continue
        for policy_name, result in namespace_results.items():
            if policy_name not in allowed_policy_names:
                continue

            # Persist certification results for data products BEFORE the vacuous-pass
            # skip below. Otherwise a first scan that returns a clean/empty result
            # leaves certification_violations NULL, which the UI reads as "never
            # scanned" (status "awaiting") — the initial run then appears to
            # under-report. Recording here marks it scanned (empty == no violations).
            if policy_name == "data_certification" and resource.get("type") == "data_product":
                _record_certification_violations(db, resource, result, scan_time)

            rule_results_raw = result.get("rule_results", []) or []
            if not rule_results_raw and not result.get("is_violation"):
                # No applicable rules for this resource (e.g. compute_and_jobs vs a
                # data_product). Skip the vacuous PASS so we don't bloat the report.
                continue

            is_violation = result.get("is_violation")
            action = result.get("action", "KILL")

            logger.info(
                "POLICY_CHECK_EVALUATED: Policy=%s | Result=%s | Action=%s | "
                "ResourceType=%s | ResourceID=%s | Severity=%s",
                policy_name,
                "VIOLATION" if is_violation else "PASS",
                action,
                resource.get("type"),
                resource.get("id"),
                result.get("severity", "N/A"),
            )

            resource_snapshot = {
                "name": (
                    resource.get("name")
                    or resource.get("title")
                    or resource.get("table_name")
                    or resource.get("id")
                ),
                "description": resource.get("description"),
                "tags": resource.get("tags"),
                "owner": resource.get("owner"),
                "catalog": resource.get("catalog"),
                "schema": resource.get("schema"),
                "policies": resource.get("policies"),
            }
            resource_snapshot = {k: v for k, v in resource_snapshot.items() if v not in (None, "", [])}

            rule_results_sorted = sorted(
                rule_results_raw, key=lambda r: (bool(r.get("passed")), r.get("id", ""))
            )

            checks.append(
                {
                    "resource_id": resource.get("id"),
                    "resource_type": resource.get("type"),
                    "resource": resource_snapshot,
                    "policy": policy_name,
                    "result": "VIOLATION" if is_violation else "PASS",
                    "action": action,
                    "reason": result.get("reason", "" if not is_violation else "Unknown violation"),
                    "violation_reasons": result.get("violation_reasons", []),
                    "rule_results": rule_results_sorted,
                    "severity": result.get("severity", "N/A"),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
            )

            if is_violation or action in ["CERTIFY", "UNCERTIFY"]:
                violation_record = {
                    "resource_id": resource.get("id"),
                    "resource_type": resource.get("type"),
                    "policy": policy_name,
                    "action": action,
                    "reason": result.get(
                        "reason", "Action triggered" if not is_violation else "Unknown violation"
                    ),
                    "violation_reasons": result.get("violation_reasons", []),
                    "severity": result.get("severity", "HIGH"),
                    "input_context": input_data,
                }
                violations.append(violation_record)
                if is_violation:
                    logger.warning(
                        "POLICY_VIOLATION_DETECTED: Policy=%s | Action=%s | ResourceType=%s | "
                        "ResourceID=%s | Reason=%s | Severity=%s",
                        policy_name,
                        action,
                        violation_record["resource_type"],
                        violation_record["resource_id"],
                        violation_record["reason"],
                        violation_record["severity"],
                    )

    # Count per individual policy rule, falling back to one unit per evaluation
    # for policies that don't emit per-rule results yet.
    def _rule_outcomes(check: Dict[str, Any]) -> List[bool]:
        rr = check.get("rule_results") or []
        if rr:
            return [bool(r.get("passed")) for r in rr]
        return [check["result"] == "PASS"]

    rule_outcomes = [passed for c in checks for passed in _rule_outcomes(c)]
    pass_count = sum(1 for ok in rule_outcomes if ok)
    violation_count = sum(1 for ok in rule_outcomes if not ok)

    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to commit certification updates: %s", e)
        db.rollback()

    summary = (
        f"Sentinel scan of '{workspace_name}' ({environment}): scanned "
        f"{len(discovered_resources)} resource(s) across {len(policy_files)} policy file(s); "
        f"{len(rule_outcomes)} checks ({pass_count} passed, {violation_count} failed). "
        f"{len(violations)} policy violation(s) recorded."
    )

    _persist_state_context(
        db,
        request,
        {
            "violations": violations,
            "checks": checks,
            "summary": summary,
            "scan_stats": {
                "violation_count": violation_count,
                "pass_count": pass_count,
                "total_resources_scanned": len(discovered_resources),
                "policies_evaluated": len(policy_files),
                "total_checks": len(rule_outcomes),
                "total_evaluations": len(checks),
            },
        },
    )

    return {
        "violations": violations,
        "checks": checks,
        "violation_count": violation_count,
        "pass_count": pass_count,
        "total_resources_scanned": len(discovered_resources),
        "summary": summary,
    }


def _refresh_data_asset_quality(db, discovered_resources: List[Dict[str, Any]], scan_time: datetime) -> None:
    """Mirror data-product DQ rollups into the local DataAsset cache for the UI.

    ``scan_time`` is a single timestamp for the whole run (the sentinel request's
    created_at) so every product's "Last Policy Run" matches the run's date shown
    on the Enforcement Sentinel page, instead of drifting by the per-product
    processing time.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.data_asset import DataAssetModel

    for resource in discovered_resources:
        if resource.get("type") != "data_product" or "dataset_id" not in resource:
            continue
        dataset_id = resource.get("dataset_id")
        asset = db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
        if not asset:
            asset = DataAssetModel(
                id=dataset_id,
                catalog="Multiple datasets",
                schema="",
                table_name=dataset_id,
                type="DATA_PRODUCT",
                description=f"Data contract for {dataset_id}",
                domain="unknown",
                contract_url=f"/governance/certification?dataset={dataset_id}",
            )
            db.add(asset)
            db.flush()

        dq = dict(asset.data_quality or {})
        assets = resource.get("assets", []) or []
        total_failed = sum(a.get("failed_rule_count", 0) for a in assets if a.get("failed_rule_count", -1) >= 0)
        if any(a.get("failed_rule_count", 0) < 0 for a in assets):
            total_failed = -1
        dq["failed_rule_count"] = total_failed
        aggregated_failed_rules: List[Any] = []
        for a in assets:
            aggregated_failed_rules.extend(a.get("failed_rules", []) or [])
        dq["failed_rules"] = aggregated_failed_rules
        asset.data_quality = dq
        flag_modified(asset, "data_quality")
        # Roll up the live Unity Catalog certification tag into the cached flag
        # the certification UI reads (DataAsset.certified). A product is
        # certified iff it has backing tables and every one carries
        # system.certification_status=certified — the same source of truth the
        # manual certify action writes. Discovery already fetched these tags, so
        # this is free (no extra Databricks round-trips) and keeps the UI in
        # sync with reality on every scan, not just after a manual "Review and
        # Act".
        if assets:
            asset.certified = all(
                str((a.get("tags") or {}).get("system.certification_status", "")).lower() == "certified"
                for a in assets
            )
        else:
            asset.certified = False
        # The certification UI surfaces this as "Last Policy Run"; a sentinel
        # scan IS a policy evaluation, so bump it here (not just on data sync).
        # Use the shared run timestamp so it matches the Sentinel run's date.
        asset.last_synced_at = scan_time
        db.add(asset)


def _record_certification_violations(db, resource: Dict[str, Any], result: Dict[str, Any], scan_time: datetime) -> None:
    """Update the local DataAsset cache with the latest certification violations."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.data_asset import DataAssetModel

    dataset_id = resource.get("dataset_id", resource.get("id"))
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
    if not asset:
        asset = DataAssetModel(
            id=dataset_id,
            catalog="Multiple datasets",
            schema="",
            table_name=dataset_id,
            type="DATA_PRODUCT",
            description=f"Data contract for {dataset_id}",
            domain="unknown",
            contract_url=f"/governance/certification?dataset={dataset_id}",
        )
        db.add(asset)
        db.flush()
    asset.certification_violations = result.get("violation_reasons", [])
    flag_modified(asset, "certification_violations")
    # Cache the FULL per-rule checklist (pass + fail, with category) so the
    # certification UI can render the exact same checklist the Sentinel shows and
    # the exec report can bucket by category — sorted violations-first, matching
    # how the Sentinel run report orders them.
    rule_results = result.get("rule_results", []) or []
    asset.certification_rule_results = sorted(
        rule_results, key=lambda r: (bool(r.get("passed")), r.get("id", ""))
    )
    flag_modified(asset, "certification_rule_results")
    asset.last_synced_at = scan_time
    db.add(asset)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
async def run_enforcement(db, request) -> Dict[str, Any]:
    """Remediate the violations discovered for this request.

    Reads ``violations`` from ``request.state_context`` (written by
    :func:`run_discovery`), resolves the automated step per violation, calls the
    typed handler, and records an audit row. Returns a summary for the notify step.

    There is no enforcement mode: safe/reversible actions (certify, uncertify,
    warn) execute on every run; destructive intents are downgraded to an owner
    warning and left for manual "Review & Act" (see :func:`resolve_automated_step`).
    """
    from app.db.enforcement_audit import EnforcementAuditModel

    state_ctx = request.state_context or {}
    violations = state_ctx.get("violations", []) or []
    scan_summary = state_ctx.get("summary", "")

    def _combined(enforce_summary: str) -> str:
        return f"{scan_summary}\n\n{enforce_summary}".strip() if scan_summary else enforce_summary

    if not violations:
        summary = _combined("No policy violations required enforcement.")
        _persist_state_context(
            db, request, {"enforcement_actions": [], "summary": summary}
        )
        return {"enforced": True, "actions": [], "executed_count": 0, "summary": summary}

    try:
        workspace_client = _new_workspace_client()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: enforcement could not reach Databricks: %s", e)
        return {
            "enforced": False,
            "actions": [],
            "executed_count": 0,
            "summary": _combined(f"Enforcement skipped: unable to reach Databricks ({e})."),
        }

    handlers = _handlers_by_type(workspace_client)
    actions: List[Dict[str, Any]] = []
    executed_count = 0

    for violation in violations:
        action = violation.get("action", "KILL")
        severity = violation.get("severity", "HIGH")
        resource_type = violation.get("resource_type", "unknown")
        resource_id = violation.get("resource_id")
        handler = handlers.get(resource_type)

        step = resolve_automated_step(severity, action)
        intended = determine_intended_step(severity, action)
        executed_action = step

        try:
            if step == "skip":
                logger.debug(
                    "Enforcement %s: policy=%s resource=%s action=%s severity=%s",
                    step, violation.get("policy"), resource_id, action, normalize_severity(severity),
                )
            elif step == "warn":
                if not handler:
                    executed_action = "error_no_handler"
                    logger.warning("No handler for resource_type=%s; cannot warn", resource_type)
                else:
                    body = violation.get("reason", "")
                    # Destructive intents are downgraded to a warning here; make the
                    # recommended (manual-only) action explicit to the owner.
                    if action != "WARN":
                        body = f"{warn_prefix(severity, action)} {body}".strip()
                    await handler.warn(resource_id, body)
                    executed_count += 1
            elif step == "certify":
                if not handler:
                    executed_action = "error_no_handler"
                elif hasattr(handler, "certify"):
                    await handler.certify(resource_id)
                    executed_count += 1
                else:
                    executed_action = "error_no_handler_method"
            elif step == "uncertify":
                if not handler:
                    executed_action = "error_no_handler"
                elif hasattr(handler, "uncertify"):
                    await handler.uncertify(resource_id)
                    executed_count += 1
                else:
                    executed_action = "error_no_handler_method"
        except Exception as e:  # noqa: BLE001 - one resource's failure shouldn't abort the run
            logger.error("Enforcement action %s failed for %s: %s", step, resource_id, e)
            executed_action = "error_execution"

        actions.append(
            {
                "resource_id": resource_id,
                "resource_type": resource_type,
                "policy": violation.get("policy", "unknown"),
                "severity": normalize_severity(severity),
                "intended_action": intended,
                "executed_action": executed_action,
            }
        )

        try:
            db.add(
                EnforcementAuditModel(
                    id=str(uuid.uuid4()),
                    request_id=request.id,
                    resource_id=resource_id or "unknown",
                    resource_type=resource_type,
                    policy_name=violation.get("policy", "unknown"),
                    severity=normalize_severity(severity),
                    intended_action=intended,
                    executed_action=executed_action,
                    reason=violation.get("reason", ""),
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to log enforcement audit: %s", e)

    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to commit enforcement audit logs: %s", e)
        db.rollback()

    manual_required = sum(1 for a in actions if a["intended_action"] != a["executed_action"])
    enforce_summary = (
        f"Processed {len(violations)} violation(s); executed {executed_count} automated "
        f"action(s) (certify/uncertify/warn)."
        + (
            f" {manual_required} destructive action(s) were downgraded to a warning and "
            "require manual Review & Act."
            if manual_required else " No destructive actions were required."
        )
    )
    summary = _combined(enforce_summary)

    _persist_state_context(
        db, request, {"enforcement_actions": actions, "summary": summary}
    )

    return {
        "enforced": True,
        "actions": actions,
        "executed_count": executed_count,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Notification (governance)
# ---------------------------------------------------------------------------
def _active_violations(state_ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Current-run violations that warrant a governance signal (severity != NONE).

    ``CERTIFY`` outcomes resolve to severity NONE and are intentionally excluded
    (they're good news, not something to alert on)."""
    out = []
    for v in state_ctx.get("violations", []) or []:
        sev = normalize_severity(v.get("severity"))
        if sev == "NONE":
            continue
        out.append({
            "resource_id": v.get("resource_id"),
            "resource_type": v.get("resource_type"),
            "policy": v.get("policy", "unknown"),
            "reason": v.get("reason", ""),
            "severity": sev,
        })
    return out


def _prior_severity(db, request_id: str, resource_id: str, policy_name: str) -> Optional[str]:
    """Normalized severity of the most recent *earlier* audit row for this
    (resource, policy), from a run other than the current one. ``None`` if the
    pair was never recorded before (i.e. it's brand new)."""
    from app.db.enforcement_audit import EnforcementAuditModel

    row = (
        db.query(EnforcementAuditModel)
        .filter(
            EnforcementAuditModel.resource_id == (resource_id or "unknown"),
            EnforcementAuditModel.policy_name == policy_name,
            EnforcementAuditModel.request_id != request_id,
        )
        .order_by(EnforcementAuditModel.created_at.desc())
        .first()
    )
    return normalize_severity(row.severity) if row else None


def _digest_should_emit(db, request) -> bool:
    """Anchored once-per-local-day gate: emit on the first sentinel run at/after
    ``ENFORCEMENT_DIGEST_HOUR_LOCAL`` on a new local calendar day. Cadence-agnostic
    (works whether the sentinel runs daily or every 30 min) with no midnight
    double-send and no drift."""
    from zoneinfo import ZoneInfo
    from sqlalchemy import func as _func
    from app.db import RequestModel
    from app.models.request import RequestType

    try:
        tz = ZoneInfo(getattr(settings, "ENFORCEMENT_DIGEST_TIMEZONE", "America/Los_Angeles"))
    except Exception:  # noqa: BLE001 - bad tz string shouldn't break the run
        tz = ZoneInfo("America/Los_Angeles")
    target_hour = int(getattr(settings, "ENFORCEMENT_DIGEST_HOUR_LOCAL", 7))

    now_local = datetime.now(timezone.utc).astimezone(tz)
    if now_local.hour < target_hour:
        return False

    last_emit = (
        db.query(_func.max(RequestModel.digest_emitted_at))
        .filter(
            RequestModel.type == RequestType.ENFORCEMENT_SENTINEL.value,
            RequestModel.digest_emitted_at.isnot(None),
        )
        .scalar()
    )
    if last_emit is None:
        return True
    # Column is naive UTC; interpret as UTC then compare local calendar dates.
    last_local_date = last_emit.replace(tzinfo=timezone.utc).astimezone(tz).date()
    return last_local_date < now_local.date()


def _violations_table_html(rows: List[Dict[str, Any]]) -> str:
    body = "".join(
        f"<tr><td>{r['severity']}</td><td>{r['policy']}</td>"
        f"<td>{r.get('resource_type','')}</td><td><code>{r.get('resource_id','')}</code></td>"
        f"<td>{r.get('reason','')}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>Severity</th><th>Policy</th><th>Type</th>"
        f"<th>Resource</th><th>Reason</th></tr></thead><tbody>{body}</tbody></table>"
    )


async def run_notify(db, request) -> Dict[str, Any]:
    """Governance notifications for a completed sentinel run.

    Two channels (owners are already warned per-resource during enforcement):
      * **Immediate HIGH** — HIGH-severity violations that are *new this run*
        (severity transition, deduped against the prior run's audit row) email the
        governance group right away. Steady-state HIGHs don't re-fire every scan.
      * **Daily digest** — an anchored once-per-local-day snapshot of all current
        violations, emailed to the governance group.
    """
    from app.providers.notifications.client import NotificationProvider

    state_ctx = request.state_context or {}
    rows = _active_violations(state_ctx)
    recipient = getattr(settings, "GOVERNANCE_EMAIL_GROUP", "") or ""
    if not recipient:
        logger.warning("Sentinel notify: GOVERNANCE_EMAIL_GROUP is unset; skipping governance emails.")
        return {"notified": False, "reason": "no_recipient"}

    notifier = NotificationProvider()
    sent_immediate = 0
    sent_digest = False

    # --- Immediate HIGH (transition-gated) ---
    high_rows = [r for r in rows if r["severity"] == "HIGH"]
    new_high = [
        r for r in high_rows
        if _prior_severity(db, request.id, r["resource_id"], r["policy"]) != "HIGH"
    ]
    if new_high:
        subject = f"[Enforcement] {len(new_high)} new high-severity violation(s)"
        body = (
            f"<p><strong>{len(new_high)}</strong> new high-severity policy violation(s) "
            "were detected in the latest Enforcement Sentinel scan and require attention.</p>"
            + _violations_table_html(new_high)
        )
        try:
            await notifier.send_email(to=recipient, subject=subject, body=body, is_html=True)
            sent_immediate = len(new_high)
        except Exception as e:  # noqa: BLE001 - notification failure shouldn't fail the run
            logger.error("Sentinel notify: immediate HIGH email failed: %s", e)

    # --- Daily digest (anchored) ---
    if _digest_should_emit(db, request):
        if rows:
            body = (
                f"<p>Daily Enforcement Sentinel digest — <strong>{len(rows)}</strong> "
                "active policy violation(s) across the workspace.</p>"
                + _violations_table_html(sorted(rows, key=lambda r: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(r["severity"], 3)))
            )
        else:
            body = "<p>Daily Enforcement Sentinel digest — no active policy violations. All clear.</p>"
        try:
            await notifier.send_email(
                to=recipient, subject="[Enforcement] Daily governance digest", body=body, is_html=True
            )
            request.digest_emitted_at = datetime.utcnow()
            db.commit()
            sent_digest = True
        except Exception as e:  # noqa: BLE001
            logger.error("Sentinel notify: daily digest email failed: %s", e)
            db.rollback()

    logger.info(
        "Sentinel notify: request=%s immediate_high=%d digest_sent=%s active_violations=%d",
        request.id, sent_immediate, sent_digest, len(rows),
    )
    return {"notified": True, "immediate_high": sent_immediate, "digest_sent": sent_digest}
