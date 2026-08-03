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
import functools
import glob
import html
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from app.core.config import settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


# Dedicated thread pool for the sentinel's *blocking* Databricks SDK calls
# (auth probe, resource discovery, OAuth diagnostic). Isolating these from the
# default asyncio ``to_thread`` executor is what makes a longer sentinel per-call
# timeout SAFE: even if several sentinel calls stall for their full timeout, they
# consume threads HERE, never the shared pool that ordinary state-machine
# requests rely on — so a slow/unreachable workspace can't freeze the whole app.
_sentinel_executor: Optional[ThreadPoolExecutor] = None
_sentinel_executor_lock = threading.Lock()


def _get_sentinel_executor() -> ThreadPoolExecutor:
    """Lazily build the sentinel's dedicated thread pool.

    Sized to the scan concurrency plus headroom for the serial probe/diagnostic
    calls, so bounded fan-out (``SENTINEL_SCAN_CONCURRENCY``) never queues behind
    itself. Rebuilt if a prior pool was shut down.
    """
    global _sentinel_executor
    if _sentinel_executor is not None:
        return _sentinel_executor
    with _sentinel_executor_lock:
        if _sentinel_executor is None:
            workers = max(2, int(getattr(settings, "SENTINEL_SCAN_CONCURRENCY", 5) or 5) + 2)
            _sentinel_executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="sentinel-scan"
            )
    return _sentinel_executor


