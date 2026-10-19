"""The approver-facing report for an ``rbac_access_change`` request (ADR 0005 D10).

The workflow's prepare step returns it as ``report_markdown``, so it shows on the
approval card. Everything quoted from a requester, an editing admin or the
ledger (principals, securables, sheet names, justifications, finding messages)
is untrusted and goes through ``safe_text`` / ``safe_code``. Plain bullet lists,
no tables: a ``|`` inside a code span would still split a table cell.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.db.rbac import PLANE_WORKSPACE
from app.models.rbac import (
    CANONICAL_FIELDS,
    AccessItem,
    AccessProposal,
    ChangeSetPreview,
    Finding,
    ItemOutcome,
    RowDiff,
)
from app.services.app_code_review.report import safe_code, safe_text
from app.services.rbac import cells, layout

# Each section lists at most this many entries; the rest are counted.
MAX_ENTRIES = 40

NO_GROUP = "No approver group set — Platform Admins approve."

# (request id, or None for a sibling not filed yet; plane)
Sibling = Tuple[Optional[str], str]

_FIELD_LABELS = {
    "environment_value": "Environment",
    "securable_type": "Securable type",
    "catalog": "Catalog",
    "schema_name": "Schema",
    "object_name": "Object",
    "workspace_name": "Workspace",
    "owner": "Owner",
    "principal_type": "Principal type",
}


def plane_label(plane: Optional[str]) -> str:
    return layout.PLANE_LABELS.get(plane or "", plane or "access")


def approver_line(group: Optional[str]) -> str:
    return f"Approvers: members of {safe_code(group)}." if group else f"Approvers: {NO_GROUP}"


# --- Pieces ------------------------------------------------------------------------


def _cell(value: Any) -> str:
    return safe_code(value) or "(empty)"


def _securable(obj: Any, plane: str) -> str:
    """``schema `main.sales```, ``folder `/Shared` in workspace `ws-dev```."""
    kind = safe_text((getattr(obj, "securable_type", "") or "").strip() or "securable", 60)
    if plane == PLANE_WORKSPACE:
        name = (getattr(obj, "object_name", "") or "").strip()
        workspace = (getattr(obj, "workspace_name", "") or "").strip()
        if name and workspace:
            return f"{kind} {safe_code(name)} in workspace {safe_code(workspace)}"
        target = name or workspace
    else:
        parts = [
            (getattr(obj, f, "") or "").strip()
            for f in layout.SECURABLE_FIELDS.get(plane, ())
            if f not in ("securable_type", "workspace_name")
        ]
        target = ".".join(p for p in parts if p)
    return f"{kind} {safe_code(target)}".strip()


def _principal(principal_type: Optional[str], name: Optional[str]) -> str:
    kind = cells.principal_type_key(principal_type) or "group"
    return f"{safe_text(kind, 40)} {_cell(name)}"


def item_line(item: AccessItem, plane: str) -> str:
    """``Grant `USE_SCHEMA;SELECT` on schema `main.sales` to group `analysts` in `dev```."""
    grant = item.action == "grant"
    verb = "Grant" if grant else "Revoke"
    where = f" in {safe_code(item.environment)}" if (item.environment or "").strip() else ""
    return (
        f"{verb} {_cell(cells.join_cell(item.privileges))} on {_securable(item, plane)} "
        f"{'to' if grant else 'from'} {_principal(item.principal_type, item.principal_name)}{where}"
    )


def _snapshot(snap: Optional[Dict[str, Any]]) -> SimpleNamespace:
    snap = snap or {}
    return SimpleNamespace(**{f: snap.get(f) or "" for f in CANONICAL_FIELDS}, extra=snap.get("extra") or {})


def _packed_delta(before: str, after: str) -> str:
    """``+ `a`, `b`; − `c``` for a packed principal / privilege cell, case-insensitive."""
    old = {cells.cell_key(v) for v in cells.split_cell(before)}
    new = {cells.cell_key(v) for v in cells.split_cell(after)}
    added = [v for v in cells.split_cell(after) if cells.cell_key(v) not in old]
    removed = [v for v in cells.split_cell(before) if cells.cell_key(v) not in new]
    parts = []
    if added:
        parts.append("+ " + ", ".join(safe_code(v) for v in added))
    if removed:
        parts.append("− " + ", ".join(safe_code(v) for v in removed))
    return "; ".join(parts) or "reordered"


def diff_lines(diff: RowDiff) -> List[str]:
    """A compact before / after for one row: what, where (sheet), and which cells change."""
    before, after = _snapshot(diff.before), _snapshot(diff.after)
    row = before if diff.op == "delete" else after
    where = f"{_securable(row, diff.plane)} on sheet {_cell(diff.sheet)}"
    if diff.op == "create":
        return [
            f"- **New row**: {where}",
            f"  - {_principal(after.principal_type, after.principal_name)} · privileges {_cell(after.privilege)}",
        ]
    if diff.op == "delete":
        return [
            f"- **Row removed**: {where}",
            f"  - was {_principal(before.principal_type, before.principal_name)} · privileges "
            f"{_cell(before.privilege)}",
        ]
    head = "**Row restored**" if diff.op == "restore" else "**Row edited**"
    lines = [f"- {head}: {where}"]
    for field, label in (("principal_name", "Principals"), ("privilege", "Privileges")):
        old, new = getattr(before, field), getattr(after, field)
        if cells.cell_key(old) == cells.cell_key(new):
            lines.append(f"  - {label}: {_cell(new)} (unchanged)")
        else:
            lines.append(f"  - {label}: {_packed_delta(old, new)} (now {_cell(new)})")
    for field, label in _FIELD_LABELS.items():
        old, new = getattr(before, field), getattr(after, field)
        if diff.before is not None and (old or "").strip() != (new or "").strip():
            lines.append(f"  - {label}: {_cell(old)} → {_cell(new)}")
    if diff.before is not None and (before.extra or {}) != (after.extra or {}):
        lines.append("  - Other columns on this row change too.")
    return lines


def _capped(entries: Sequence[List[str]], noun: str) -> List[str]:
    lines = [line for entry in entries[:MAX_ENTRIES] for line in entry]
    if len(entries) > MAX_ENTRIES:
        lines.append(f"- …and {len(entries) - MAX_ENTRIES} more {noun}.")
    return lines


def _section(title: str, lines: List[str]) -> List[str]:
    return ["", f"**{title}**", *lines] if lines else []


def _finding_lines(findings: Iterable[Finding]) -> List[List[str]]:
    return [[f"- {safe_text(f.message)}"] for f in findings]


def _is_prerequisite(outcome: ItemOutcome, added: Sequence[AccessItem]) -> bool:
    return any(outcome.item == p for p in added)


def _sibling_lines(siblings: Sequence[Sibling]) -> List[str]:
    lines = []
    for request_id, plane in siblings:
        filed = f"request {safe_code(request_id)}" if request_id else "filed alongside this one"
        lines.append(f"- {safe_text(plane_label(plane), 60)}: {filed}")
    return lines


# --- Reports -----------------------------------------------------------------------


def render_proposal_report(
    proposal: AccessProposal,
    *,
    siblings: Sequence[Sibling] = (),
    justification: Optional[str] = None,
    requester: Optional[str] = None,
) -> str:
    """Markdown for one type's part of an access change, as approvers see it."""
    plane = proposal.plane
    requested = [o for o in proposal.outcomes
                 if o.status == "change" and not _is_prerequisite(o, proposal.added_prerequisites)]
    noops = [o for o in proposal.outcomes if o.status == "noop"]
    blocked = [o for o in proposal.outcomes if o.status == "blocked"]
    # A deny tied to an item is already its "blocked" reason; list the rest.
    loose_denies = [f for f in proposal.findings if f.severity == "deny" and f.change_index is None]
    warnings = [f for f in proposal.findings if f.severity == "warn"]

    lines = [
        f"**{safe_text(plane_label(plane), 60)} access change**",
        "",
        f"- {approver_line(proposal.approver_group)}",
    ]
    if requester:
        lines.append(f"- Requested by: {safe_code(requester)}")
    reason = (justification or "").strip()
    lines.append(f"- Justification: {safe_text(reason, 1500)}" if reason else "- Justification: none given")

    lines += _section("Requested changes", _capped([[f"- {item_line(o.item, plane)}"] for o in requested], "changes"))
    lines += _section(
        "Prerequisites added",
        _capped([[f"- {item_line(p, plane)}"] for p in proposal.added_prerequisites], "prerequisites")
        + (["- These give access to the containing catalog or schema and are part of this request."]
           if proposal.added_prerequisites else []),
    )
    lines += _section("Ledger rows", _capped([diff_lines(d) for d in proposal.diffs], "rows"))
    lines += _section(
        "Already true (no change)",
        _capped([[f"- {item_line(o.item, plane)} — {safe_text(o.reason or 'no change', 200)}"] for o in noops], "items"),
    )
    lines += _section(
        "Blocked",
        _capped([[f"- {item_line(o.item, plane)} — {safe_text(o.reason or 'blocked', 300)}"] for o in blocked]
                + _finding_lines(loose_denies), "items"),
    )
    lines += _section("Warnings", _capped(_finding_lines(warnings), "warnings"))
    if siblings:
        lines += _section(
            "Linked requests",
            ["- The same ask also changes other types of rules. Each type's approvers decide their part on "
             "its own request.", *_sibling_lines(siblings)],
        )
    return "\n".join(lines).strip() + "\n"


