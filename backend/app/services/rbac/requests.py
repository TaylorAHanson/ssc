"""Filing ``rbac_access_change`` requests, and the workflow's plan and apply steps (ADR 0005 D4, D8).

One workflow carries two shapes of request:

* an access change: ``items`` (AccessItem dicts, all of one type), the
  requester's ``justification`` and an optional ``group_ref`` shared by the
  requests one ask was split into (one per type, each with its own approvers);
* a direct edit held by the two-person rule: ``changes`` (a ChangeSetIn dict)
  and its ``plane``, filed by the Access Rules page for the editing admin. Only
  ``submit_row_changes`` files these. It records a ``rbac_direct_edit_filed``
  fact with a digest of the edit, and the plan step refuses ``changes`` without
  a matching fact, so a chat submission can't pass itself off as an admin edit;
* a revert held by the two-person rule: ``revert_of`` (a change set id) and its
  ``plane`` (None when the change set spans several types; Platform Admins
  approve those), filed from History by ``submit_revert`` with the same fact.

The plan step stores what will happen in the request's state context under
``inflight.PLAN_CONTEXT_KEY`` (every shape keeps its row diffs under
``diffs``), so the grid marks the rows. The apply step reads the plan back from
the request, never from its arguments, so it can't go beyond what approvers saw.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.db.rbac import PLANES, SOURCE_REQUEST, RbacChangeSetModel
from app.models.rbac import AccessChangeIn, AccessItem, AccessProposal, ChangeSetIn, ChangeSetPreview, Finding
from app.services.rbac import approvers, cells, csv_export, inflight, layout, ledger, report
from app.services.rbac.ledger import ChangeBlocked, LedgerError, VersionConflict

logger = logging.getLogger(__name__)

WORKFLOW_ID = inflight.WORKFLOW_ID
PLAN_CONTEXT_KEY = inflight.PLAN_CONTEXT_KEY
DIRECT_EDIT_FACT = "rbac_direct_edit_filed"
PLAN_FACT = "rbac_plan_recorded"
KIND_ITEMS = "items"
KIND_CHANGES = "changes"
KIND_REVERT = "revert"
OVERLAP_CODE = "inflight_overlap"
# execute_workflow's placeholder title, replaced by a descriptive one at plan time.
_AGENT_TITLE_PREFIX = "Agent Request:"
_TITLE_LIMIT = 200


class AccessRequestError(LedgerError):
    """A request that can't be filed, with a message fit for the user."""


def _session() -> Session:
    from app.db.session import get_lakebase_session

    return get_lakebase_session()


# --- Shared helpers ------------------------------------------------------------------


def _deny_text(findings: Sequence[Finding]) -> str:
    messages = [f.message for f in findings if f.severity == "deny"]
    unique = list(dict.fromkeys(messages))
    return " ".join(unique) or "An access policy blocks it."


def _requested_items(proposal: AccessProposal) -> List[AccessItem]:
    """The proposal's items without the prerequisites the planner added (re-planning adds them again)."""
    return [i for i in proposal.items if i not in proposal.added_prerequisites]


def _plain_item(item: AccessItem) -> str:
    plane = cells.item_plane(item) or ""
    verb = "grant" if item.action == "grant" else "revoke"
    target = cells.describe_securable(item, plane) if plane else (item.securable_type or "securable")
    prep = "to" if item.action == "grant" else "from"
    return f"{verb} {cells.join_cell(item.privileges)} on {target} {prep} {item.principal_name}"


def _items_title(items: Sequence[AccessItem]) -> str:
    if not items:
        return "Access change"
    title = f"Access change: {_plain_item(items[0])}"
    if len(items) > 1:
        title += f" and {len(items) - 1} more"
    return title[:_TITLE_LIMIT]


