"""Apply an actor's ``authz.BrowseScope`` to what the change services return.

Anyone may file a change against any object (it is routed to the object's owners),
but the preview, result, conflicts and change sets they get back must not show
the current values of objects outside their browse scope. The change services
keep working on full state; the API and tools pass their output through here.
Approvers are unaffected: the filed request keeps the full preview.
"""
from __future__ import annotations

from typing import List, Optional

from app.services.metadata import report
from app.services.metadata.authz import BrowseScope
from app.services.metadata.errors import Forbidden, NotFound
from app.services.metadata.types import (
    ChangePreview,
    ChangeResult,
    ChangeSet,
    ItemDecision,
    VersionConflictItem,
)

HIDDEN_REASON = "You can't browse this object, so its current values aren't shown."
HIDDEN_SENSITIVE = "A sensitive change: it needs approval."
# Findings that describe an object's current state rather than the requested change.
_STATE_FINDINGS = frozenset({"unmanaged_object"})
_KEPT_NOTE = " Unity Catalog already has this value, so it's kept as is."


def _hide_item(decision: ItemDecision) -> ItemDecision:
    item = decision.item.model_copy(update={
        "before": None, "before_exists": False, "before_version": 0, "unchanged_in_uc": False,
        "before_hidden": True,
    })
    d = decision.decision
    authz = d.model_copy(update={
        "reasons": [HIDDEN_REASON, *[r for r in d.reasons if "already has" not in r]],
        "sensitive_reasons": [HIDDEN_SENSITIVE] if d.sensitive_reasons else [],
    })
    findings = [f.model_copy(update={"message": f.message.replace(_KEPT_NOTE, "")}) for f in decision.findings]
    return ItemDecision(item=item, decision=authz, findings=findings)


def redact_preview(preview: Optional[ChangePreview], browse: BrowseScope, *, reason: str = "",
                   title: str = "Metadata change") -> Optional[ChangePreview]:
    """A copy without the current values of objects ``browse`` can't see; ``preview`` itself when it shows none."""
    if preview is None or browse.full:
        return preview
    sees = browse.predicate()
    hidden_items = [not sees(d.item.fqn_key) for d in preview.items]
    hidden_findings = [f.code in _STATE_FINDINGS and bool(f.fqn_key) and not sees(f.fqn_key)
                       for f in preview.findings]
    if not any(hidden_items) and not any(hidden_findings):
        return preview
    from app.services.metadata.changes import items_digest

    items = [_hide_item(d) if hidden else d for d, hidden in zip(preview.items, hidden_items)]
    out = preview.model_copy(update={
        "items": items,
        # The original hashes the hidden values, which a short value list could be checked against.
        "digest": items_digest([d.item for d in items]),
        "findings": [f for f, hidden in zip(preview.findings, hidden_findings) if not hidden],
        "kept_count": sum(1 for d in items if d.item.unchanged_in_uc and report.bucket(d) == report.DIRECT),
    })
    out.report_markdown = report.build_report(out, reason=reason, title=title)
    return out


def redact_change_set(change_set: Optional[ChangeSet], browse: BrowseScope) -> Optional[ChangeSet]:
    """Only the events on objects ``browse`` can see."""
    if change_set is None or browse.full:
        return change_set
    sees = browse.predicate()
    return change_set.model_copy(update={"events": [e for e in change_set.events if sees(e.fqn_key)]})


def redact_result(result: ChangeResult, browse: BrowseScope, *, reason: str = "",
                  title: str = "Metadata change") -> ChangeResult:
    if browse.full:
        return result
    return result.model_copy(update={
        "preview": redact_preview(result.preview, browse, reason=reason, title=title),
        "change_set": redact_change_set(result.change_set, browse),
    })


def redact_conflicts(conflicts: List[VersionConflictItem], browse: BrowseScope) -> List[VersionConflictItem]:
    if browse.full:
        return conflicts
    sees = browse.predicate()
    return [c if sees(c.fqn_key) else c.model_copy(update={"current_value": None}) for c in conflicts]


def visible_change_set(change_set: ChangeSet, browse: BrowseScope) -> ChangeSet:
    """``change_set`` with only the visible events; NotFound (as for a missing one) when none is visible."""
    if browse.full:
        return change_set
    shown = redact_change_set(change_set, browse)
    if not shown.events:
        raise NotFound(f"Change set {change_set.id} doesn't exist.")
    return shown


def require_whole_change_set(change_set: ChangeSet, browse: BrowseScope) -> None:
    """A revert restores old values, so it needs every object of the change set to be visible."""
    if browse.full:
        return
    shown = visible_change_set(change_set, browse)
    if len(shown.events) != len(change_set.events):
        raise Forbidden("This change set also changed objects you can't browse, so only governance can revert it.")
