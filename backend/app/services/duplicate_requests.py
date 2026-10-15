"""Find existing requests that a new workflow submission would duplicate.

Shared by the read-only ``check_duplicate_requests`` agent tool and the gate in
``execute_workflow``, so the check the agent shows the user and the check that
holds back a submission can't disagree.

Matching is generic rather than written per workflow. Each submitted parameter
is classified by its name:

* identity  - names the target resource (``workspace_name``, ``repo_name``,
              ``assets``, ``target_host``, ``*_name`` / ``*_path`` / ``*_id``).
              Every identity parameter the two requests share must match, and
              a location (``target_host``, ``targets``) only counts on its own
              when the request names nothing more specific: two different
              tables in the same workspace are not duplicates.
* qualifier - which variant of the operation (``action``, ``access_level``,
              ``environment``). When both requests carry one and the values
              don't overlap, they are different requests (enable vs. disable).
* anything else (justification, cost center, owner group, ...) is ignored, so
  rewording a reason never hides a duplicate.

Access-style workflows grant something to the requester, so another person's
request for the same resource is not a duplicate of yours. Those compare only
against the caller's own requests, unless the parameters name a beneficiary
(``principal``, ``github_username``), which then has to match instead.

Non-admins can't open other people's requests (``GET /requests/{id}`` 403s), so
a match on someone else's request is reported without its id, link, title, or
requester: only that it exists, its status and date, and which of the caller's
own values it matched.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.request import RequestModel
from app.services.approval_scope import is_platform_admin, parse_csv

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "rejected", "failed"})

_IDENTITY_KEYS = frozenset({
    "workspace", "workspace_name", "target_host", "host", "targets",
    "catalog", "schema", "table", "volume", "full_name", "asset_name", "assets", "datasets",
    "parent", "name", "path", "folder_path", "notebook_path",
    "target_catalog", "reference_catalog", "target_location", "source_system",
    "repo", "repo_name", "repository", "feature", "feature_id", "setting_name",
    "resource_id", "service", "service_name", "credential_name", "tag_name",
    "group", "group_name", "access_group", "project_name", "business_domain", "domain",
    "course_id",
    "principal", "github_username", "user_email", "member_email", "members", "team_members",
})
_LOCATION_KEYS = frozenset({"target_host", "host", "targets", "workspace_url"})
# Playbook revisions have sent the same thing under different names
# (``asset_name`` vs ``assets: [{asset_name}]``); compare them as one field.
_KEY_ALIASES = {"asset_name": "asset", "assets": "asset", "datasets": "asset", "full_name": "asset"}
_BENEFICIARY_KEYS = frozenset({
    "principal", "github_username", "user_email", "member_email", "members", "team_members",
})
_IDENTITY_SUFFIXES = ("_name", "_path", "_host", "_id", "_slug")
# Suffix matches that describe people, display text, or bookkeeping rather than
# the resource itself.
_NOT_IDENTITY = frozenset({
    "request_id", "parent_request_id", "root_request_id", "trace_id", "session_id",
    "thread_id", "conversation_id", "tool_call_id",
    "display_name", "manager_name", "team_name", "team_slug",
})
_QUALIFIER_KEYS = frozenset({
    "action", "type", "asset_type", "resource_type", "volume_type", "workspace_type",
    "environment", "scope", "access_level", "access_type", "permission", "privileges",
    "request_category",
})
_ACCESS_HINT_KEYS = frozenset({
    "access_level", "access_type", "permission", "privileges", "duration", "duration_days",
})

# Bounds the rows loaded per check; state_context is read for each candidate.
_MAX_CANDIDATES = 200
_MAX_REPORTED = 5

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_SEPARATOR_RE = re.compile(r"[\s_\-`'\"]+")


def is_enabled() -> bool:
    return bool(getattr(settings, "DUPLICATE_CHECK_ENABLED", True))


def configured_statuses() -> Set[str]:
    raw = getattr(settings, "DUPLICATE_CHECK_STATUSES", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(s).strip().lower() for s in raw if str(s).strip()}


def _lookback_days() -> int:
    try:
        return max(0, int(getattr(settings, "DUPLICATE_CHECK_LOOKBACK_DAYS", 14) or 0))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Parameter classification + normalization
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> Optional[str]:
    """Fold a value so cosmetic differences don't hide a match.

    ``Supply Chain`` / ``supply-chain`` / ``supply_chain`` all become
    ``supplychain``; ``https://adb-1.net/`` becomes ``adb1.net``. Dots and
    slashes survive, so ``cat.schema.table`` and workspace paths keep their shape.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    text = _SCHEME_RE.sub("", value.strip().lower()).rstrip("/")
    text = _SEPARATOR_RE.sub("", text)
    return text or None


