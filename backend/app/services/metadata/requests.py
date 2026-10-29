"""Filing ``metadata_change`` approval requests, and the workflow's prepare and record steps (ADR-0006 D8).

Mirrors ``services/rbac/requests.py``: the prepare step stores the exact items and
approver groups in the request's state context, and the record step reads them
back from the request, never from its arguments, so it can't go beyond what
approvers saw.

A request carries ``ops`` (ChangeOp dicts), already expanded to every target
environment, so preparing it again resolves exactly the filed items.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import PermanentError
from app.services.metadata.errors import MetadataError, VersionConflict
from app.services.metadata.types import (
    Actor,
    ChangeItem,
    ChangeOp,
    ChangePreview,
    ChangeRequest,
    ItemDecision,
    ObjectKey,
    RevertRequest,
)

logger = logging.getLogger(__name__)

WORKFLOW_ID = "metadata_change"
PREPARED_CONTEXT_KEY = "metadata_prepared"
FILED_FACT = "metadata_change_filed"
PREPARED_FACT = "metadata_change_prepared"
_AGENT_TITLE_PREFIX = "Agent Request:"
_TITLE_LIMIT = 200


def _session() -> Session:
    from app.db.session import get_lakebase_session

    return get_lakebase_session()


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _item_label(item: ChangeItem) -> str:
    target = f"{item.name}.{item.column}" if item.column else item.name
    if item.field == "comment":
        verb = "clear the description of" if item.action == "comment_clear" else "set the description of"
        return f"{verb} {target}"
    verb = "remove" if item.action == "unset" else "set"
    return f"{verb} {item.key} on {target}"


def _title(items: Sequence[ChangeItem]) -> str:
    if not items:
        return "Metadata change"
    title = f"Metadata change: {_item_label(items[0])}"
    if len(items) > 1:
        title += f" and {len(items) - 1} more"
    return title[:_TITLE_LIMIT]


def ops_for_items(items: Sequence[ChangeItem]) -> List[ChangeOp]:
    """Ops that resolve to exactly these items, each pinned to the version it was resolved against."""
    sets: Dict[tuple, ChangeOp] = {}
    unsets: Dict[tuple, ChangeOp] = {}
    comments: List[ChangeOp] = []
    for item in items:
        target = (item.name, item.column)
        if item.field == "comment":
            comments.append(ChangeOp(
                op="clear_comment" if item.action == "comment_clear" else "set_comment",
                name=item.name, column=item.column, comment=item.after,
                expected_comment_version=item.before_version, securable_type=item.securable_type,
            ))
            continue
        bucket = unsets if item.action == "unset" else sets
        op = bucket.get(target)
        if op is None:
            op = bucket[target] = ChangeOp(
                op="unset_tags" if item.action == "unset" else "set_tags",
                name=item.name, column=item.column, securable_type=item.securable_type,
            )
        if item.action == "unset":
            op.keys.append(item.key or "")
        else:
            op.tags[item.key or ""] = item.after
        op.expected_versions[item.key or ""] = item.before_version
    return [*sets.values(), *unsets.values(), *comments]


def _request_row(db: Session, request_id: Optional[str]):
    from app.db import RequestModel

    request = db.get(RequestModel, request_id) if request_id else None
    if request is None:
        raise PermanentError(f"Request {request_id} doesn't exist, so its metadata change can't be prepared or "
                             "recorded.")
    return request


def _requester(db: Session, request) -> Actor:
    """The request row's requester; the actor type comes from the server's filing record, never from context."""
    from app.services.metadata import principals
    from app.state_machines.facts import get_latest_fact

    requester = request.requester_email or "unknown"
    fact = get_latest_fact(db, request.id, FILED_FACT)
    data = (fact.event_data or {}) if fact is not None else {}
    if data.get("actor_type") == "service_principal" and data.get("actor") == requester:
        row = principals.find_principal(db, [requester])
        return Actor(id=requester, actor_type="service_principal",
                     display_name=row.display_name if row else requester,
                     principal=principals.to_scope(row) if row else None)
    return Actor(id=requester, actor_type="user", email=requester)


def _approver(db: Session, request_id: str) -> Optional[str]:
    from app.state_machines.facts import get_latest_fact

    fact = get_latest_fact(db, request_id, "approval_received")
    data = (fact.event_data or {}) if fact is not None else {}
    return data.get("approved_by") or data.get("actor")


def _deny_text(preview: ChangePreview) -> str:
    messages = [f.message for f in preview.findings if f.severity == "deny"]
    messages += [f.message for d in preview.items for f in d.findings if f.severity == "deny"]
    return " ".join(dict.fromkeys(messages)) or "A metadata policy blocks it."


# --- Filing ----------------------------------------------------------------------------


def file_change_request(
    db: Session,
    *,
    request: ChangeRequest,
    preview: ChangePreview,
    items: List[ItemDecision],
    actor: Actor,
    source: Optional[str] = None,
    reverts_change_set_id: Optional[str] = None,
) -> str:
    """File the items that need approval as one request and return its id.

    ``reverts_change_set_id`` (a revert) is kept on the filed fact, so recording the
    approved change marks that change set reverted.
    """
    from app.models.request import RequestCreate
    from app.services.request_service import RequestService
    from app.state_machines.facts import add_fact

    change_items = [d.item for d in items]
    if not change_items:
        raise MetadataError("There's nothing in this change that needs approval.")
    ops = [op.model_dump(mode="json") for op in ops_for_items(change_items)]
    metadata = {
        "ops": ops,
        "reason": request.reason,
        "source": source or "api",
        "environments": sorted({i.environment for i in change_items if i.environment}),
    }
    try:
        row = RequestService.create_request(db, RequestCreate(
            type=WORKFLOW_ID, title=_title(change_items), requester_email=actor.id, metadata=metadata,
        ))
    except ValueError as exc:
        raise MetadataError(str(exc)) from None
    filed = {"digest": _digest(ops), "actor": actor.id, "actor_type": actor.actor_type}
    if reverts_change_set_id:
        filed["reverts_change_set_id"] = reverts_change_set_id
    add_fact(db, row.id, FILED_FACT, filed, actor=actor.id)
    add_fact(db, row.id, "request_submitted", {}, actor=actor.id)
    logger.info("Metadata: filed request %s for %d item(s) by %s (%s)", row.id, len(change_items), actor.id,
                actor.actor_type)
    return row.id


# --- Prepare step ----------------------------------------------------------------------


def _owner_groups(db: Session, items: Sequence[ChangeItem]) -> List[str]:
    """The objects' current owner groups (not ones this change would set), else the governance group."""
    from app.core.config import settings
    from app.services.metadata import authz

    groups: List[str] = []
    for fqn_key in dict.fromkeys(i.fqn_key for i in items):
        found, _source = authz.owner_groups(db, ObjectKey.from_fqn_key(fqn_key))
        for group in found:
            if group not in groups:
                groups.append(group)
    if not groups:
        fallback = (settings.METADATA_GOVERNANCE_APPROVER_GROUP or "").strip()
        if fallback:
            groups.append(fallback)
    return groups


