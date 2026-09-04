"""
Governance Sentinel scanning and automated remediation workflow tools.
"""
import logging
from typing import Any, Dict

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


@tool(
    name="sentinel_discover",
    side_effect_class="read",
    description="Discover policy violations across governed assets (OPA evaluation).",
)
async def sentinel_discover(**kwargs) -> Dict[str, Any]:
    if "_violations" in kwargs:
        violations = kwargs.get("_violations") or []
        return {"violations": violations, "checks": [], "summary": f"{len(violations)} injected violation(s)."}

    request_id = kwargs.get("_request_id")
    db, request = _common._load_request(request_id)
    if request is None:
        logger.warning("sentinel_discover: no request found for id=%s; nothing to scan", request_id)
        return {"violations": [], "checks": [], "summary": "No request context; scan skipped."}

    logger.info(
        "sentinel_discover: request=%s workspaces=%s",
        request_id,
        (request.state_context or {}).get("workspaces") or "all",
    )
    try:
        from app.workflows.sentinel import run_discovery

        result = await run_discovery(request)
        return {
            "summary": result.get("summary", ""),
            "violation_count": result.get("violation_count", 0),
            "pass_count": result.get("pass_count", 0),
            "total_resources_scanned": result.get("total_resources_scanned", 0),
        }
    finally:
        db.close()


@tool(
    name="sentinel_enforce",
    side_effect_class="app_write",
    description=(
        "Apply automated remediation for discovered violations: safe/reversible "
        "actions (certify/uncertify/warn) execute; destructive intents are "
        "downgraded to an owner warning and left for manual Review & Act."
    ),
)
async def sentinel_enforce(**kwargs) -> Dict[str, Any]:
    request_id = kwargs.get("_request_id")
    db, request = _common._load_request(request_id)
    if request is None:
        logger.warning("sentinel_enforce: no request found for id=%s", request_id)
        return {"enforced": True, "actions": [], "summary": "No request context; nothing to enforce."}

    logger.info("sentinel_enforce: request=%s", request_id)
    try:
        from app.workflows.sentinel import run_enforcement

        return await run_enforcement(db, request)
    finally:
        db.close()


@tool(
    name="sentinel_notify",
    side_effect_class="notify",
    description=(
        "Send governance notifications for a sentinel run: immediate email for "
        "new HIGH-severity violations (deduped by transition) + an anchored "
        "once-per-day digest to the governance group."
    ),
)
async def sentinel_notify(**kwargs) -> Dict[str, Any]:
    request_id = kwargs.get("_request_id")
    db, request = _common._load_request(request_id)
    if request is None:
        logger.warning("sentinel_notify: no request found for id=%s", request_id)
        return {"notified": False, "reason": "no_request_context"}
    try:
        from app.workflows.sentinel import run_notify

        return await run_notify(db, request)
    finally:
        db.close()