def _is_identity_key(key: str) -> bool:
    k = key.lower()
    if k in _NOT_IDENTITY or k.startswith(("requested_by", "requester", "requestor")):
        return False
    return k in _IDENTITY_KEYS or k.endswith(_IDENTITY_SUFFIXES)


def _signature(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(normalized, display) for a dict item such as one entry of ``assets``."""
    norms: List[str] = []
    shown: List[str] = []
    for key in sorted(k for k in item if isinstance(k, str) and _is_identity_key(k)):
        norm = _normalize(item[key])
        if norm:
            norms.append(f"{key.lower()}={norm}")
            shown.append(str(item[key]).strip())
    if not norms:
        return None
    if len(norms) == 1:
        # A bare ``{"asset_name": x}`` compares equal to a plain ``x``.
        return norms[0].split("=", 1)[1], shown[0]
    return "|".join(norms), " / ".join(shown)


def _values(value: Any) -> Dict[str, str]:
    """Normalized -> display form of one parameter value (scalar, list, or dict)."""
    items: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    out: Dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            sig = _signature(item)
            if sig:
                out.setdefault(sig[0], sig[1])
        else:
            norm = _normalize(item)
            if norm:
                out.setdefault(norm, str(item).strip())
    return out


def identity_values(params: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for key, value in (params or {}).items():
        if isinstance(key, str) and _is_identity_key(key):
            vals = _values(value)
            if vals:
                field = _KEY_ALIASES.get(key.lower(), key.lower())
                out.setdefault(field, {}).update(vals)
    return out


def _qualifier_values(params: Optional[Dict[str, Any]]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for key, value in (params or {}).items():
        if isinstance(key, str) and key.lower() in _QUALIFIER_KEYS:
            vals = set(_values(value))
            if vals:
                out[key.lower()] = vals
    return out


def compares_own_requests_only(workflow_type: str, params: Optional[Dict[str, Any]]) -> bool:
    present = {
        str(k).lower() for k, v in (params or {}).items() if v not in (None, "", [], {})
    }
    if present & _BENEFICIARY_KEYS:
        return False
    return "access" in (workflow_type or "").lower() or bool(present & _ACCESS_HINT_KEYS)


def _matching_types(workflow_type: str) -> Set[str]:
    from app.services.workflow_service import LEGACY_WORKFLOW_ALIASES

    types = {workflow_type}
    for legacy, current in LEGACY_WORKFLOW_ALIASES.items():
        if workflow_type in (legacy, current):
            types.update((legacy, current))
    return types


def _match(
    new_ident: Dict[str, Dict[str, str]],
    new_qual: Dict[str, Set[str]],
    context: Dict[str, Any],
) -> Optional[Dict[str, List[str]]]:
    """Matched fields (shown in the caller's own wording), or None."""
    old_ident = identity_values(context)
    shared = sorted(new_ident.keys() & old_ident.keys())
    if not shared:
        return None
    if (new_ident.keys() - _LOCATION_KEYS) and not (set(shared) - _LOCATION_KEYS):
        return None
    matched: Dict[str, List[str]] = {}
    for key in shared:
        overlap = new_ident[key].keys() & old_ident[key].keys()
        if not overlap:
            return None
        matched[key] = sorted(new_ident[key][n] for n in overlap)
    old_qual = _qualifier_values(context)
    for key, vals in new_qual.items():
        if key in old_qual and not (vals & old_qual[key]):
            return None
    return matched


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def _role_list(roles: Any) -> List[str]:
    if roles is None:
        return []
    if isinstance(roles, str):
        return parse_csv(roles)
    return [str(r) for r in roles]


def _match_recommendation(status: str, mine: bool) -> str:
    finished = status in TERMINAL_STATUSES
    if status in ("rejected", "failed"):
        return (
            f"A matching request was {status}. Check why before submitting again."
        )
    if mine and not finished:
        return "You already have this request in progress. Track it instead of submitting again."
    if mine:
        return "You already completed this request recently. Check whether it's still needed."
    if not finished:
        return (
            "Another person already has a matching request in progress, which may cover "
            "what you need."
        )
    return (
        "A matching request for someone else was completed recently, so the resource may "
        "already exist."
    )


def _overall_recommendation(matches: List[Dict[str, Any]]) -> str:
    if not matches:
        return "No matching open or recent requests. Safe to proceed."
    lead = matches[0]
    return (
        f"{lead['recommendation']} Tell the user about the match and ask whether to submit "
        "anyway, wait, or change the request."
    )


def find_duplicate_requests(
    db: Session,
    workflow_type: str,
    parameters: Optional[Dict[str, Any]],
    *,
    requester_email: Optional[str] = None,
    roles: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Look for open (any age) and recently finished requests the submission repeats.

    Returns ``{checked, count, possible_duplicates, compared_fields, recommendation}``.
    Statuses and the lookback come from Admin -> Settings -> Agent.
    """
    params = parameters if isinstance(parameters, dict) else {}
    new_ident = identity_values(params)
    if not workflow_type or not new_ident:
        return {
            "checked": True,
            "count": 0,
            "possible_duplicates": [],
            "compared_fields": [],
            "recommendation": (
                "None of these parameters name a specific resource, so there was nothing "
                "to compare. Safe to proceed."
            ),
        }

    own_only = compares_own_requests_only(workflow_type, params)
    statuses = configured_statuses()
    open_statuses = statuses - TERMINAL_STATUSES
    finished_statuses = statuses & TERMINAL_STATUSES
    lookback = _lookback_days()

    clauses = []
    if open_statuses:
        clauses.append(RequestModel.status.in_(sorted(open_statuses)))
    if finished_statuses and lookback:
        current = now or datetime.now(timezone.utc)
        cutoff = current.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback)
        finished_at = func.coalesce(RequestModel.updated_at, RequestModel.created_at)
        clauses.append(
            and_(RequestModel.status.in_(sorted(finished_statuses)), finished_at >= cutoff)
        )

    candidates: List[RequestModel] = []
    if clauses and not (own_only and not requester_email):
        query = db.query(RequestModel).filter(
            RequestModel.type.in_(sorted(_matching_types(workflow_type))),
            or_(*clauses),
        )
        if own_only:
            query = query.filter(
                func.lower(RequestModel.requester_email) == requester_email.lower()
            )
        candidates = (
            query.order_by(RequestModel.created_at.desc()).limit(_MAX_CANDIDATES).all()
        )

    admin = is_platform_admin(_role_list(roles))
    me = (requester_email or "").strip().lower()
    new_qual = _qualifier_values(params)
    matches: List[Dict[str, Any]] = []
    for req in candidates:
        context = req.state_context if isinstance(req.state_context, dict) else {}
        matched = _match(new_ident, new_qual, context)
        if matched is None:
            continue
        status = (req.status or "").lower()
        mine = bool(me) and (req.requester_email or "").strip().lower() == me
        entry: Dict[str, Any] = {
            "status": status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "matched_fields": matched,
            "submitted_by_you": mine,
        }
        if mine or admin:
            entry["request_id"] = req.id
            entry["request_link"] = f"[{req.id}](/requests/{req.id})"
            entry["title"] = req.title
            entry["requester"] = req.requester_email or "system"
        else:
            entry["requester"] = "another user"
        entry["recommendation"] = _match_recommendation(status, mine)
        matches.append(entry)

    # In-flight before finished, then newest first (the query order).
    matches.sort(key=lambda m: m["status"] in TERMINAL_STATUSES)
    return {
        "checked": True,
        "count": len(matches),
        "possible_duplicates": matches[:_MAX_REPORTED],
        "compared_fields": sorted(new_ident),
        "recommendation": _overall_recommendation(matches),
    }