async def _to_sentinel_thread(func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """``asyncio.to_thread`` equivalent that runs on the dedicated sentinel pool."""
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(_get_sentinel_executor(), call)


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

# current policy name -> names it used to be filed under. The multi-resource
# .rego files were split one-per-resource-type, and `monitoring_and_logging` was
# renamed to `resource_tags` to match what it actually contains. Audit rows keep
# whatever name was current when they were written, so alert dedup has to look
# under the old names too. Safe to prune once no audit rows predate the split.
_LEGACY_POLICY_NAMES: Dict[str, List[str]] = {
    "apps": ["apps_and_genie"],
    "genie_spaces": ["apps_and_genie"],
    "compute": ["compute_and_jobs"],
    "jobs": ["compute_and_jobs"],
    "dashboards": ["dashboards_and_sql"],
    "sql_warehouses": ["dashboards_and_sql"],
    "catalog_access": ["data_and_ai_governance"],
    "storage": ["data_and_ai_governance"],
    "personal_access_tokens": ["identity_and_access"],
    "grants": ["identity_and_access"],
    "resource_tags": ["monitoring_and_logging"],
}


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


def _workspace_scoped_handler_classes():
    """Handlers whose resources are workspace-specific (compute, jobs, apps, ...).

    Everything except :class:`DatasetResourceHandler`, which is Unity Catalog
    (metastore) scoped and handled once by the data-certification pass rather
    than per target workspace.

    :class:`NotebookResourceHandler` is excluded by default: its full recursive
    workspace-tree walk is the dominant cost of a scan. It's re-enabled only when
    ``SENTINEL_SCAN_NOTEBOOKS`` is set (Admin -> Settings, no redeploy).
    """
    from app.providers.databricks.handlers import (
        DatasetResourceHandler,
        NotebookResourceHandler,
    )

    handlers = [hc for hc in _all_handler_classes() if hc is not DatasetResourceHandler]
    if not bool(getattr(settings, "SENTINEL_SCAN_NOTEBOOKS", False)):
        handlers = [hc for hc in handlers if hc is not NotebookResourceHandler]
    return handlers


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


async def revalidate_violation(
    *,
    workspace_client,
    host: Optional[str],
    resource_type: str,
    resource_id: str,
    policy_name: str,
) -> Dict[str, Any]:
    """Re-discover ONE resource and re-evaluate a single policy against it.

    Called immediately before a manual *destructive* action (e.g. ``kill``) so a
    user who has already remediated the finding is never punished for a stale
    violation. It mirrors the discovery + OPA-evaluation the scheduled scan does,
    but for the single target resource.

    Returns a dict:
      * ``still_violates`` — ``True`` (confirmed still violating), ``False``
        (fixed / resource gone / suppressed by an allowlist entry), or ``None``
        (could not determine — no handler, discovery error, or the policy
        produced no result). Callers should treat ``None`` as "do not proceed".
      * ``found`` — whether the resource was located in the workspace.
      * ``reason`` / ``violation_reasons`` — the fresh policy result, for display.
      * ``detail`` — human-readable context (esp. for the ``None`` case).
    """
    from app.providers.opa.client import OpaProvider

    handler = _handlers_by_type(workspace_client).get(resource_type)
    if handler is None:
        return {
            "still_violates": None, "found": False, "reason": "no_handler",
            "violation_reasons": [],
            "detail": f"No handler is registered for resource_type '{resource_type}'.",
        }

    # Re-discover the CURRENT resources of this type and locate the target.
    try:
        resources = list(await handler.discover() or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("Re-validation discovery failed for %s/%s: %s", resource_type, resource_id, e)
        return {
            "still_violates": None, "found": False, "reason": "discovery_failed",
            "violation_reasons": [],
            "detail": f"Could not re-scan the workspace to re-validate: {e}",
        }

    match = next(
        (r for r in resources if r.get("id") == resource_id or r.get("dataset_id") == resource_id),
        None,
    )
    if match is None:
        # Gone from the workspace → there is nothing left to remediate.
        return {
            "still_violates": False, "found": False, "reason": "resource_not_found",
            "violation_reasons": [],
            "detail": "The resource no longer exists in the workspace.",
        }

    # Resolve workspace context (name/type/environment) exactly as the scan does,
    # so the .rego policies see the same input shape.
    ws_name = ws_env = None
    if host:
        try:
            from app.core.workspaces import get_workspace_config

            cfg = get_workspace_config(host)
            if cfg is not None:
                ws_name, ws_env = cfg.name, cfg.environment
        except Exception:  # noqa: BLE001
            pass
    opa_ws = {
        "name": ws_name,
        "type": "enterprise" if "enterprise" in (ws_name or "") else "domain",
        "environment": ws_env,
    }

    # An active allowlist entry for this workspace must suppress the violation
    # here too, so a granted exception doesn't get killed manually.
    allowlist_records: List[Dict[str, Any]] = []
    try:
        from app.db.allowlist import AllowlistModel
        from app.db.session import get_lakebase_session

        _db = get_lakebase_session()
        try:
            for entry in _db.query(AllowlistModel).filter(AllowlistModel.workspace == ws_name).all():
                allowlist_records.append({
                    "resource_id": entry.resource_id,
                    "status": entry.status,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                    "justification": entry.justification,
                })
        finally:
            _db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Re-validation could not load allowlist for %s: %s", ws_name, e)

    input_data = {
        "workspace": opa_ws,
        "resource": match,
        "request_time": datetime.now(timezone.utc).isoformat(),
        "allowlist_records": allowlist_records,
    }
    try:
        opa_provider = OpaProvider(settings.opa_provider_config())
        namespace_results = await opa_provider.evaluate_namespace(input_data)
    except Exception as e:  # noqa: BLE001
        logger.warning("Re-validation OPA evaluation failed for %s: %s", resource_id, e)
        return {
            "still_violates": None, "found": True, "reason": "evaluation_failed",
            "violation_reasons": [],
            "detail": f"Could not evaluate policy '{policy_name}': {e}",
        }

    result = (namespace_results or {}).get(policy_name)
    if result is None:
        return {
            "still_violates": None, "found": True, "reason": "policy_not_evaluated",
            "violation_reasons": [],
            "detail": f"Policy '{policy_name}' produced no result for this resource.",
        }

    return {
        "still_violates": bool(result.get("is_violation")),
        "found": True,
        "reason": result.get("reason", ""),
        "violation_reasons": result.get("violation_reasons", []),
        "detail": "",
    }


def _new_workspace_client(host: Optional[str] = None):
    """Build a Databricks workspace client for a target workspace.

    When ``host`` is given, resolve that target workspace's credentials via
    :func:`app.core.workspaces.get_workspace_config` (install-wide secret scope +
    the workspace's inline SP key names, with a fall-back to the app's own SP).
    When ``host`` is omitted, build from the app's own service principal — the
    app's home workspace (historical behavior).
    """
    from app.providers.databricks.client import DatabricksProvider

    # Sentinel clients get a longer per-call HTTP timeout than the rest of the app
    # (remote/locked-down workspaces can be slow), which is safe because sentinel
    # discovery runs on its own dedicated thread pool (see _to_sentinel_thread) so
    # a slow call can't starve the shared pool used by ordinary requests.
    sentinel_http_timeout = (
        int(getattr(settings, "SENTINEL_SDK_HTTP_TIMEOUT_SECONDS", 0) or 0) or None
    )

    if host:
        from app.core.workspaces import get_workspace_config

        cfg = get_workspace_config(host)
        if cfg is not None:
            provider = DatabricksProvider(
                host=cfg.host,
                token=cfg.token,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                http_timeout_seconds=sentinel_http_timeout,
            )
            return provider.client

    provider = DatabricksProvider(
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
        http_timeout_seconds=sentinel_http_timeout,
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
    # Keep the compact list-view projection in lockstep with the full context so
    # the Sentinel list never has to load the (huge) state_context column.
    from app.services.state_summary import summarize_state_context

    request.state_summary = summarize_state_context(ctx)
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(request, "state_context")
    flag_modified(request, "state_summary")
    db.add(request)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("Sentinel: failed to persist state_context: %s", e)
        db.rollback()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _rule_outcomes(check: Dict[str, Any]) -> List[bool]:
    """Per-rule pass/fail outcomes for a check, falling back to one unit per
    evaluation for policies that don't emit per-rule results yet."""
    rr = check.get("rule_results") or []
    if rr:
        return [bool(r.get("passed")) for r in rr]
    return [check["result"] == "PASS"]


def aggregate_check_counts(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll a run's checks up into the numbers the scan report shows.

    Everything here is counted in ONE unit — the individual policy *rule* — so
    the report's severity cards sum exactly to its violation total. A check is a
    (resource, policy) evaluation that can fail several rules at once; counting
    failures per rule but severity per check is what used to make HIGH+MEDIUM+LOW
    come out lower than the headline number.

    Failed rules on a resource with an approved allowlist exception are signed-off
    risk rather than an open finding, so they're reported as ``exempt_count`` and
    left out of both the total and the severity breakdown.

    Invariants: ``total_checks == pass_count + violation_count + exempt_count``
    and ``sum(severity_counts.values()) == violation_count``.
    """
    total_checks = 0
    pass_count = 0
    violation_count = 0
    exempt_count = 0
    severity_counts: Dict[str, int] = {}

    for check in checks or []:
        outcomes = _rule_outcomes(check)
        failed = sum(1 for ok in outcomes if not ok)
        total_checks += len(outcomes)
        pass_count += len(outcomes) - failed
        if not failed:
            continue
        if check.get("action") == "SKIPPED_ALLOWLIST":
            exempt_count += failed
            continue
        violation_count += failed
        sev = normalize_severity(check.get("severity"))
        severity_counts[sev] = severity_counts.get(sev, 0) + failed

    return {
        "total_checks": total_checks,
        "pass_count": pass_count,
        "violation_count": violation_count,
        "exempt_count": exempt_count,
        "severity_counts": severity_counts,
    }


# Human-readable phrasing for each failure category, used in the workspace-level
# ERROR log + run summary so a 0-result scan is never ambiguous.
_CATEGORY_HELP = {
    "authentication": "authentication/credentials",
    "authorization": "permissions",
    "network": "network/connectivity",
    "rate_limited": "rate limiting",
    "not_found": "missing API/endpoint",
    "unknown": "unclassified",
}


def _classify_databricks_error(exc: Exception) -> str:
    """Best-effort, definitive-as-possible classification of a discovery failure.

    Lets the logs state *why* a scan came back empty (bad credentials vs missing
    permissions vs a network problem) instead of leaving "0 resources"
    ambiguous. Returns one of: ``authentication``, ``authorization``,
    ``network``, ``rate_limited``, ``not_found``, ``unknown``. Matches on both
    the SDK exception type name and the message text (the Databricks SDK often
    raises OAuth/`invalid_client` failures as generic errors, so text matching is
    the reliable signal). Diagnostic only — never drives control flow.
    """
    msg = str(exc).lower()
    type_name = type(exc).__name__

    # Network / connectivity — the request never reached a Databricks auth check.
    network_markers = (
        "connection", "timed out", "timeout", "getaddrinfo", "temporary failure",
        "name or service not known", "name resolution", "max retries", "ssl",
        "certificate", "connection refused", "connection reset",
        "network is unreachable", "no route to host", "failed to establish",
    )
    if type_name in {
        "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout",
        "NewConnectionError", "MaxRetryError", "SSLError", "ProxyError",
    } or any(m in msg for m in network_markers):
        return "network"

    # Authentication — identity itself was rejected (bad/unknown SP, OAuth fail).
    auth_markers = (
        "invalid_client", "unauthenticated", "authentication failed",
        "invalid access token", "invalid_grant", "token request failed",
        "could not resolve credentials", "default auth", "cannot configure default",
        "invalid credentials", "401",
    )
    if type_name in {"Unauthenticated"} or any(m in msg for m in auth_markers):
        return "authentication"

    # Authorization — identity is valid but lacks permission on the resource.
    authz_markers = (
        "permission denied", "does not have", "not authorized", "forbidden",
        "insufficient", "is not allowed", "access denied", "403",
    )
    if type_name in {"PermissionDenied"} or any(m in msg for m in authz_markers):
        return "authorization"

    if type_name in {"TooManyRequests"} or "429" in msg or "too many requests" in msg:
        return "rate_limited"

    if type_name in {"NotFound"} or "404" in msg:
        return "not_found"

    return "unknown"


def _summarize_discovery_failures(
    ws_name: Optional[str],
    ws_host: Optional[str],
    ws_cred_source: Optional[str],
    discover_errors: List[Dict[str, Any]],
    attempted: int,
) -> Optional[Dict[str, Any]]:
    """Log + summarize a workspace's discovery failures.

    Critically distinguishes a genuinely EMPTY workspace (no errors, 0 resources)
    from one that FAILED to scan (auth/permission/network). When every handler
    failed the workspace's "0 findings" is meaningless, so we log an ERROR that
    names the definitive cause and return a structured record for the run summary
    / UI. ``None`` when nothing failed.
    """
    if not discover_errors:
        return None
    from collections import Counter

    cats = Counter(e["category"] for e in discover_errors)
    dominant = cats.most_common(1)[0][0]
    failed = len(discover_errors)
    breakdown = dict(cats)
    example = discover_errors[0]["error"]
    record = {
        "workspace": ws_name,
        "host": ws_host,
        "credential_source": ws_cred_source,
        "category": dominant,
        "failed": failed,
        "attempted": attempted,
        "breakdown": breakdown,
        "example": example,
        "partial": not (attempted and failed >= attempted),
    }
    if not record["partial"]:
        logger.error(
            "Sentinel: workspace '%s' returned NO resources — ALL %d discovery "
            "call(s) failed (host=%s, credentials=%s). Definitive cause: %s error. "
            "This is a %s problem, NOT an empty workspace. Breakdown: %s. Example: %s",
            ws_name, attempted, ws_host, ws_cred_source, dominant,
            _CATEGORY_HELP.get(dominant, dominant), breakdown, example,
        )
    else:
        logger.warning(
            "Sentinel: workspace '%s' had %d/%d discovery call(s) fail (host=%s, "
            "credentials=%s); returning PARTIAL results. Dominant cause: %s (%s). "
            "Breakdown: %s. Example: %s",
            ws_name, failed, attempted, ws_host, ws_cred_source, dominant,
            _CATEGORY_HELP.get(dominant, dominant), breakdown, example,
        )
    return record


def _oauth_token_diagnostic(
    host: Optional[str],
    client_id: Optional[str],
    client_secret: Optional[str],
    timeout: float = 10.0,
) -> Optional[Dict[str, Any]]:
    """Exercise the OAuth M2M client-credentials flow directly against a
    workspace's OIDC token endpoint to capture the PRECISE error the Databricks
    SDK hides behind a generic ``invalid_client``.

    Returns ``{ok, status, error, error_description}`` (or ``None`` when there
    aren't SP creds to test). Uses only the stdlib (no new deps) and NEVER logs
    or returns the secret. The ``error_description`` distinguishes e.g. an
    unknown/wrong client_id from a bad secret, which is what turns "creds
    rejected" into an actionable message.
    """
    if not (host and client_id and client_secret):
        return None
    import base64 as _b64
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    url = host.rstrip("/") + "/oidc/v1/token"
    data = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "all-apis"}
    ).encode()
    basic = _b64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            body = _json.loads(resp.read().decode() or "{}")
        if body.get("access_token"):
            return {"ok": True, "status": getattr(resp, "status", 200)}
        return {"ok": False, "status": getattr(resp, "status", None),
                "error": body.get("error"), "error_description": body.get("error_description")}
    except urllib.error.HTTPError as e:
        try:
            body = _json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        return {"ok": False, "status": e.code,
                "error": body.get("error"), "error_description": body.get("error_description")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": "request_failed",
                "error_description": str(e)}


async def _probe_workspace_auth(
    workspace_client,
    ws_name: Optional[str],
    *,
    host: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Cheap auth + reachability probe for one workspace (``current_user.me()``).

    This is the AUTHORITATIVE signal for whether a workspace's 0-result is real
    or a failure: the resource handlers swallow their own discovery exceptions
    (log + ``return []``), so an ``invalid_client`` / permission / network error
    never reaches the scan loop. One canonical authenticated call surfaces it and
    — like ``ping_workspaces`` — separates the two things people conflate:

      * ``network_reachable`` — did we reach the control plane at all? A 401/403
        (rejected creds) STILL proves the network path is open; only a
        timeout/DNS/connection failure means it isn't.
      * ``ok`` — did the resolved credentials actually authenticate?

    When the SDK call fails on auth and SP creds (``host``/``client_id``/
    ``client_secret``) are supplied, we additionally run a direct OIDC token
    exchange to capture the RAW OAuth ``error``/``error_description`` — turning a
    generic ``invalid_client`` into "client not found" vs "invalid secret" vs
    "valid client but not entitled on this workspace".
    """
    try:
        me = await asyncio.wait_for(
            _to_sentinel_thread(workspace_client.current_user.me), timeout=timeout
        )
        identity = getattr(me, "user_name", None) or getattr(me, "display_name", None)
        return {"ok": True, "network_reachable": True, "category": None,
                "identity": identity, "detail": "authenticated"}
    except asyncio.TimeoutError:
        return {"ok": False, "network_reachable": False, "category": "network",
                "identity": None,
                "detail": f"current_user.me() timed out after {timeout:.0f}s (no control-plane response)"}
    except Exception as e:  # noqa: BLE001
        category = _classify_databricks_error(e)
        # Anything the control plane answered (auth/permission/rate/not_found)
        # proves the network path is open; only a 'network' category did not.
        network_reachable = category != "network"
        result: Dict[str, Any] = {
            "ok": False, "network_reachable": network_reachable,
            "category": category, "identity": None, "detail": str(e),
        }
        # Enrich auth-class failures with the precise OAuth reason.
        if network_reachable and host and client_id and client_secret:
            diag = await _to_sentinel_thread(
                _oauth_token_diagnostic, host, client_id, client_secret, timeout
            )
            if diag is not None and diag.get("ok"):
                # OAuth itself SUCCEEDS but the API call failed -> the SP is a
                # valid OAuth client that simply isn't entitled on this workspace.
                result["category"] = "authorization"
                result["oauth_ok"] = True
                result["detail"] = (
                    "OAuth token exchange SUCCEEDED but the workspace API call was "
                    "rejected — the service principal authenticates but is not "
                    "entitled on this workspace. Add/authorize the SP on this "
                    "workspace (Account console -> workspace assignment / permissions)."
                )
            elif diag is not None:
                result["oauth_ok"] = False
                result["oauth_error"] = diag.get("error")
                result["oauth_error_description"] = diag.get("error_description")
                result["oauth_status"] = diag.get("status")
                desc = diag.get("error_description") or diag.get("error") or "no detail"
                result["detail"] = (
                    f"OAuth token request rejected (HTTP {diag.get('status')}): "
                    f"{diag.get('error')} — {desc}"
                )
        return result


async def _scan_and_evaluate(
    *,
    db,
    opa_provider,
    allowed_policy_names,
    workspace_ctx: Dict[str, Any],
    workspace_client,
    handler_classes,
    dataset_id: Optional[str],
    scan_time: datetime,
    limit: int,
    record_certification: bool,
) -> tuple:
    """Discover + OPA-evaluate one workspace's resources.

    Returns ``(violations, checks, resource_count, ws_failure)`` with every
    record tagged with ``workspace_ctx`` (``{name, host, environment,
    credential_source}``). ``ws_failure`` is ``None`` when discovery succeeded, or
    a structured record describing why the workspace failed (auth / permission /
    network) so a 0-result scan can be distinguished from a genuinely empty one.
    When ``record_certification`` is set (the single data-certification pass),
    data product results are mirrored into the local DataAsset cache.
    """
    from app.db.allowlist import AllowlistModel
    from app.providers.databricks.handlers import DatasetResourceHandler

    ws_name = workspace_ctx.get("name")
    ws_host = workspace_ctx.get("host")
    ws_env = workspace_ctx.get("environment")
    ws_cred_source = workspace_ctx.get("credential_source")

    # Resolve the raw SP credentials LOCALLY (never persisted / never added to
    # workspace_ctx) so the probe can run a direct OIDC token exchange and report
    # the precise OAuth error when auth fails.
    probe_cid: Optional[str] = None
    probe_csec: Optional[str] = None
    try:
        if ws_host:
            from app.core.workspaces import get_workspace_config

            _cfg = get_workspace_config(ws_host)
            if _cfg is not None:
                probe_cid, probe_csec = _cfg.client_id, _cfg.client_secret
        if not probe_cid:
            probe_cid = settings.DATABRICKS_CLIENT_ID or None
            probe_csec = settings.DATABRICKS_CLIENT_SECRET or None
    except Exception:  # noqa: BLE001 - diagnostic creds are best-effort
        probe_cid = probe_csec = None

    # Auth/reachability gate. The per-resource handlers swallow discovery errors
    # (log + return []), so without this probe a workspace whose credentials are
    # rejected reports a misleading "0 findings". A failed probe is authoritative:
    # short-circuit with a structured failure that names the definitive cause
    # (authentication vs permissions vs network) so 0 is never read as "clean".
    logger.info("Sentinel: workspace '%s' authenticating (host=%s)...", ws_name, ws_host)
    probe = await _probe_workspace_auth(
        workspace_client, ws_name,
        host=ws_host or (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL),
        client_id=probe_cid, client_secret=probe_csec,
    )
    if not probe["ok"]:
        attempted = len(handler_classes)
        logger.error(
            "Sentinel: workspace '%s' auth/connectivity probe FAILED "
            "(host=%s, credentials=%s). Definitive cause: %s error; "
            "network_reachable=%s. %s Detail: %s. Skipping scan — a 0 here is a "
            "FAILURE, NOT a clean bill of health.",
            ws_name, ws_host, ws_cred_source, probe["category"],
            probe["network_reachable"],
            ("The control plane responded but rejected the credentials, so the "
             "network path is open and this is a credentials/permissions problem."
             if probe["network_reachable"]
             else "The control plane was never reached, so this is a network/"
             "connectivity problem (DNS / VPC peering / PrivateLink / firewall), "
             "not a credentials problem."),
            probe.get("detail"),
        )
        ws_failure = {
            "workspace": ws_name,
            "host": ws_host,
            "credential_source": ws_cred_source,
            "category": probe["category"],
            "network_reachable": probe["network_reachable"],
            "failed": attempted,
            "attempted": attempted,
            "breakdown": {probe["category"]: attempted},
            "example": probe["detail"],
            # Precise OAuth reason from the direct token exchange (no secrets).
            "oauth_error": probe.get("oauth_error"),
            "oauth_error_description": probe.get("oauth_error_description"),
            "oauth_status": probe.get("oauth_status"),
            "partial": False,
            "stage": "auth_probe",
        }
        return [], [], 0, ws_failure
    if probe.get("identity"):
        logger.info(
            "Sentinel: workspace '%s' authenticated as %s (host=%s, credentials=%s).",
            ws_name, probe["identity"], ws_host, ws_cred_source,
        )

    ws_type = "enterprise" if "enterprise" in (ws_name or "") else "domain"
    # Tag stored on every check/violation so the report + email can show which
    # workspace a finding came from. The OPA input keeps its historical shape
    # (name/type/environment) that the .rego policies read.
    ws_tag = {"name": ws_name, "host": ws_host, "environment": ws_env}
    opa_ws = {"name": ws_name, "type": ws_type, "environment": ws_env}

    # Allowlist context for THIS workspace (exceptions that suppress violations).
    # Use a SHORT-LIVED session, never the long-held ``db`` passed in: that
    # connection sits idle for the minutes of discovery/eval below and Lakebase
    # drops it (OAuth token expiry / idle SSL close), so reading through it here
    # raised "SSL connection has been closed unexpectedly". A fresh session gets a
    # live, pre-pinged connection, does the tiny read, and is closed immediately.
    allowlist_records: List[Dict[str, Any]] = []
    from app.db.session import get_lakebase_session

    _al_db = get_lakebase_session()
    try:
        for entry in (
            _al_db.query(AllowlistModel).filter(AllowlistModel.workspace == ws_name).all()
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
        logger.warning("Sentinel: failed to load allowlist for %s: %s", ws_name, e)
    finally:
        _al_db.close()

    # Discover each resource type concurrently. The handler ``.discover()``
    # methods are async but wrap *blocking* SDK calls (no internal to_thread), so
    # awaiting them on the event loop would serialize on the first blocking call.
    # We offload each to a worker thread (running its own loop) so the network
    # I/O genuinely overlaps; concurrency is bounded by SENTINEL_SCAN_CONCURRENCY.
    def _discover_one(handler_class):
        # Returns (resources, error_or_None, elapsed_seconds). Per-handler timing
        # is what lets us see which handler dominates a workspace's discovery so
        # we optimize the right one instead of guessing.
        _h_start = datetime.utcnow()
        try:
            resources = list(asyncio.run(handler_class(workspace_client).discover()) or [])
            return resources, None, (datetime.utcnow() - _h_start).total_seconds()
        except Exception as e:  # noqa: BLE001 - one handler failing shouldn't abort the scan
            category = _classify_databricks_error(e)
            # Per-handler WARNING tagged with the definitive category (auth vs
            # permission vs network) so a 0-result run is diagnosable from logs
            # alone, tied to the exact SP/credential source used.
            logger.warning(
                "Sentinel: %s.discover() failed for workspace '%s' [%s] "
                "(host=%s, credentials=%s): %s",
                handler_class.__name__, ws_name, category, ws_host, ws_cred_source, e,
            )
            return (
                [],
                {"handler": handler_class.__name__, "category": category, "error": str(e)},
                (datetime.utcnow() - _h_start).total_seconds(),
            )

    logger.info(
        "Sentinel: workspace '%s' discovering resources across %d handler(s)...",
        ws_name, len(handler_classes),
    )
    _disc_start = datetime.utcnow()
    handler_results = await _gather_bounded(
        [(lambda hc=hc: _to_sentinel_thread(_discover_one, hc)) for hc in handler_classes],
        limit,
    )

    discovered_resources: List[Dict[str, Any]] = []
    discover_errors: List[Dict[str, Any]] = []
    per_handler_counts: List[str] = []
    for handler_class, (resources, err, elapsed) in zip(handler_classes, handler_results):
        if err:
            discover_errors.append(err)
        if dataset_id and handler_class is DatasetResourceHandler:
            resources = [
                r
                for r in resources
                if r.get("dataset_id") == dataset_id or r.get("id") == dataset_id
            ]
        per_handler_counts.append(f"{handler_class.__name__}={len(resources)} in {elapsed:.1f}s")
        discovered_resources.extend(resources)

    logger.info(
        "Sentinel: workspace '%s' discovered %d resource(s) in %.1fs [%s]; "
        "evaluating against %d policy file(s)...",
        ws_name, len(discovered_resources),
        (datetime.utcnow() - _disc_start).total_seconds(),
        ", ".join(per_handler_counts), len(allowed_policy_names),
    )

    ws_failure = _summarize_discovery_failures(
        ws_name, ws_host, ws_cred_source, discover_errors, len(handler_classes)
    )

    # Backstop: auth succeeded (we got past the probe) and no handler errored, yet
    # nothing came back. Confirm it's a genuinely empty workspace so a 0 is never
    # left ambiguous in the logs.
    if not discovered_resources and not discover_errors:
        logger.info(
            "Sentinel: workspace '%s' authenticated and returned 0 resources with NO "
            "handler errors across %d handler(s) — treating as genuinely empty "
            "(host=%s, credentials=%s).",
            ws_name, len(handler_classes), ws_host, ws_cred_source,
        )

    # Certification writes to the local DataAsset cache are DEFERRED to a single
    # short-lived session AFTER the eval loop (see below). Writing them here on
    # the long-held ``db`` — then leaving that connection idle through discovery
    # and evaluation — is exactly what dropped the SSL connection on long runs.
    # We collect the per-resource certification results during the loop instead.
    cert_records: List[tuple] = []

    def _input_for(resource: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "workspace": opa_ws,
            "resource": resource,
            "request_time": datetime.now(timezone.utc).isoformat(),
            "allowlist_records": allowlist_records,
        }

    async def _eval(resource: Dict[str, Any]):
        input_data = _input_for(resource)
        try:
            return resource, input_data, await opa_provider.evaluate_namespace(input_data)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Sentinel: OPA evaluation failed for resource %s: %s", resource.get("id"), e
            )
            return resource, input_data, None

    _eval_start = datetime.utcnow()
    eval_results = await _gather_bounded(
        [(lambda r=r: _eval(r)) for r in discovered_resources], limit
    )
    if discovered_resources:
        logger.info(
            "Sentinel: workspace '%s' evaluated %d resource(s) in %.1fs.",
            ws_name, len(discovered_resources),
            (datetime.utcnow() - _eval_start).total_seconds(),
        )

    violations: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []

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
            if (
                record_certification
                and policy_name == "data_certification"
                and resource.get("type") == "data_product"
            ):
                cert_records.append((resource, result))

            rule_results_raw = result.get("rule_results", []) or []
            if not rule_results_raw and not result.get("is_violation"):
                # No applicable rules for this resource (e.g. the compute policy vs a
                # data_product). Skip the vacuous PASS so we don't bloat the report.
                continue

            is_violation = result.get("is_violation")
            action = result.get("action", "KILL")

            logger.info(
                "POLICY_CHECK_EVALUATED: Workspace=%s | Policy=%s | Result=%s | Action=%s | "
                "ResourceType=%s | ResourceID=%s | Severity=%s",
                ws_name,
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
                    "workspace": ws_tag,
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
                    "owner": resource.get("owner"),
                    "workspace": ws_tag,
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
                        "POLICY_VIOLATION_DETECTED: Workspace=%s | Policy=%s | Action=%s | "
                        "ResourceType=%s | ResourceID=%s | Reason=%s | Severity=%s",
                        ws_name,
                        policy_name,
                        action,
                        violation_record["resource_type"],
                        violation_record["resource_id"],
                        violation_record["reason"],
                        violation_record["severity"],
                    )

    # Certification cache writes happen HERE, on ONE fresh short-lived session —
    # never the long-held ``db``, which has been idle through discovery + eval and
    # may be a dead (SSL-closed) connection by now. This is the only place a cert
    # pass updates DataAsset rows, so the connection is checked out just for the
    # brief batch of upserts and closed immediately. Non-cert workspace scans make
    # no DB writes at all, so they skip this entirely.
    if record_certification and (discovered_resources or cert_records):
        from app.db.session import get_lakebase_session

        cert_db = get_lakebase_session()
        try:
            _refresh_data_asset_quality(cert_db, discovered_resources, scan_time)
            for _res, _result in cert_records:
                _record_certification_violations(cert_db, _res, _result, scan_time)
            cert_db.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("Sentinel: failed to persist certification cache updates: %s", e)
            cert_db.rollback()
        finally:
            cert_db.close()

    return violations, checks, len(discovered_resources), ws_failure


async def _scan_and_evaluate_guarded(**kwargs) -> tuple:
    """``_scan_and_evaluate`` with a per-workspace wall-clock cap.

    A single workspace's discovery makes blocking Databricks calls (auth probe,
    resource listing, OPA eval). Without a cap, one slow/unreachable workspace —
    or a huge recursive listing — hangs the entire run indefinitely with no
    further logs. This enforces ``SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS`` and
    raises ``asyncio.TimeoutError`` on breach so the caller records a structured
    failure and moves on. ``timeout <= 0`` disables the cap (old behavior).
    """
    timeout = getattr(settings, "SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS", 0)
    if timeout and timeout > 0:
        return await asyncio.wait_for(_scan_and_evaluate(**kwargs), timeout=timeout)
    return await _scan_and_evaluate(**kwargs)


def _timeout_failure(ws_name, ws_host, ws_cred_source, timeout_s, stage) -> Dict[str, Any]:
    """Structured ws_failure record for a workspace that blew its scan timeout."""
    return {
        "workspace": ws_name,
        "host": ws_host,
        "credential_source": ws_cred_source,
        "category": "timeout",
        "failed": 1,
        "attempted": 1,
        "breakdown": {"timeout": 1},
        "example": f"Workspace scan exceeded {timeout_s}s and was abandoned.",
        "partial": False,
        "stage": stage,
    }


async def run_discovery(db, request) -> Dict[str, Any]:
    """Discover resources across all target workspaces and evaluate OPA policies.

    A single run scans every configured target workspace for workspace-scoped
    resources (compute, jobs, apps, ...) and runs the Unity-Catalog-scoped data
    certification pass ONCE against the configured certification workspace
    (``SENTINEL_DATA_CERT_WORKSPACE``; blank = the app's home workspace). All
    violations/checks are aggregated into one master list on
    ``request.state_context`` — each tagged with its ``workspace`` — so the
    report and the governance email stay a single, cross-workspace view.
    """
    from app.core.workspaces import get_target_workspaces
    from app.providers.databricks.handlers import DatasetResourceHandler
    from app.providers.opa.client import OpaProvider

    # Capture the id up front (a plain string) so the final persistence never has
    # to touch the possibly-expired ``request`` instance: intermediate commits
    # during the scan expire it, and a multi-minute scan can outlive its DB
    # connection / have its row purged — either turns a later ``request.id`` /
    # ``request.state_context`` access into an ObjectDeletedError or SSL error.
    request_id = request.id
    state_ctx = request.state_context or {}
    dataset_id = state_ctx.get("dataset_id")
    requested_policies = state_ctx.get("policies", []) or []
    # Optional subset of workspaces to scan (names or hosts). Empty = all
    # configured target workspaces.
    requested_workspaces = state_ctx.get("workspaces") or []

    # Single timestamp for the whole run so every product's "Last Policy Run"
    # equals the run's date on the Sentinel page (the request's created_at).
    scan_time = getattr(request, "created_at", None) or datetime.utcnow()

    # Resolve the workspace set. get_target_workspaces() already falls back to
    # the app's home workspace when none are configured. This reads per-workspace
    # SP secrets (SDK calls), so it can take a moment — log first so 'discovering'
    # isn't silent while credentials resolve.
    logger.info("Sentinel: resolving target workspaces and credentials...")
    all_ws = get_target_workspaces()
    if requested_workspaces:
        req = {str(x) for x in requested_workspaces}
        scan_ws = [w for w in all_ws if w.name in req or w.host in req]
        if not scan_ws:
            logger.warning(
                "Sentinel: requested workspaces %s matched none; scanning all.", req
            )
            scan_ws = list(all_ws)
    else:
        scan_ws = list(all_ws)

    # Consolidated pre-scan diagnostic: one line per workspace showing exactly
    # which credentials it resolved to (dedicated SP vs the app's own SP) BEFORE
    # any scanning, so a single run tells the whole story. 'global_default' here
    # means this workspace fell back to the app's own SP — see the workspaces.py
    # WARNING for the precise reason (missing scope / key name / unreadable secret).
    logger.info(
        "Sentinel: preparing to scan %d workspace(s). Credential resolution: %s",
        len(scan_ws),
        [
            {"name": w.name, "host": w.host, "credentials": w.credential_source}
            for w in scan_ws
        ],
    )
    fallbacks = [w.name for w in scan_ws if w.credential_source == "global_default"]
    if fallbacks:
        logger.warning(
            "Sentinel: %d workspace(s) resolved to the app's OWN service principal "
            "(global_default): %s. If any of these are meant to use a dedicated SP, "
            "auth there will fail — fix the secret scope / key names under "
            "Admin -> Settings -> Target Workspaces.",
            len(fallbacks), fallbacks,
        )

    # The catalog/metastore-scoped data certification pass runs once. It uses the
    # configured certification workspace's client if set, else the app's home SP.
    home_host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    home_match = next((w for w in all_ws if w.host == home_host), None)
    cert_name = (getattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "") or "").strip()
    cert_cfg = None
    if cert_name:
        cert_cfg = next((w for w in all_ws if w.name == cert_name or w.host == cert_name), None)
        if cert_cfg is None:
            logger.warning(
                "Sentinel: SENTINEL_DATA_CERT_WORKSPACE=%r not found among target "
                "workspaces; using the home workspace for data certification.",
                cert_name,
            )
    if cert_cfg is not None:
        cert_ctx = {
            "name": cert_cfg.name,
            "host": cert_cfg.host,
            "environment": cert_cfg.environment,
            "credential_source": cert_cfg.credential_source,
        }
        cert_host: Optional[str] = cert_cfg.host
    else:
        cert_ctx = {
            "name": home_match.name if home_match else "home",
            "host": home_host,
            "environment": home_match.environment if home_match else settings.ENVIRONMENT,
            "credential_source": home_match.credential_source if home_match else "global_default",
        }
        cert_host = None  # -> _new_workspace_client(None) uses the app's own SP

    # Policy set + OPA provider (shared across every workspace scan).
    opa_provider = OpaProvider(settings.opa_provider_config())
    policy_files = glob.glob(os.path.join("policies", "*.rego"))
    all_policy_names = {os.path.basename(p).replace(".rego", "") for p in policy_files}
    if requested_policies:
        policy_files = [p for p in policy_files if any(req in p for req in requested_policies)]
        allowed_policy_names = {os.path.basename(p).replace(".rego", "") for p in policy_files}
    else:
        allowed_policy_names = all_policy_names

    limit = max(1, settings.SENTINEL_SCAN_CONCURRENCY)

    violations: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    total_resources = 0
    scan_errors: List[str] = []
    # Structured per-workspace discovery failures (auth/permission/network) so a
    # 0-result run surfaces WHY, and isn't mistaken for "all clear".
    ws_failures: List[Dict[str, Any]] = []

    # 1. Workspace-scoped scans (skipped for a single-dataset certification request).
    if not dataset_id:
        ws_handler_classes = _workspace_scoped_handler_classes()
        ws_timeout = getattr(settings, "SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS", 0)
        total_ws = len(scan_ws)
        ws_limit = max(1, int(getattr(settings, "SENTINEL_WORKSPACE_CONCURRENCY", 1) or 1))

        async def _scan_one_workspace(idx: int, w) -> Dict[str, Any]:
            """Scan ONE workspace and return aggregatable result parts.

            Never raises: a single workspace failing / timing out / being
            unreachable must not abort the whole run — which matters even more now
            that workspaces run concurrently (an uncaught raise would fail the
            entire ``gather`` batch, losing every other workspace's results too).
            """
            out: Dict[str, Any] = {
                "violations": [], "checks": [], "resources": 0,
                "ws_failures": [], "scan_errors": [],
            }
            ws_ctx = {
                "name": w.name,
                "host": w.host,
                "environment": w.environment,
                "credential_source": w.credential_source,
            }
            try:
                client = _new_workspace_client(w.host)
            except Exception as e:  # noqa: BLE001 - one unreachable workspace shouldn't abort the run
                category = _classify_databricks_error(e)
                logger.error(
                    "Sentinel: failed to init client for workspace '%s' [%s] "
                    "(host=%s, credentials=%s): %s",
                    w.name, category, w.host, w.credential_source, e,
                )
                out["scan_errors"].append(f"{w.name} ({category}): {e}")
                out["ws_failures"].append({
                    "workspace": w.name, "host": w.host,
                    "credential_source": w.credential_source, "category": category,
                    "failed": 1, "attempted": 1, "breakdown": {category: 1},
                    "example": str(e), "partial": False, "stage": "client_init",
                })
                return out
            logger.info(
                "Sentinel: scanning workspace '%s' (%d/%d, host=%s)...",
                w.name, idx, total_ws, w.host,
            )
            _ws_start = datetime.utcnow()
            try:
                v, c, n, wf = await _scan_and_evaluate_guarded(
                    db=db,
                    opa_provider=opa_provider,
                    allowed_policy_names=allowed_policy_names,
                    workspace_ctx=ws_ctx,
                    workspace_client=client,
                    handler_classes=ws_handler_classes,
                    dataset_id=None,
                    scan_time=scan_time,
                    limit=limit,
                    record_certification=False,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Sentinel: workspace '%s' scan exceeded %ss and was abandoned "
                    "(host=%s, credentials=%s); moving on. A 0 here is a TIMEOUT, "
                    "not a clean result.",
                    w.name, ws_timeout, w.host, w.credential_source,
                )
                out["scan_errors"].append(f"{w.name} (timeout): exceeded {ws_timeout}s")
                out["ws_failures"].append(
                    _timeout_failure(w.name, w.host, w.credential_source, ws_timeout, "workspace_scan")
                )
                return out
            except Exception as e:  # noqa: BLE001 - defensive: isolate a workspace's failure
                category = _classify_databricks_error(e)
                logger.error(
                    "Sentinel: workspace '%s' scan failed [%s] (host=%s, credentials=%s): %s",
                    w.name, category, w.host, w.credential_source, e, exc_info=True,
                )
                out["scan_errors"].append(f"{w.name} ({category}): {e}")
                out["ws_failures"].append({
                    "workspace": w.name, "host": w.host,
                    "credential_source": w.credential_source, "category": category,
                    "failed": 1, "attempted": 1, "breakdown": {category: 1},
                    "example": str(e), "partial": False, "stage": "scan",
                })
                return out
            _elapsed = (datetime.utcnow() - _ws_start).total_seconds()
            logger.info(
                "Sentinel: finished workspace '%s' (%d/%d): %d resource(s) in %.1fs.",
                w.name, idx, total_ws, n, _elapsed,
            )
            out["violations"] = v
            out["checks"] = c
            out["resources"] = n
            if wf:
                out["ws_failures"].append(wf)
            return out

        if ws_limit > 1 and total_ws > 1:
            logger.info(
                "Sentinel: scanning %d workspace(s), up to %d concurrently.",
                total_ws, ws_limit,
            )
        else:
            logger.info("Sentinel: scanning %d workspace(s) serially.", total_ws)

        ws_results = await _gather_bounded(
            [(lambda i=i, w=w: _scan_one_workspace(i, w)) for i, w in enumerate(scan_ws, start=1)],
            ws_limit,
        )
        for r in ws_results:
            violations += r["violations"]
            checks += r["checks"]
            total_resources += r["resources"]
            scan_errors.extend(r["scan_errors"])
            ws_failures.extend(r["ws_failures"])

    # 2. Data certification pass (once, Unity Catalog / metastore scoped).
    try:
        cert_client = _new_workspace_client(cert_host)
        logger.info(
            "Sentinel: running data certification pass (workspace='%s', host=%s)...",
            cert_ctx.get("name"), cert_ctx.get("host"),
        )
        _cert_start = datetime.utcnow()
        try:
            v, c, n, wf = await _scan_and_evaluate_guarded(
                db=db,
                opa_provider=opa_provider,
                allowed_policy_names=allowed_policy_names,
                workspace_ctx=cert_ctx,
                workspace_client=cert_client,
                handler_classes=[DatasetResourceHandler],
                dataset_id=dataset_id,
                scan_time=scan_time,
                limit=limit,
                record_certification=True,
            )
        except asyncio.TimeoutError:
            _cert_timeout = getattr(settings, "SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS", 0)
            logger.error(
                "Sentinel: data certification pass exceeded %ss and was abandoned "
                "(workspace='%s', host=%s); continuing without it.",
                _cert_timeout, cert_ctx.get("name"), cert_ctx.get("host"),
            )
            scan_errors.append(f"data_certification (timeout): exceeded {_cert_timeout}s")
            ws_failures.append(
                _timeout_failure(
                    cert_ctx.get("name"), cert_ctx.get("host"),
                    cert_ctx.get("credential_source"), _cert_timeout, "data_certification",
                )
            )
            v, c, n, wf = [], [], 0, None
        else:
            logger.info(
                "Sentinel: data certification pass finished: %d resource(s) in %.1fs.",
                n, (datetime.utcnow() - _cert_start).total_seconds(),
            )
        violations += v
        checks += c
        total_resources += n
        if wf:
            ws_failures.append(wf)
    except Exception as e:  # noqa: BLE001
        category = _classify_databricks_error(e)
        logger.error("Sentinel: data certification pass failed [%s]: %s", category, e)
        scan_errors.append(f"data_certification ({category}): {e}")

    counts = aggregate_check_counts(checks)
    total_checks = counts["total_checks"]
    pass_count = counts["pass_count"]
    violation_count = counts["violation_count"]
    exempt_count = counts["exempt_count"]
    severity_counts = counts["severity_counts"]

    scanned_names = [w.name for w in scan_ws] if not dataset_id else [cert_ctx["name"]]
    summary = (
        f"Sentinel scan across {len(scanned_names)} workspace(s) "
        f"({', '.join(scanned_names)}): scanned {total_resources} resource(s) across "
        f"{len(policy_files)} policy file(s); {total_checks} checks "
        f"({pass_count} passed, {violation_count} failed). "
        f"{len(violations)} policy violation(s) recorded."
    )
    if exempt_count:
        summary += f" {exempt_count} failed check(s) suppressed by approved exceptions."
    # Workspaces where EVERY discovery call failed produced a meaningless "0" —
    # call that out explicitly so the run isn't read as "all clear".
    full_fail = [f for f in ws_failures if not f.get("partial")]
    partial_fail = [f for f in ws_failures if f.get("partial")]
    if full_fail:
        cats = "/".join(sorted({f["category"] for f in full_fail}))
        names = ", ".join(f["workspace"] for f in full_fail)
        summary += (
            f" \u26a0 {len(full_fail)} workspace(s) returned NO data due to "
            f"{cats} error(s) [{names}] — these are NOT confirmed clean."
        )
    elif scan_errors:
        summary += f" {len(scan_errors)} workspace(s) could not be fully scanned."
    if partial_fail and not full_fail:
        summary += f" {len(partial_fail)} workspace(s) returned partial results."

    # Persistence split: high-level counts/summary go in state_context (small, so
    # the run list stays fast); the full per-record detail (ALL violations + ALL
    # checks — we never drop findings) goes into the joined ``sentinel_findings``
    # table as many small rows. Writing the detail inline as one multi-hundred-MB
    # JSON column was dropping the DB connection ("SSL connection has been closed
    # unexpectedly") on large runs; batched row inserts do not.
    from app.services.sentinel_findings import replace_run_findings

    persist: Dict[str, Any] = {
        "summary": summary,
        "workspaces_scanned": scanned_names,
        "scan_stats": {
            "violation_count": violation_count,
            "pass_count": pass_count,
            "total_resources_scanned": total_resources,
            "policies_evaluated": len(policy_files),
            "total_checks": total_checks,
            "total_evaluations": len(checks),
            "workspaces_scanned": len(scanned_names),
            "workspaces_failed": len(full_fail),
            "workspaces_partial": len(partial_fail),
            # Same unit as violation_count (failed rules), so the UI's
            # HIGH+MEDIUM+LOW cards sum exactly to it.
            "severity_counts": severity_counts,
            # Failed rules suppressed by an approved allowlist exception. Not in
            # violation_count or severity_counts; total_checks == pass_count +
            # violation_count + exempt_count.
            "exempt_count": exempt_count,
            # Detail-record totals (the detail view pages these from the table).
            "violation_records_total": len(violations),
            "check_records_total": len(checks),
        },
    }
    if ws_failures:
        # Structured, non-secret failure records (workspace, host, category,
        # credential source, example error) for the run report / UI.
        persist["workspace_failures"] = ws_failures
    if scan_errors:
        persist["scan_error"] = "; ".join(scan_errors)

    # Persist on a FRESH session, not the one held open across the whole scan.
    # The scan can run for minutes with no DB activity, so the original
    # connection may be dead (Lakebase drops idle/expired-token SSL connections)
    # and the original ``request`` instance is expired by intermediate commits.
    # A fresh session gets a live, pre-pinged connection and a live instance;
    # ``expire_on_commit=False`` keeps the re-fetched row usable across the two
    # commits below. If the run was deleted mid-scan (e.g. an admin purge of a
    # "stuck" run), we detect the missing row and discard results cleanly instead
    # of crashing with ObjectDeletedError (which previously poisoned the session
    # and cascaded into the poller's error path).
    from app.db import RequestModel
    from app.db.session import get_lakebase_session

    write_db = get_lakebase_session()
    write_db.expire_on_commit = False
    try:
        fresh_request = (
            write_db.query(RequestModel).filter(RequestModel.id == request_id).first()
        )
        if fresh_request is None:
            logger.warning(
                "Sentinel: request %s no longer exists (deleted mid-scan); "
                "discarding %d violation(s)/%d check(s) without persisting.",
                request_id, len(violations), len(checks),
            )
        else:
            _persist_state_context(write_db, fresh_request, persist)
            # Persist the full detail into the joined table (committed there).
            replace_run_findings(write_db, request_id, violations=violations, checks=checks)
    finally:
        write_db.close()

    return {
        "violations": violations,
        "checks": checks,
        "violation_count": violation_count,
        "pass_count": pass_count,
        "total_resources_scanned": total_resources,
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

    from app.services.sentinel_findings import load_run_violations

    state_ctx = request.state_context or {}
    # Full detail lives in the joined findings table (not state_context).
    violations = load_run_violations(db, request)
    scan_summary = state_ctx.get("summary", "")

    def _combined(enforce_summary: str) -> str:
        return f"{scan_summary}\n\n{enforce_summary}".strip() if scan_summary else enforce_summary

    if not violations:
        summary = _combined("No policy violations required enforcement.")
        _persist_state_context(
            db,
            request,
            {
                "enforcement_stats": {"processed": 0, "executed_count": 0, "manual_required": 0},
                "summary": summary,
            },
        )
        return {
            "enforced": True,
            "executed_count": 0,
            "manual_required": 0,
            "processed": 0,
            "summary": summary,
        }

    # Idempotent re-run guard. Automated enforcement re-processes the FULL
    # violation set on every pass, and audit rows below are committed in batches
    # (not one final commit), so a retried run — e.g. after a transient mid-run
    # error — could otherwise leave duplicate automated rows from a partial prior
    # pass. Clear this run's *automated* audit rows first; manual "Review & Act"
    # rows (``manual_*``) and revalidation aborts are preserved. Mirrors how
    # ``replace_run_findings`` deletes a run's prior findings before re-inserting.
    try:
        db.query(EnforcementAuditModel).filter(
            EnforcementAuditModel.request_id == request.id,
            ~EnforcementAuditModel.executed_action.like("manual_%"),
            EnforcementAuditModel.executed_action != "aborted_revalidated",
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as e:  # noqa: BLE001 - non-fatal; worst case is duplicate rows
        logger.error(
            "Sentinel: failed to clear prior enforcement audit rows for %s: %s",
            request.id, e,
        )
        db.rollback()

    # Violations can span multiple workspaces (each carries a ``workspace`` tag).
    # Build and cache one typed-handler map per workspace host so each resource is
    # remediated against the workspace it actually lives in. A host that can't be
    # reached yields ``None`` (its violations record ``error_no_handler`` rather
    # than aborting the whole run).
    _handlers_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def _handlers_for(host: Optional[str]) -> Optional[Dict[str, Any]]:
        key = host or "__home__"
        if key not in _handlers_cache:
            try:
                _handlers_cache[key] = _handlers_by_type(_new_workspace_client(host))
            except Exception as e:  # noqa: BLE001
                logger.error("Sentinel: enforcement could not reach workspace %s: %s", key, e)
                _handlers_cache[key] = None
        return _handlers_cache[key]

    # Per-action detail is persisted to the ``enforcement_audit`` table (one row
    # each) — the same source the ``/enforcement-actions`` API reads — so we do
    # NOT accumulate a full per-violation list here or write it back into
    # ``state_context``. A large run (tens of thousands of violations) that wrote
    # that list inline produced a multi-MB single-row JSON UPDATE that dropped the
    # Lakebase connection ("SSL connection has been closed unexpectedly"),
    # poisoning the session and failing the run's terminal status write — the
    # exact failure the ``sentinel_findings`` table was introduced to avoid.
    _AUDIT_COMMIT_BATCH = 1000
    executed_count = 0
    manual_required = 0
    pending = 0

    def _commit_audit_batch() -> None:
        nonlocal pending
        if not pending:
            return
        try:
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("Sentinel: failed to commit enforcement audit batch: %s", e)
            db.rollback()
        pending = 0

    for violation in violations:
        action = violation.get("action", "KILL")
        severity = violation.get("severity", "HIGH")
        resource_type = violation.get("resource_type", "unknown")
        resource_id = violation.get("resource_id")
        ws = violation.get("workspace") or {}
        handlers = _handlers_for(ws.get("host")) or {}
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

        if intended != executed_action:
            manual_required += 1

        try:
            db.add(
                EnforcementAuditModel(
                    id=str(uuid.uuid4()),
                    request_id=request.id,
                    resource_id=resource_id or "unknown",
                    resource_type=resource_type,
                    workspace=ws.get("name"),
                    policy_name=violation.get("policy", "unknown"),
                    severity=normalize_severity(severity),
                    intended_action=intended,
                    executed_action=executed_action,
                    reason=violation.get("reason", ""),
                )
            )
            pending += 1
            # Commit periodically so a huge run is never one giant transaction and
            # the connection isn't left idle for the whole (minutes-long) loop —
            # both of which risk Lakebase dropping the connection mid-run.
            if pending >= _AUDIT_COMMIT_BATCH:
                _commit_audit_batch()
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to log enforcement audit: %s", e)

    _commit_audit_batch()

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

    # Persist only compact counts (never the full per-action list — see above).
    _persist_state_context(
        db,
        request,
        {
            "enforcement_stats": {
                "processed": len(violations),
                "executed_count": executed_count,
                "manual_required": manual_required,
            },
            "summary": summary,
        },
    )

    return {
        "enforced": True,
        "executed_count": executed_count,
        "manual_required": manual_required,
        "processed": len(violations),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Notification (governance)
# ---------------------------------------------------------------------------
def _active_violations(violations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Current-run violations that warrant a governance signal (severity != NONE).

    Takes the full violation list (loaded from the joined findings table).
    ``CERTIFY`` outcomes resolve to severity NONE and are intentionally excluded
    (they're good news, not something to alert on)."""
    out = []
    for v in (violations or []):
        sev = normalize_severity(v.get("severity"))
        if sev == "NONE":
            continue
        # Workspace is captured on newer (multi-workspace) records; fall back to
        # the OPA input snapshot for runs that predate the top-level field.
        ws = v.get("workspace") or (v.get("input_context") or {}).get("workspace") or {}
        _res = (v.get("input_context") or {}).get("resource") or {}
        _name = str(_res.get("name") or _res.get("title") or "").strip()
        out.append({
            "resource_id": v.get("resource_id"),
            # Display name from the discovery snapshot. Blank when it just
            # repeats the id, so the digest doesn't print the same string twice.
            "resource_name": _name if _name != str(v.get("resource_id") or "").strip() else "",
            "resource_type": v.get("resource_type"),
            # Owner is captured on newer records; fall back to the OPA input
            # snapshot for runs that predate the top-level field.
            "owner": v.get("owner") or ((v.get("input_context") or {}).get("resource") or {}).get("owner"),
            "workspace": ws.get("name") or "",
            "policy": v.get("policy", "unknown"),
            "reason": v.get("reason", ""),
            "issue_count": len(v.get("violation_reasons") or []),
            "severity": sev,
        })
    return out


def _policy_name_history(policy_name: str) -> List[str]:
    """Every name a policy has been known by, newest first.

    The policy name is the .rego filename, and it is persisted on every audit
    row. Splitting the multi-resource policies into one file per resource type
    renamed them, which would make every existing finding look brand new to
    ``_prior_severity`` and re-fire the whole backlog as HIGH alerts once.
    Matching the old names too keeps that dedup intact across the rename.
    """
    return [policy_name] + _LEGACY_POLICY_NAMES.get(policy_name, [])


def _prior_severity(
    db, request_id: str, resource_id: str, policy_name: str, workspace: Optional[str] = None
) -> Optional[str]:
    """Normalized severity of the most recent *earlier* audit row for this
    (resource, policy[, workspace]), from a run other than the current one.
    ``None`` if the tuple was never recorded before (i.e. it's brand new).

    ``workspace`` scopes the lookup so the same ``resource_id`` in two different
    workspaces (Databricks job IDs / notebook paths repeat across workspaces) is
    not conflated. It's only applied when provided AND matching rows carry a
    workspace, so legacy audit rows (workspace NULL) still dedup by
    (resource, policy) and don't spuriously re-fire."""
    from sqlalchemy import or_

    from app.db.enforcement_audit import EnforcementAuditModel

    query = db.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.resource_id == (resource_id or "unknown"),
        EnforcementAuditModel.policy_name.in_(_policy_name_history(policy_name)),
        EnforcementAuditModel.request_id != request_id,
    )
    if workspace:
        # Match this workspace, OR legacy rows that predate workspace tagging.
        query = query.filter(
            or_(
                EnforcementAuditModel.workspace == workspace,
                EnforcementAuditModel.workspace.is_(None),
            )
        )
    row = query.order_by(EnforcementAuditModel.created_at.desc()).first()
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


# ---------------------------------------------------------------------------
# Governance email rendering
# ---------------------------------------------------------------------------
# All styles are INLINE (no reliance on <style> blocks) so the layout survives
# Gmail/Outlook/Apple Mail, which strip or ignore head CSS. Tables use
# role="presentation" for layout so screen readers + Outlook behave.
# (label, text color, background, border) per severity tier.
_SEV_META = {
    "HIGH": ("High", "#B91C1C", "#FEE2E2", "#FCA5A5"),
    "MEDIUM": ("Medium", "#B45309", "#FEF3C7", "#FCD34D"),
    "LOW": ("Low", "#334155", "#EEF2F7", "#CBD5E1"),
}
_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _esc(value: Any) -> str:
    """HTML-escape any cell value (policy reasons can contain arbitrary text)."""
    return html.escape("" if value is None else str(value))


def _sev_chip(sev: str) -> str:
    label, fg, bg, border = _SEV_META.get(sev, _SEV_META["LOW"])
    return (
        '<span style="display:inline-block;padding:3px 10px;border-radius:9999px;'
        'font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;'
        f'color:{fg};background:{bg};border:1px solid {border};white-space:nowrap;">{label}</span>'
    )


def _summary_cards_html(rows: List[Dict[str, Any]]) -> str:
    """A row of three stat cards (HIGH / MEDIUM / LOW counts)."""
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in rows:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    cells = []
    for sev in ("HIGH", "MEDIUM", "LOW"):
        label, fg, bg, border = _SEV_META[sev]
        cells.append(
            '<td width="33.33%" style="border:0;padding:5px;vertical-align:top;">'
            f'<div style="background:{bg};border:1px solid {border};border-radius:12px;padding:16px 12px;text-align:center;">'
            f'<div style="font-size:30px;font-weight:800;line-height:1;color:{fg};">{counts[sev]}</div>'
            f'<div style="margin-top:8px;font-size:11px;font-weight:700;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{fg};">{label}</div>'
            '</div></td>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:separate;width:100%;margin:0 0 24px 0;"><tr>'
        + "".join(cells)
        + '</tr></table>'
    )


def _owner_html(owner: Any, *, small: bool = False) -> str:
    """Render the responsible party: an email verbatim, a service-principal id
    behind an "SP" chip, or a muted "Unknown" when we couldn't resolve one."""
    o = ("" if owner is None else str(owner)).strip()
    if not o:
        return '<span style="color:#94a3b8;">Unknown</span>'
    if "@" in o:
        return f'<span style="color:#0f172a;word-break:break-word;">{_esc(o)}</span>'
    chip = (
        '<span style="display:inline-block;font-size:10px;font-weight:700;color:#475569;'
        'background:#e2e8f0;padding:1px 5px;border-radius:4px;margin-right:5px;vertical-align:middle;">SP</span>'
    )
    return (
        f'{chip}<code style="font-size:11px;color:#334155;word-break:break-all;">{_esc(o)}</code>'
    )


def _resource_type_label(resource_type: Any) -> str:
    """Friendly display label for a resource type.

    The engine calls certified data products "data_product"; governance readers
    know these as "Dataset". Everything else is shown as-is (the caller may
    capitalize it)."""
    t = ("" if resource_type is None else str(resource_type)).strip().lower()
    if t == "data_product":
        return "Dataset"
    return "" if resource_type is None else str(resource_type)


def _workspace_html(name: Any) -> str:
    """Render a workspace name as a small muted pill; em-dash when unknown."""
    n = ("" if name is None else str(name)).strip()
    if not n:
        return '<span style="color:#94a3b8;">&mdash;</span>'
    return (
        '<span style="display:inline-block;font-size:11px;font-weight:600;color:#334155;'
        f'background:#eef2f7;padding:2px 8px;border-radius:6px;word-break:break-word;">{_esc(n)}</span>'
    )


def _truncate_reason(text: Any, limit: int = 160) -> str:
    """Collapse whitespace + multi-reason concatenations to a short one-liner.

    Full detail lives in the app; the email only needs the gist so a resource
    with a dozen sub-reasons can't blow up a whole row (or clip the message)."""
    t = " ".join(("" if text is None else str(text)).split())
    if len(t) <= limit:
        return t
    return t[:limit].rstrip(" .,;:") + "\u2026"


def _section_heading(title: str, sub: str = "") -> str:
    return (
        '<div style="margin:30px 0 12px 0;">'
        f'<div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#0f172a;">{title}</div>'
        + (f'<div style="font-size:12px;color:#64748b;margin-top:3px;">{sub}</div>' if sub else "")
        + "</div>"
    )


def _violations_table_html(rows: List[Dict[str, Any]], cap: Optional[int] = None) -> str:
    """Detailed per-violation table (severity, policy, resource, owner, reason).

    Reasons are truncated to a one-liner and the list is capped at ``cap`` with a
    "+N more" footer row, so the table stays compact even with many rows."""
    def _th(text: str, width: str, radius: str = "") -> str:
        return (
            f'<th width="{width}" style="text-align:left;padding:11px 14px;background:#0f172a;color:#e2e8f0;'
            f'font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;border:0;{radius}">'
            f'{text}</th>'
        )

    # Column widths (px). Every column — INCLUDING Reason — gets an explicit width.
    # Outlook's Word engine collapses a fixed-layout column that has no width to
    # near-zero when space is tight (that's the "one character per line" squish),
    # so leaving Reason blank is exactly what broke it there.
    col_w = {"sev": 66, "ws": 86, "policy": 92, "res": 132, "owner": 112, "reason": 224}
    header = (
        "<tr>"
        + _th("Severity", str(col_w["sev"]), "border-top-left-radius:10px;")
        + _th("Workspace", str(col_w["ws"]))
        + _th("Policy", str(col_w["policy"]))
        + _th("Resource", str(col_w["res"]))
        + _th("Owner", str(col_w["owner"]))
        + _th("Reason", str(col_w["reason"]), "border-top-right-radius:10px;")
        + "</tr>"
    )

    # A <colgroup> with explicit widths is the most reliable way to pin column
    # widths in Outlook/Word (it honors these even when it ignores CSS on cells).
    colgroup = "<colgroup>" + "".join(
        f'<col width="{w}" style="width:{w}px;">'
        for w in (col_w["sev"], col_w["ws"], col_w["policy"], col_w["res"], col_w["owner"], col_w["reason"])
    ) + "</colgroup>"

    total = len(rows)
    shown = rows[:cap] if cap else rows

    # table-layout:fixed keeps columns at their declared widths so text WRAPS
    # inside the cell instead of stretching the table past the email frame.
    body_rows = []
    for i, r in enumerate(shown):
        bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
        cell = "padding:13px 14px;border:0;border-bottom:1px solid #eef2f7;vertical-align:top;word-break:break-word;overflow-wrap:anywhere;"
        issue_count = int(r.get("issue_count") or 0)
        issue_badge = (
            f'<span style="display:inline-block;margin-left:6px;font-size:10px;font-weight:700;color:#B91C1C;'
            f'background:#FEE2E2;padding:1px 6px;border-radius:9999px;white-space:nowrap;">{issue_count} issues</span>'
            if issue_count > 1 else ""
        )
        body_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="{cell}">{_sev_chip(r["severity"])}</td>'
            f'<td style="{cell}font-size:12px;color:#334155;">{_workspace_html(r.get("workspace"))}</td>'
            f'<td style="{cell}font-size:13px;color:#0f172a;font-weight:600;">{_esc(r.get("policy"))}</td>'
            f'<td style="{cell}font-size:12px;color:#334155;">'
            f'<div style="font-weight:600;color:#64748b;text-transform:capitalize;margin-bottom:3px;">{_esc(_resource_type_label(r.get("resource_type")))}</div>'
            + (
                f'<div style="font-size:13px;color:#0f172a;font-weight:600;margin-bottom:3px;">{_esc(r.get("resource_name"))}</div>'
                if r.get("resource_name") else ""
            )
            + f'<code style="font-size:12px;color:#0f172a;background:#f1f5f9;padding:2px 6px;border-radius:4px;word-break:break-all;">{_esc(r.get("resource_id"))}</code>'
            '</td>'
            f'<td style="{cell}font-size:12px;color:#334155;">{_owner_html(r.get("owner"))}</td>'
            f'<td style="{cell}font-size:13px;color:#475569;line-height:1.5;">{_esc(_truncate_reason(r.get("reason")))}{issue_badge}</td>'
            "</tr>"
        )

    if cap and total > cap:
        body_rows.append(
            '<tr><td colspan="6" style="padding:12px 14px;border:0;background:#f8fafc;'
            'font-size:13px;color:#64748b;font-style:italic;">'
            f'+ {total - cap} more high-severity violation(s) &mdash; see the full report below.'
            "</td></tr>"
        )

    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:separate;border-spacing:0;width:100%;table-layout:fixed;'
        'border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:0;">'
        f"{colgroup}<thead>{header}</thead><tbody>{''.join(body_rows)}</tbody></table>"
    )


def _severity_summary_table_html(rows: List[Dict[str, Any]]) -> str:
    """Aggregate non-HIGH violations by workspace + policy + severity (count only).

    Keeps the digest compact at scale: hundreds of medium/low findings collapse
    to a handful of rows instead of one row each, while still attributing each
    group to the workspace it came from."""
    counts: Dict[tuple, int] = {}
    for r in rows:
        key = (r.get("workspace") or "", r.get("policy", "unknown"), r["severity"])
        counts[key] = counts.get(key, 0) + 1
    # Sort by severity, then workspace, then descending count.
    items = sorted(
        counts.items(), key=lambda kv: (_SEV_ORDER.get(kv[0][2], 3), kv[0][0], -kv[1])
    )

    def _th(text: str, align: str, radius: str = "") -> str:
        return (
            f'<th style="text-align:{align};padding:10px 14px;background:#0f172a;color:#e2e8f0;'
            f'font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;border:0;{radius}">'
            f'{text}</th>'
        )

    header = (
        "<tr>"
        + _th("Workspace", "left", "border-top-left-radius:10px;")
        + _th("Policy", "left")
        + _th("Severity", "left")
        + _th("Count", "right", "border-top-right-radius:10px;")
        + "</tr>"
    )
    # Explicit column widths so Outlook doesn't squish a column (see the HIGH table).
    sum_col_w = (150, 250, 118, 74)
    colgroup = "<colgroup>" + "".join(
        f'<col width="{w}" style="width:{w}px;">' for w in sum_col_w
    ) + "</colgroup>"
    body = []
    for i, ((workspace, policy, sev), count) in enumerate(items):
        bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
        cell = "padding:11px 14px;border:0;border-bottom:1px solid #eef2f7;vertical-align:middle;"
        body.append(
            f'<tr style="background:{bg};">'
            f'<td style="{cell}">{_workspace_html(workspace)}</td>'
            f'<td style="{cell}font-size:13px;color:#0f172a;font-weight:600;word-break:break-word;">{_esc(policy)}</td>'
            f'<td style="{cell}">{_sev_chip(sev)}</td>'
            f'<td style="{cell}text-align:right;font-size:15px;font-weight:800;color:#0f172a;">{count}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:separate;border-spacing:0;width:100%;table-layout:fixed;'
        'border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:0;">'
        f"{colgroup}<thead>{header}</thead><tbody>{''.join(body)}</tbody></table>"
    )


def _cta_html(app_url: str, brand_color: str, label: str = "See the full report") -> str:
    """A 'See the full report' button linking to the Sentinel page.

    Falls back to a plain-text note when no app URL is configured so the intent
    ("this is a summary — the full, filterable list lives in the app") is always
    conveyed."""
    if not app_url:
        return (
            '<p style="margin:26px 0 0 0;font-size:13px;line-height:1.5;color:#64748b;">'
            "This is a summary of the latest scan. Open the Enforcement Sentinel in the "
            "app for the complete, filterable list of violations.</p>"
        )
    url = _esc(app_url.rstrip("/") + "/governance/sentinel")
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="border-collapse:separate;margin:28px 0 0 0;"><tr>'
        f'<td style="border:0;border-radius:8px;background:{brand_color};">'
        f'<a href="{url}" style="display:inline-block;padding:13px 26px;color:#ffffff;'
        f'text-decoration:none;font-size:14px;font-weight:600;">{_esc(label)} &rarr;</a>'
        '</td></tr></table>'
    )


def _all_clear_html() -> str:
    return (
        '<div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:12px;padding:22px 24px;">'
        '<div style="font-size:16px;font-weight:700;color:#065f46;">&#10003; All clear</div>'
        '<div style="margin-top:6px;font-size:14px;color:#047857;line-height:1.5;">'
        'No active policy violations across the workspace in the latest scan.</div>'
        '</div>'
    )


# Cap on the number of HIGH-severity rows shown in full detail. Beyond this the
# table shows a "+N more" footer and points at the app. Keeps the email small
# and under mail-client clipping limits (Gmail clips ~102KB) even at scale.
_HIGH_DETAIL_CAP = 25


def render_digest_html(rows: List[Dict[str, Any]], brand_color: str = "#2563eb", app_url: str = "") -> str:
    """Compose the daily-digest email body. Designed to scale to hundreds of
    violations:

      * **Summary cards** — HIGH / MEDIUM / LOW totals.
      * **High severity** — full per-violation detail, capped at
        ``_HIGH_DETAIL_CAP`` with a "+N more" pointer.
      * **Medium & low** — an aggregated policy x severity count table, not one
        row per finding.
      * **See the full report** — a link to the app for the complete list.

    Shared by the scheduled digest and the on-demand send so the two never drift.
    """
    cta = _cta_html(app_url, brand_color)

    def _lead(text: str) -> str:
        return f'<p style="margin:0 0 20px 0;font-size:16px;line-height:1.5;color:#0f172a;">{text}</p>'

    if not rows:
        return _lead("Daily Enforcement Sentinel digest") + _all_clear_html() + cta

    high = [r for r in rows if r["severity"] == "HIGH"]
    lower = [r for r in rows if r["severity"] != "HIGH"]

    parts = [
        _lead(
            f'Daily Enforcement Sentinel digest &mdash; <strong>{len(rows)}</strong> '
            "active policy violation(s) across the workspace."
        ),
        _summary_cards_html(rows),
    ]

    if high:
        sub = (
            f"Showing the first {_HIGH_DETAIL_CAP} of {len(high)}."
            if len(high) > _HIGH_DETAIL_CAP
            else "Every high-severity violation, in full."
        )
        parts.append(_section_heading(f"High severity &mdash; {len(high)}", sub))
        parts.append(_violations_table_html(high, cap=_HIGH_DETAIL_CAP))

    if lower:
        parts.append(
            _section_heading(
                f"Medium &amp; low severity &mdash; {len(lower)}",
                "Grouped by policy. Open the full report for per-resource detail.",
            )
        )
        parts.append(_severity_summary_table_html(lower))

    parts.append(cta)
    return "".join(parts)


def digest_schedule_info() -> Dict[str, Any]:
    """Describe the anchored daily-digest schedule for the UI (hour, timezone,
    a human label, and the next fire time as naive-UTC ISO)."""
    from zoneinfo import ZoneInfo

    tz_name = getattr(settings, "ENFORCEMENT_DIGEST_TIMEZONE", "America/Los_Angeles") or "America/Los_Angeles"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - bad tz string shouldn't break the UI
        tz_name = "America/Los_Angeles"
        tz = ZoneInfo(tz_name)

    hour = int(getattr(settings, "ENFORCEMENT_DIGEST_HOUR_LOCAL", 7) or 0)
    hour = max(0, min(23, hour))

    now_local = datetime.now(timezone.utc).astimezone(tz)
    next_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if next_local <= now_local:
        next_local += timedelta(days=1)
    next_utc = next_local.astimezone(timezone.utc).replace(tzinfo=None)

    return {
        "hour": hour,
        "timezone": tz_name,
        "label": f"Daily at {hour:02d}:00 {tz_name}",
        "next_run": next_utc.isoformat() + "Z",
    }


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
    from app.services.sentinel_findings import load_run_violations

    state_ctx = request.state_context or {}
    rows = _active_violations(load_run_violations(db, request))
    recipient = getattr(settings, "GOVERNANCE_EMAIL_GROUP", "") or ""
    if not recipient:
        logger.warning("Sentinel notify: GOVERNANCE_EMAIL_GROUP is unset; skipping governance emails.")
        return {"notified": False, "reason": "no_recipient"}

    notifier = NotificationProvider()
    brand_color = (getattr(settings, "BRAND_COLOR_PRIMARY", "") or "#2563eb").strip() or "#2563eb"
    app_url = (getattr(settings, "APP_BASE_URL", "") or "").strip()
    cta = _cta_html(app_url, brand_color)
    sent_immediate = 0
    sent_digest = False

    def _lead(text: str, accent: str = "#0f172a") -> str:
        return (
            f'<p style="margin:0 0 20px 0;font-size:16px;line-height:1.5;color:{accent};">{text}</p>'
        )

    # --- Immediate HIGH (transition-gated) ---
    high_rows = [r for r in rows if r["severity"] == "HIGH"]
    new_high = [
        r for r in high_rows
        if _prior_severity(db, request.id, r["resource_id"], r["policy"], r.get("workspace")) != "HIGH"
    ]
    if new_high:
        subject = f"[Enforcement] {len(new_high)} new high-severity violation(s)"
        body = (
            _lead(
                f'<strong style="color:#B91C1C;">{len(new_high)}</strong> new high-severity policy '
                "violation(s) were detected in the latest Enforcement Sentinel scan and require attention."
            )
            + _violations_table_html(new_high, cap=_HIGH_DETAIL_CAP)
            + cta
        )
        try:
            await notifier.send_email(to=recipient, subject=subject, body=body, is_html=True)
            sent_immediate = len(new_high)
        except Exception as e:  # noqa: BLE001 - notification failure shouldn't fail the run
            logger.error("Sentinel notify: immediate HIGH email failed: %s", e)

    # --- Daily digest (anchored) ---
    if _digest_should_emit(db, request):
        body = render_digest_html(rows, brand_color, app_url)
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