def _store_prepared(db: Session, request, prepared: Dict[str, Any], *, title: str) -> None:
    from app.state_machines.facts import add_fact

    ctx = dict(request.state_context or {})
    ctx[PREPARED_CONTEXT_KEY] = prepared
    request.state_context = ctx
    if not request.title or request.title.startswith(_AGENT_TITLE_PREFIX):
        request.title = title
    db.commit()
    db.refresh(request)
    stored = (request.state_context or {}).get(PREPARED_CONTEXT_KEY)
    add_fact(db, request.id, PREPARED_FACT, {"digest": _digest(stored)}, actor="system")


def prepare(db: Session, request_id: str) -> Dict[str, Any]:
    """Workflow prepare step: re-resolve and re-check the requested change, store the prepared items."""
    from app.services.metadata import changes

    from app.state_machines.facts import get_latest_fact

    request = _request_row(db, request_id)
    ctx = request.state_context or {}
    filed = get_latest_fact(db, request.id, FILED_FACT)
    if filed is None:
        raise PermanentError("This metadata change wasn't submitted through the Metadata Manager, so it can't be "
                             "prepared. Submit it from the Metadata Manager, its API or submit_metadata_change.")
    if (filed.event_data or {}).get("digest") != _digest(ctx.get("ops") or []):
        raise PermanentError("This request's change was edited after it was filed, so it won't be prepared. "
                             "Submit the change again.")
    try:
        ask = ChangeRequest.model_validate({"ops": ctx.get("ops") or [], "reason": ctx.get("reason") or ""})
    except ValidationError as exc:
        raise PermanentError(f"This request's metadata change isn't valid: {exc.errors()[0].get('msg')}") from None
    requester = _requester(db, request)
    reverts = (filed.event_data or {}).get("reverts_change_set_id")
    try:
        # A revert is planned again rather than re-resolved from its ops: undoing a change set that started
        # managing an object drops the object's row, which no op can express.
        preview = (changes.preview_revert(db, reverts, RevertRequest(reason=ask.reason), requester) if reverts
                   else changes.preview(db, ask, requester))
    except MetadataError as exc:
        raise PermanentError(exc.message) from None
    if preview.blocked:
        raise PermanentError(f"This metadata change is blocked. {_deny_text(preview)}")

    items = [d.item for d in preview.items]
    sensitive_reasons = list(dict.fromkeys(r for d in preview.items for r in d.decision.sensitive_reasons))
    approver_groups = _owner_groups(db, items)
    prepared = {
        "items": [i.model_dump(mode="json") for i in items],
        "approver_groups": approver_groups,
        "sensitive": bool(sensitive_reasons),
        "sensitive_reasons": sensitive_reasons,
        "reason": ask.reason,
        "source": ctx.get("source") or "agent",
        "actor_type": requester.actor_type,
        "digest": preview.digest,
    }
    if reverts:
        prepared["reverts_change_set_id"] = reverts
    _store_prepared(db, request, prepared, title=_title(items))
    logger.info("Metadata: prepared request %s (%d item(s), approvers %s, sensitive %s)", request.id, len(items),
                approver_groups or "Platform Admins", bool(sensitive_reasons))
    return {
        "nothing_to_change": not items,
        "sensitive": bool(sensitive_reasons),
        "approver_groups": approver_groups,
        "change_count": len(items),
        "report_title": "Metadata change",
        "report_markdown": preview.report_markdown,
    }