def _change_digest(change: ChangeSetIn) -> str:
    payload = json.dumps(change.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file(db: Session, *, title: str, requester_email: str, metadata: Dict[str, Any]) -> str:
    from app.models.request import RequestCreate
    from app.services.request_service import RequestService
    from app.state_machines.facts import add_fact

    try:
        request = RequestService.create_request(db, RequestCreate(
            type=WORKFLOW_ID, title=title, requester_email=requester_email, metadata=metadata,
        ))
    except ValueError as exc:
        raise AccessRequestError(str(exc)) from None
    add_fact(db, request.id, "request_submitted", {}, actor=requester_email)
    return request.id


def _request(db: Session, request_id: Optional[str]):
    from app.db import RequestModel

    request = db.get(RequestModel, request_id) if request_id else None
    if request is None:
        raise PermanentError(f"Request {request_id} doesn't exist, so its access change can't be planned or applied.")
    return request


def _requester(request) -> str:
    # The request row's requester, never a context value a caller could have supplied.
    return request.requester_email or "unknown"


def _approver(db: Session, request_id: str) -> Optional[str]:
    from app.state_machines.facts import get_latest_fact

    fact = get_latest_fact(db, request_id, "approval_received")
    data = (fact.event_data or {}) if fact is not None else {}
    return data.get("approved_by") or data.get("actor")


def _plan_digest(plan: Any) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _store_plan(db: Session, request, plan: Dict[str, Any], *, title: Optional[str] = None) -> None:
    """Store the plan on the request, and its digest as a fact apply checks it against."""
    from app.state_machines.facts import add_fact

    ctx = dict(request.state_context or {})
    ctx[PLAN_CONTEXT_KEY] = plan
    request.state_context = ctx
    if title and (not request.title or request.title.startswith(_AGENT_TITLE_PREFIX)):
        request.title = title[:_TITLE_LIMIT]
    db.commit()
    # Digest what the database hands back, so apply compares like with like.
    db.refresh(request)
    add_fact(db, request.id, PLAN_FACT, {"digest": _plan_digest((request.state_context or {}).get(PLAN_CONTEXT_KEY))},
             actor="system")


def _check_plan_unchanged(db: Session, request, plan: Dict[str, Any]) -> None:
    from app.state_machines.facts import get_latest_fact

    fact = get_latest_fact(db, request.id, PLAN_FACT)
    if fact is None or (fact.event_data or {}).get("digest") != _plan_digest(plan):
        raise PermanentError("This request's stored plan was changed after it was planned, so it won't be "
                             "applied. File the change again.")


def _without_self_overlap(db: Session, findings: Sequence[Finding], request_id: str) -> List[Finding]:
    """Drop this request from in-flight overlap warnings (its own stored plan isn't an overlap)."""
    touched = inflight.touched_rule_ids(db)
    out: List[Finding] = []
    for finding in findings:
        if finding.code != OVERLAP_CODE or not finding.rule_id:
            out.append(finding)
            continue
        holders = touched.get(finding.rule_id, [])
        others = [r for r in holders if r != request_id]
        if not others:
            continue
        if len(others) != len(holders):
            noun = "request" if len(others) == 1 else "requests"
            finding = finding.model_copy(
                update={"message": f"Open access {noun} {', '.join(others)} will also change this row."})
        out.append(finding)
    return out


def _siblings(db: Session, request, group_ref: Optional[str]) -> List[report.Sibling]:
    """Other requests filed from the same ask (same requester, same group_ref)."""
    if not group_ref:
        return []
    from app.db import RequestModel

    rows = (
        db.query(RequestModel)
        .filter(RequestModel.type == WORKFLOW_ID, RequestModel.id != request.id,
                RequestModel.requester_email == request.requester_email)
        .order_by(RequestModel.created_at.asc())
        .all()
    )
    out: List[report.Sibling] = []
    for row in rows:
        ctx = row.state_context or {}
        if ctx.get("group_ref") != group_ref:
            continue
        out.append((row.id, _context_plane(ctx)))
    return out


def _context_plane(ctx: Dict[str, Any]) -> str:
    plan = ctx.get(PLAN_CONTEXT_KEY) or {}
    if plan.get("plane"):
        return plan["plane"]
    if ctx.get("plane"):
        return ctx["plane"]
    for item in ctx.get("items") or []:
        if isinstance(item, dict):
            plane = layout.plane_for_securable_type(item.get("securable_type")) or item.get("plane")
            if plane:
                return plane
    return ""


def _validation_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        where = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
    return "; ".join(parts)


def export_note(db: Session) -> str:
    """When the CSV files will pick the change up. Best-effort: never fails a step."""
    try:
        status = csv_export.status(db, limit=1)
    except Exception as exc:  # noqa: BLE001 - the ledger write already happened
        logger.debug("Access Rules: couldn't read the export schedule: %s", exc)
        return ""
    when = None
    if status.next_run_at is not None:
        at = status.next_run_at
        at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
        when = at.strftime("%Y-%m-%d %H:%M UTC")
    if settings.RBAC_EXPORT_ON_CHANGE:
        return "The CSV export runs shortly" + (f"; the next scheduled run is at {when}." if when else ".")
    if when:
        return f"The next scheduled CSV export is at {when}."
    return "No CSV export is scheduled; the files update when someone exports from the Access Rules page."


# --- Chat preview ------------------------------------------------------------------

JUSTIFICATION_CODE = "justification_too_short"
_PROPOSAL_FIELDS = ("plane", "approver_group", "approver_fallback", "added_prerequisites", "outcomes",
                    "findings", "blocked", "report_markdown")


def preview_access_change(db: Session, change: AccessChangeIn, *, requester: Optional[str]) -> Dict[str, Any]:
    """The AccessPreview for the agent: one proposal per type, each with its report and what to submit.

    Row diffs stay out (the report shows them); ``submit_items`` is each
    proposal's ``items`` parameter (prerequisites are re-added when it's planned).
    """
    preview = ledger.plan_access_change(db, change, requester=requester)
    proposals: List[Dict[str, Any]] = []
    for proposal in preview.proposals:
        siblings = [(None, other.plane) for other in preview.proposals if other is not proposal]
        proposal.report_markdown = report.render_proposal_report(
            proposal, siblings=siblings, justification=change.justification, requester=requester,
        )
        denies = [f for f in proposal.findings if f.severity == "deny"]
        out = {k: v for k, v in proposal.model_dump(mode="json").items() if k in _PROPOSAL_FIELDS}
        out.update({
            "label": report.plane_label(proposal.plane),
            "summary": report.proposal_summary(proposal),
            "change_count": len(proposal.diffs),
            "needs_justification": any(f.code == JUSTIFICATION_CODE for f in denies),
            "blocked_by_policy": any(f.code != JUSTIFICATION_CODE for f in denies),
            "submit": bool(proposal.diffs),
            "submit_items": [i.model_dump(mode="json", exclude_none=True) for i in _requested_items(proposal)],
        })
        proposals.append(out)
    return {
        "group_ref": preview.group_ref,
        "proposals": proposals,
        "next_step": _next_step(proposals, preview.group_ref),
    }


def _next_step(proposals: Sequence[Dict[str, Any]], group_ref: Optional[str]) -> str:
    if any(p["blocked_by_policy"] for p in proposals):
        return ("Don't submit. Explain each blocked item from its findings and what would be allowed instead "
                "(for example a group rather than a user, or a narrower privilege).")
    to_file = [p for p in proposals if p["submit"]]
    if not to_file:
        return "Nothing to request: everything asked for is already in place. Tell the user; don't submit."
    if any(p["needs_justification"] for p in to_file):
        return ("Present the plan, then ask the user for their own justification (never write it for them) "
                "and preview again with it before submitting.")
    submit = (
        f"Then call execute_workflow once per proposal with submit=true ({len(to_file)} in total): workflow_type "
        f"'{WORKFLOW_ID}', parameters items = that proposal's submit_items and justification = the user's own words"
    )
    if group_ref and len(to_file) > 1:
        submit += (f", plus group_ref '{group_ref}' on every one. Say it becomes one request per type, each "
                   "approved by its own group, and file them together.")
    else:
        submit += "."
    return "Present each proposal's report_markdown and ask for an explicit yes. " + submit


# --- Filing ------------------------------------------------------------------------


def submit_access_change(
    db: Session,
    proposal: AccessProposal,
    *,
    requester_email: str,
    justification: str,
    group_ref: Optional[str] = None,
) -> str:
    """File one type's part of an access change for approval and return the request id.

    ``proposal`` comes from ``ledger.plan_access_change``. A mixed-type ask is
    one call per proposal with the same ``group_ref``. Raises AccessRequestError
    for a blocked or mixed-type proposal, nothing to change, or no justification.
    """
    reason = (justification or "").strip()
    if not reason:
        raise AccessRequestError("Add the requester's justification before filing an access change.")
    if proposal.blocked:
        raise AccessRequestError(f"This change can't be requested. {_deny_text(proposal.findings)}")
    stray = {cells.item_plane(i) for i in proposal.items} - {None, proposal.plane}
    if stray:
        raise AccessRequestError("This change spans more than one type of rule. Split by type: file one "
                                 "request per type with the same group_ref.")
    if not proposal.diffs:
        raise AccessRequestError("Nothing to request: everything asked for is already true in the ledger.")

    items = _requested_items(proposal)
    metadata: Dict[str, Any] = {
        "items": [i.model_dump(mode="json") for i in items],
        "justification": reason,
        "requested_by_email": requester_email,
        "requested_by": requester_email,
        PLAN_CONTEXT_KEY: {**proposal.model_dump(mode="json"), "kind": KIND_ITEMS},
    }
    if group_ref:
        metadata["group_ref"] = group_ref
    request_id = _file(db, title=_items_title(items), requester_email=requester_email, metadata=metadata)
    logger.info("Access Rules: %s filed access request %s (%s, %d item(s), group %s)",
                requester_email, request_id, proposal.plane, len(items), group_ref or "-")
    return request_id


def submit_row_changes(db: Session, change: ChangeSetIn, *, plane: str, requester_email: str) -> str:
    """File a direct edit held by the two-person rule; the editing admin is the requester.

    Re-checks the edit (one type, not blocked) and raises the ledger's errors
    (VersionConflict, ChangeBlocked, LedgerError) so the caller maps them as for a commit.
    """
    if plane not in PLANES:
        raise AccessRequestError(f"Unknown rule type '{plane}'.")
    planes = ledger.planes_in_change(db, change)
    if planes != {plane}:
        raise AccessRequestError("This edit spans more than one type of rule. Split by type: save each "
                                 "type's rows separately.")
    preview = ledger.preview_change_set(db, change, actor=requester_email)
    if preview.blocked:
        raise ChangeBlocked(preview)

    changes = change.model_dump(mode="json")
    label = report.plane_label(plane)
    count = len(preview.diffs) or len(change.changes)
    metadata: Dict[str, Any] = {
        "changes": changes,
        "plane": plane,
        "justification": change.reason or "",
        "requested_by_email": requester_email,
        "requested_by": requester_email,
        PLAN_CONTEXT_KEY: _direct_plan(preview, plane=plane),
    }
    title = f"Access Rules edit: {count} {label} row{'' if count == 1 else 's'} (second approval)"
    request_id = _file(db, title=title, requester_email=requester_email, metadata=metadata)

    from app.state_machines.facts import add_fact

    add_fact(db, request_id, DIRECT_EDIT_FACT,
             {"plane": plane, "editor": requester_email, "digest": _change_digest(change), "rows": count},
             actor=requester_email)
    logger.info("Access Rules: %s filed %s edit %s (%d rows) for a second approval",
                requester_email, plane, request_id, count)
    return request_id


def _direct_plan(preview: ChangeSetPreview, *, plane: str, report_markdown: str = "") -> Dict[str, Any]:
    return {
        "kind": KIND_CHANGES,
        "plane": plane,
        "approver_group": approvers.approver_group(plane),
        "diffs": [d.model_dump(mode="json") for d in preview.diffs],
        "findings": [f.model_dump(mode="json") for f in preview.findings],
        "blocked": preview.blocked,
        "report_markdown": report_markdown,
    }


def _diff_planes(preview: ChangeSetPreview) -> List[str]:
    found = {d.plane for d in preview.diffs}
    return [p for p in PLANES if p in found]


def _planes_label(planes: Sequence[str]) -> str:
    return " and ".join(report.plane_label(p) for p in planes) or "Access Rules"


def _revert_marker(request_id: str) -> str:
    """Appended to the revert change set's reason: ties it to the request in History and on a rerun."""
    return f"(request {request_id})"


def _open_revert(db: Session, change_set_id: str) -> Optional[str]:
    for row in inflight.open_requests(db):
        if (row.state_context or {}).get("revert_of") == change_set_id:
            return row.id
    return None


def submit_revert(
    db: Session, change_set_id: str, *, plane: Optional[str], requester_email: str, reason: str,
) -> str:
    """File a revert held by the two-person rule; the admin reverting is the requester.

    ``plane`` is the one type the change set touched, or None when it spans
    several: a revert can't be split, so Platform Admins approve those. Re-checks
    the revert (reason, policy, rows unchanged since) and raises the ledger's
    errors (VersionConflict, ChangeBlocked, LedgerError) so the caller maps them
    as for a direct revert.
    """
    if plane is not None and plane not in PLANES:
        raise AccessRequestError(f"Unknown rule type '{plane}'.")
    open_id = _open_revert(db, change_set_id)
    if open_id:
        raise AccessRequestError(f"Request {open_id} to revert this change set is already open.")
    preview = ledger.revert_preview(db, change_set_id, actor=requester_email, reason=reason)
    if preview.blocked:
        raise ChangeBlocked(preview)
    planes = _diff_planes(preview)
    matches = planes == [plane] if plane else len(planes) > 1
    if not matches:
        raise AccessRequestError("The revert's rule types don't match the change set. Open it in History "
                                 "and revert it again.")

    count = len(preview.diffs)
    metadata: Dict[str, Any] = {
        "revert_of": change_set_id,
        "plane": plane,
        "justification": (reason or "").strip(),
        "requested_by_email": requester_email,
        "requested_by": requester_email,
        PLAN_CONTEXT_KEY: _revert_plan(preview, change_set_id=change_set_id, planes=planes),
    }
    title = (f"Access Rules revert: {count} {_planes_label(planes)} row{'' if count == 1 else 's'} "
             f"from change set {change_set_id} (second approval)")
    request_id = _file(db, title=title, requester_email=requester_email, metadata=metadata)

    from app.state_machines.facts import add_fact

    add_fact(db, request_id, DIRECT_EDIT_FACT,
             {"kind": KIND_REVERT, "plane": plane, "editor": requester_email, "digest": change_set_id,
              "rows": count},
             actor=requester_email)
    logger.info("Access Rules: %s filed revert %s of change set %s (%s, %d rows) for a second approval",
                requester_email, request_id, change_set_id, ", ".join(planes), count)
    return request_id


def _revert_plan(
    preview: ChangeSetPreview, *, change_set_id: str, planes: List[str], report_markdown: str = "",
) -> Dict[str, Any]:
    single = len(planes) == 1
    return {
        "kind": KIND_REVERT,
        "revert_of": change_set_id,
        "plane": planes[0] if single else None,
        "planes": planes,
        "approver_group": approvers.approver_group(planes[0]) if single else None,
        "diffs": [d.model_dump(mode="json") for d in preview.diffs],
        "findings": [f.model_dump(mode="json") for f in preview.findings],
        "blocked": preview.blocked,
        "report_markdown": report_markdown,
    }


# --- Plan step ---------------------------------------------------------------------


def plan_step(
    request_id: Optional[str],
    *,
    items: Optional[List[Dict[str, Any]]] = None,
    justification: Optional[str] = None,
    group_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and plan the request, store the plan on it, and say who approves.

    Raises PermanentError (the request fails with the message) for invalid
    items, a change set spanning more than one type, or a blocked plan.
    """
    db = _session()
    try:
        request = _request(db, request_id)
        ctx = request.state_context or {}
        if ctx.get("revert_of"):
            return _plan_revert(db, request, ctx)
        if ctx.get("changes"):
            return _plan_direct_edit(db, request, ctx)
        return _plan_items(db, request, items, justification, group_ref)
    finally:
        db.close()


def _approvers(group: Optional[str]) -> List[str]:
    # Empty -> the gate's approval has no assignee, which only Platform Admins can act on.
    return [group] if group else []


def _plan_items(db: Session, request, items, justification, group_ref) -> Dict[str, Any]:
    if not items:
        raise PermanentError("This request lists no access changes. Submit it again with the items to change.")
    try:
        ask = AccessChangeIn.model_validate({"items": items, "justification": justification or ""})
    except ValidationError as exc:
        raise PermanentError(f"The access items aren't valid: {_validation_text(exc)}") from None
    requester = _requester(request)
    try:
        preview = ledger.plan_access_change(db, ask, requester=requester)
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None
    if len(preview.proposals) > 1:
        labels = ", ".join(report.plane_label(p.plane) for p in preview.proposals)
        raise PermanentError(
            f"This request changes more than one type of rule ({labels}). Split by type: file one request "
            "per type with the same group_ref, so each type's approvers review their own part."
        )
    proposal = preview.proposals[0]
    if proposal.blocked:
        raise PermanentError(f"This access change is blocked. {_deny_text(proposal.findings)}")
    proposal.findings = _without_self_overlap(db, proposal.findings, request.id)
    proposal.report_markdown = report.render_proposal_report(
        proposal, siblings=_siblings(db, request, group_ref), justification=ask.justification, requester=requester,
    )
    _store_plan(db, request, {**proposal.model_dump(mode="json"), "kind": KIND_ITEMS},
                title=_items_title(_requested_items(proposal)))
    logger.info("Access Rules: planned request %s (%s, %d row edit(s), approvers %s)",
                request.id, proposal.plane, len(proposal.diffs), proposal.approver_group or report.NO_GROUP)
    label = report.plane_label(proposal.plane)
    return {
        "plane": proposal.plane,
        "approver_group": proposal.approver_group,
        "approvers": _approvers(proposal.approver_group),
        "nothing_to_change": not proposal.diffs,
        "change_count": len(proposal.diffs),
        "summary": report.proposal_summary(proposal),
        "report_title": f"{label} access change",
        "report_markdown": proposal.report_markdown,
    }


def _filing_matches(db: Session, request_id: str, *, kind: str, plane: Optional[str], digest: str) -> bool:
    """Whether the server's filing fact matches. Raises RetryableError while there's none."""
    from app.state_machines.facts import get_latest_fact

    fact = get_latest_fact(db, request_id, DIRECT_EDIT_FACT)
    if fact is None:
        # submit_row_changes / submit_revert add the fact just after creating the
        # request; one filed any other way never gets it and fails after the retries.
        noun = "revert" if kind == KIND_REVERT else "direct edit"
        raise RetryableError(f"This {noun} has no filing record from the Access Rules page yet.")
    data = fact.event_data or {}
    return (data.get("kind", KIND_CHANGES) == kind and data.get("plane") == plane
            and data.get("digest") == digest)


def _check_filed_by_editor(db: Session, request_id: str, plane: str, change: ChangeSetIn) -> None:
    if not _filing_matches(db, request_id, kind=KIND_CHANGES, plane=plane, digest=_change_digest(change)):
        raise PermanentError("This edit no longer matches what was saved on the Access Rules page. "
                             "Re-edit the rows there and save again.")


def _revert_again(change_set_id: str) -> str:
    return f"Open change set {change_set_id} in History on the Access Rules page and revert it again."


def _plan_revert(db: Session, request, ctx: Dict[str, Any]) -> Dict[str, Any]:
    change_set_id = str(ctx.get("revert_of"))
    plane = ctx.get("plane") or None
    if plane is not None and plane not in PLANES:
        raise PermanentError("This revert names an unknown type of rule.")
    if not _filing_matches(db, request.id, kind=KIND_REVERT, plane=plane, digest=change_set_id):
        raise PermanentError("This revert no longer matches what was filed. " + _revert_again(change_set_id))
    original = db.get(RbacChangeSetModel, change_set_id)
    if original is None:
        raise PermanentError(f"Change set {change_set_id} doesn't exist, so it can't be reverted.")
    editor = _requester(request)
    reason = (ctx.get("justification") or "").strip()
    try:
        preview = ledger.revert_preview(db, change_set_id, actor=editor, reason=reason)
    except VersionConflict as exc:
        raise PermanentError(f"{exc} Rows in that change set changed after the revert was filed. "
                             + _revert_again(change_set_id)) from None
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None
    if preview.blocked:
        raise PermanentError(f"This revert is blocked. {_deny_text(preview.findings)}")
    planes = _diff_planes(preview)
    if plane is not None and planes != [plane]:
        raise PermanentError("This revert no longer matches what was filed. " + _revert_again(change_set_id))
    preview.findings = _without_self_overlap(db, preview.findings, request.id)
    group = approvers.approver_group(planes[0]) if len(planes) == 1 else None
    markdown = report.render_revert_report(
        preview, change_set_id=change_set_id, original_reason=original.reason, original_actor=original.actor,
        planes=planes, approver_group=group, editor=editor, reason=reason,
    )
    _store_plan(db, request, _revert_plan(preview, change_set_id=change_set_id, planes=planes,
                                          report_markdown=markdown))
    logger.info("Access Rules: planned revert %s of change set %s (%s, %d rows, approvers %s)",
                request.id, change_set_id, ", ".join(planes), len(preview.diffs),
                group or "Platform Admins")
    label = _planes_label(planes)
    return {
        "plane": plane,
        "approver_group": group,
        "approvers": _approvers(group),
        # A revert always has rows (the ledger refuses an empty one), so it always waits for approval.
        "nothing_to_change": False,
        "change_count": len(preview.diffs),
        "summary": (f"{label}: revert of change set {change_set_id} ({len(preview.diffs)} row(s)) by {editor}, "
                    "waiting for a second approval."),
        "report_title": f"{label} revert",
        "report_markdown": markdown,
    }


def _plan_direct_edit(db: Session, request, ctx: Dict[str, Any]) -> Dict[str, Any]:
    plane = ctx.get("plane")
    if plane not in PLANES:
        raise PermanentError("This direct edit doesn't say which type of rule it changes.")
    try:
        change = ChangeSetIn.model_validate(ctx.get("changes"))
    except ValidationError as exc:
        raise PermanentError(f"The saved edit isn't valid: {_validation_text(exc)}") from None
    _check_filed_by_editor(db, request.id, plane, change)
    editor = _requester(request)
    try:
        planes = ledger.planes_in_change(db, change)
        if planes != {plane}:
            raise PermanentError("This edit spans more than one type of rule. Split by type: save each "
                                 "type's rows separately.")
        preview = ledger.preview_change_set(db, change, actor=editor)
    except VersionConflict as exc:
        raise PermanentError(f"{exc} This edit can't be approved as saved. Re-edit the rows on the "
                             "Access Rules page and save again.") from None
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None
    if preview.blocked:
        raise PermanentError(f"This edit is blocked. {_deny_text(preview.findings)}")
    preview.findings = _without_self_overlap(db, preview.findings, request.id)
    group = approvers.approver_group(plane)
    markdown = report.render_change_set_report(preview, plane=plane, approver_group=group, editor=editor,
                                               reason=change.reason)
    _store_plan(db, request, _direct_plan(preview, plane=plane, report_markdown=markdown))
    logger.info("Access Rules: planned direct edit %s (%s, %d rows, approvers %s)",
                request.id, plane, len(preview.diffs), group or report.NO_GROUP)
    label = report.plane_label(plane)
    return {
        "plane": plane,
        "approver_group": group,
        "approvers": _approvers(group),
        "nothing_to_change": not preview.diffs,
        "change_count": len(preview.diffs),
        "summary": f"{label}: direct edit of {len(preview.diffs)} row(s) by {editor}, waiting for a second approval.",
        "report_title": f"{label} direct edit",
        "report_markdown": markdown,
    }


# --- Apply step --------------------------------------------------------------------


def apply_step(request_id: Optional[str]) -> Dict[str, Any]:
    """Write the approved plan to the ledger. Idempotent on the request id."""
    db = _session()
    try:
        request = _request(db, request_id)
        plan = (request.state_context or {}).get(PLAN_CONTEXT_KEY)
        if not isinstance(plan, dict) or not plan:
            raise PermanentError("This request has no stored plan, so there's nothing approved to apply.")
        _check_plan_unchanged(db, request, plan)
        if plan.get("kind") == KIND_REVERT:
            result = _apply_revert(db, request, plan)
        elif plan.get("kind") == KIND_CHANGES:
            result = _apply_direct_edit(db, request, plan)
        else:
            result = _apply_items(db, request, plan)
        if result.get("change_set_id") and not result.get("already_applied"):
            note = export_note(db)
            if note:
                result["message"] = f"{result['message']} {note}"
        return result
    finally:
        db.close()


def _same_person(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _two_person_diffs(diffs: Sequence[Any]) -> bool:
    for diff in diffs:
        keys = {diff.environment, (diff.before or {}).get("environment")}
        for key in keys:
            env = layout.environment(key) if key else None
            if env is not None and env.two_person_rule:
                return True
    return False


def _existing_change_set(db: Session, request_id: str) -> Optional[RbacChangeSetModel]:
    return (
        db.query(RbacChangeSetModel)
        .filter(RbacChangeSetModel.source == SOURCE_REQUEST, RbacChangeSetModel.source_ref == request_id)
        .order_by(RbacChangeSetModel.created_at)
        .first()
    )


def _apply_items(db: Session, request, plan: Dict[str, Any]) -> Dict[str, Any]:
    try:
        approved = AccessProposal.model_validate({k: v for k, v in plan.items() if k != "kind"})
    except ValidationError as exc:
        raise PermanentError(f"The stored plan isn't valid: {_validation_text(exc)}") from None
    approver = _approver(db, request.id)
    requester = _requester(request)
    if _two_person_diffs(approved.diffs) and (not approver or _same_person(approver, requester)):
        raise PermanentError("This change touches an environment with the two-person rule, so someone other "
                             "than the requester must approve it.")
    actor = approver or requester
    try:
        result = ledger.apply_access_change(db, request_id=request.id, approved=approved, actor=actor)
    except ChangeBlocked as exc:
        raise PermanentError(f"An access policy now blocks this change. {_deny_text(exc.preview.findings)}") from None
    except VersionConflict as exc:
        raise RetryableError(str(exc)) from None
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None

    skipped = [
        {"index": s.index, "status": s.status, "reason": s.reason, "item": _plain_item(s.item)}
        for s in result.skipped
    ]
    if result.already_applied:
        message = f"Already applied as change set {result.change_set_id}."
    elif result.change_set_id:
        message = f"Updated {len(result.applied)} ledger row(s) in change set {result.change_set_id}."
    else:
        message = "Nothing changed: everything requested was already true in the ledger."
    if skipped and not result.already_applied:
        message += " Skipped: " + "; ".join(f"{s['item']} ({s['reason']})" for s in skipped) + "."
    logger.info("Access Rules: request %s apply -> change set %s (%d rows, %d skipped, already applied %s)",
                request.id, result.change_set_id, len(result.applied), len(skipped), result.already_applied)
    return {
        "change_set_id": result.change_set_id,
        "applied_rows": len(result.applied),
        "skipped": skipped,
        "already_applied": result.already_applied,
        "message": message,
    }


def _apply_direct_edit(db: Session, request, plan: Dict[str, Any]) -> Dict[str, Any]:
    existing = _existing_change_set(db, request.id)
    if existing is not None:
        return {"change_set_id": existing.id, "applied_rows": 0, "skipped": [], "already_applied": True,
                "message": f"Already applied as change set {existing.id}."}
    ctx = request.state_context or {}
    try:
        change = ChangeSetIn.model_validate(ctx.get("changes"))
    except ValidationError as exc:
        raise PermanentError(f"The saved edit isn't valid: {_validation_text(exc)}") from None
    _check_filed_by_editor(db, request.id, str(plan.get("plane")), change)
    editor = _requester(request)
    approver = _approver(db, request.id)
    if not plan.get("diffs"):
        # Approved as "nothing to change": never write rows nobody reviewed.
        return {"change_set_id": None, "applied_rows": 0, "skipped": [], "already_applied": False,
                "message": "Nothing changed: the edit matched the ledger already."}
    if not approver or _same_person(approver, editor):
        raise PermanentError("The two-person rule needs someone other than the editor to approve this edit.")
    try:
        result = ledger.commit_change_set(db, change, actor=editor, source=SOURCE_REQUEST, source_ref=request.id)
    except VersionConflict as exc:
        raise PermanentError(f"{exc} The approved edit no longer matches the ledger. Re-edit the rows on "
                             "the Access Rules page and save again.") from None
    except ChangeBlocked as exc:
        raise PermanentError(f"An access policy now blocks this edit. {_deny_text(exc.preview.findings)}") from None
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None
    if result.change_set_id:
        message = f"Updated {len(result.diffs)} ledger row(s) in change set {result.change_set_id}."
    else:
        message = "Nothing changed: the edit matches the ledger already."
    logger.info("Access Rules: direct edit %s by %s applied as change set %s (%d rows, approved by %s)",
                request.id, editor, result.change_set_id, len(result.diffs), approver or "auto")
    return {
        "change_set_id": result.change_set_id,
        "applied_rows": len(result.diffs),
        "skipped": [],
        "already_applied": False,
        "message": message,
    }


def _apply_revert(db: Session, request, plan: Dict[str, Any]) -> Dict[str, Any]:
    ctx = request.state_context or {}
    change_set_id = ctx.get("revert_of")
    if not change_set_id or plan.get("revert_of") != change_set_id:
        raise PermanentError("This revert's stored plan doesn't match the request, so there's nothing approved "
                             "to apply.")
    original = db.get(RbacChangeSetModel, change_set_id)
    if original is None:
        raise PermanentError(f"Change set {change_set_id} doesn't exist, so it can't be reverted.")
    marker = _revert_marker(request.id)
    if original.reverted_by:
        done = db.get(RbacChangeSetModel, original.reverted_by)
        if done is not None and marker in (done.reason or ""):
            return {"change_set_id": done.id, "applied_rows": 0, "skipped": [], "already_applied": True,
                    "message": f"Already applied: change set {change_set_id} was reverted as change set {done.id}."}
        raise PermanentError(f"Change set {change_set_id} was already reverted outside this request (change set "
                             f"{original.reverted_by}), so there's nothing left to apply.")
    editor = _requester(request)
    approver = _approver(db, request.id)
    if not approver or _same_person(approver, editor):
        raise PermanentError("The two-person rule needs someone other than the requester to approve this revert.")
    reason = (ctx.get("justification") or "").strip()
    try:
        result = ledger.revert_change_set(db, change_set_id, actor=editor, reason=f"{reason} {marker}".strip())
    except VersionConflict as exc:
        raise PermanentError(f"{exc} Rows in that change set changed after the revert was approved, so it can't "
                             "be applied. " + _revert_again(change_set_id)) from None
    except ChangeBlocked as exc:
        raise PermanentError(f"An access policy now blocks this revert. {_deny_text(exc.preview.findings)}") from None
    except LedgerError as exc:
        raise PermanentError(str(exc)) from None
    logger.info("Access Rules: revert %s by %s applied: change set %s reverted as %s (%d rows, approved by %s)",
                request.id, editor, change_set_id, result.change_set_id, len(result.diffs), approver)
    return {
        "change_set_id": result.change_set_id,
        "applied_rows": len(result.diffs),
        "skipped": [],
        "already_applied": False,
        "message": (f"Reverted change set {change_set_id}: updated {len(result.diffs)} ledger row(s) in change set "
                    f"{result.change_set_id}."),
    }