def render_change_set_report(
    preview: ChangeSetPreview,
    *,
    plane: str,
    approver_group: Optional[str],
    editor: Optional[str],
    reason: Optional[str],
) -> str:
    """Markdown for a direct edit held for a second approval (the two-person rule)."""
    warnings = [f for f in preview.findings if f.severity == "warn"]
    denies = [f for f in preview.findings if f.severity == "deny"]
    lines = [
        f"**{safe_text(plane_label(plane), 60)} direct edit (second approval)**",
        "",
        f"- {approver_line(approver_group)}",
        f"- Edited by: {_cell(editor)}. The editor can't approve their own edit.",
    ]
    text = (reason or "").strip()
    lines.append(f"- Reason: {safe_text(text, 1500)}" if text else "- Reason: none given")
    lines += _section("Ledger rows", _capped([diff_lines(d) for d in preview.diffs], "rows"))
    lines += _section("Blocked", _capped(_finding_lines(denies), "findings"))
    lines += _section("Warnings", _capped(_finding_lines(warnings), "warnings"))
    return "\n".join(lines).strip() + "\n"


def render_revert_report(
    preview: ChangeSetPreview,
    *,
    change_set_id: str,
    original_reason: Optional[str],
    original_actor: Optional[str],
    planes: Sequence[str],
    approver_group: Optional[str],
    editor: Optional[str],
    reason: Optional[str],
) -> str:
    """Markdown for a revert held for a second approval (the two-person rule).

    A revert spanning more than one type has no single approver group, so it goes to Platform Admins.
    """
    warnings = [f for f in preview.findings if f.severity == "warn"]
    denies = [f for f in preview.findings if f.severity == "deny"]
    single = len(planes) == 1
    label = plane_label(planes[0]) if single else "Access Rules"
    if single:
        who = approver_line(approver_group)
    else:
        kinds = " and ".join(plane_label(p) for p in planes)
        who = f"Approvers: Platform Admins, because this revert changes {safe_text(kinds, 120)} rules."
    lines = [
        f"**{safe_text(label, 60)} revert (second approval)**",
        "",
        f"- Reverts change set {_cell(change_set_id)}, made by {_cell(original_actor)}.",
    ]
    original = (original_reason or "").strip()
    lines.append(f"- Original reason: {safe_text(original, 1500)}" if original else "- Original reason: none recorded")
    lines += [
        f"- {who}",
        f"- Requested by: {_cell(editor)}. The requester can't approve their own revert.",
    ]
    text = (reason or "").strip()
    lines.append(f"- Reason for reverting: {safe_text(text, 1500)}" if text else "- Reason for reverting: none given")
    lines += _section("Ledger rows", _capped([diff_lines(d) for d in preview.diffs], "rows"))
    lines += _section("Blocked", _capped(_finding_lines(denies), "findings"))
    lines += _section("Warnings", _capped(_finding_lines(warnings), "warnings"))
    return "\n".join(lines).strip() + "\n"


def proposal_summary(proposal: AccessProposal) -> str:
    """One plain sentence for the agent: counts and who approves."""
    prereqs = len(proposal.added_prerequisites)
    changes = sum(1 for o in proposal.outcomes
                  if o.status == "change" and not _is_prerequisite(o, proposal.added_prerequisites))
    noops = sum(1 for o in proposal.outcomes if o.status == "noop")
    blocked = sum(1 for o in proposal.outcomes if o.status == "blocked")
    parts = [f"{changes} change{'' if changes == 1 else 's'}"]
    if prereqs:
        parts.append(f"{prereqs} prerequisite{'' if prereqs == 1 else 's'} added")
    if noops:
        parts.append(f"{noops} already true")
    if blocked:
        parts.append(f"{blocked} blocked")
    group = proposal.approver_group
    who = f"members of {group}" if group else "Platform Admins (no approver group set)"
    state = "Blocked by policy" if proposal.blocked else "Approvers: " + who
    return f"{plane_label(proposal.plane)}: {', '.join(parts)}. {state}."