def prepare_step(request_id: Optional[str]) -> Dict[str, Any]:
    db = _session()
    try:
        return prepare(db, request_id)
    finally:
        db.close()


# --- Record step -----------------------------------------------------------------------


def _prepared(db: Session, request) -> Dict[str, Any]:
    from app.state_machines.facts import get_latest_fact

    prepared = (request.state_context or {}).get(PREPARED_CONTEXT_KEY)
    if not prepared:
        raise PermanentError("This request has no prepared metadata change, so there's nothing approved to record.")
    fact = get_latest_fact(db, request.id, PREPARED_FACT)
    if fact is None or (fact.event_data or {}).get("digest") != _digest(prepared):
        raise PermanentError("This request's prepared change was edited after it was prepared, so it won't be "
                             "recorded. File the change again.")
    return prepared


def _recorded_change_set(db: Session, request_id: str) -> Optional[str]:
    from app.db.metadata import MetadataChangeSetModel, MetadataEventModel

    row = (
        db.query(MetadataChangeSetModel.id)
        .join(MetadataEventModel, MetadataEventModel.change_set_id == MetadataChangeSetModel.id)
        .filter(MetadataChangeSetModel.request_id == request_id)
        .first()
    )
    return row[0] if row else None


def record(db: Session, request_id: str) -> Dict[str, Any]:
    """Workflow record step: write the prepared items, rechecked against current versions."""
    from app.services.metadata import changes

    request = _request_row(db, request_id)
    prepared = _prepared(db, request)
    existing = _recorded_change_set(db, request.id)
    if existing:
        return {"change_set_id": existing, "already_recorded": True,
                "message": f"Already recorded as change set {existing}."}
    if not prepared.get("items"):
        return {"change_set_id": None, "already_recorded": False,
                "message": "Nothing changed: everything requested was already true."}
    requester = _requester(db, request)
    approver = _approver(db, request.id)
    try:
        change_set = changes.record_prepared(db, request_id=request.id, prepared=prepared, requester=requester,
                                             approver=approver)
    except VersionConflict as exc:
        raise PermanentError(f"{exc.message} The approved change wasn't recorded; file it again.") from None
    except MetadataError as exc:
        raise PermanentError(exc.message) from None
    count = len(prepared["items"])
    logger.info("Metadata: request %s recorded as change set %s (%d item(s), approved by %s)", request.id,
                change_set.id, count, approver or "unknown")
    return {
        "change_set_id": change_set.id,
        "already_recorded": False,
        "message": f"Recorded {count} change(s) as change set {change_set.id}; the reconciler applies them to "
                   "Unity Catalog on its next pass.",
    }


def record_step(request_id: Optional[str]) -> Dict[str, Any]:
    db = _session()
    try:
        return record(db, request_id)
    finally:
        db.close()


# --- Approver source -------------------------------------------------------------------


def owner_approver_groups(db: Session, request_id: str) -> List[str]:
    """The ``metadata_owner`` approver source: owner groups of the prepared items."""
    from app.db import RequestModel

    request = db.get(RequestModel, request_id)
    prepared = ((request.state_context or {}) if request is not None else {}).get(PREPARED_CONTEXT_KEY) or {}
    return list(prepared.get("approver_groups") or [])


def owner_approver_groups_for(request_id: str) -> List[str]:
    db = _session()
    try:
        return owner_approver_groups(db, request_id)
    finally:
        db.close()
