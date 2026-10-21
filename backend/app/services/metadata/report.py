"""Approver-facing markdown for a metadata change. Workstream A.

Names, values and reasons come from callers, so every one is rendered with
``safe_text`` / ``safe_code`` (``app.services.app_code_review.report``). Items go
in a GFM table, and every unescaped ``|`` in a cell (code spans included) is
escaped, so a value can't split a cell.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from app.services.app_code_review.report import safe_code, safe_text
from app.services.metadata.types import ChangeItem, ChangePreview, Finding, ItemDecision

# The table lists at most this many items; the rest are counted.
MAX_ROWS = 50
MAX_FINDINGS = 30

DIRECT = "direct"
APPROVAL = "approval"
DENIED = "denied"

_DECISION_LABELS = {DIRECT: "Direct", APPROVAL: "Needs approval", DENIED: "Denied"}
_SEVERITY_LABELS = {"deny": "Blocked", "warn": "Warning", "info": "Note"}
# A pipe after an even number of backslashes (none included) would end a table cell.
_CELL_PIPE = re.compile(r"(?<!\\)((?:\\\\)*)\|")


def bucket(decision: ItemDecision) -> str:
    """How submit routes an item: ``direct`` (write now), ``approval`` (file a request) or ``denied``.

    An item with a deny finding is denied whatever authorization said.
    """
    d = decision.decision
    if any(f.severity == "deny" for f in decision.findings):
        return DENIED
    if d.allowed and not d.needs_approval:
        return DIRECT
    return APPROVAL if d.can_request else DENIED


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _cell(value: Any, limit: int = 120) -> str:
    return safe_code(value, limit)


def _escape_pipes(cell: str) -> str:
    return _CELL_PIPE.sub(r"\1\\|", cell)


def _display(item: ChangeItem) -> str:
    return f"{item.name}.{item.column}" if item.column else item.name


def _object(item: ChangeItem) -> str:
    text = _cell(_display(item), 200)
    notes: List[str] = []
    if item.environment:
        notes.append(_cell(item.environment, 40))
    if item.copied_from:
        notes.append(f"copied from {_cell(item.copied_from, 40)}")
    if item.object_state == "pending":
        notes.append("not created yet")
    return f"{text} ({', '.join(notes)})" if notes else text


def _before(item: ChangeItem) -> str:
    if not item.before_exists:
        return "(none)"
    if item.before is None:
        return "(key only)" if item.field == "tag" else "(none)"
    return _cell(item.before, 80 if item.field == "comment" else 120)


def _after(item: ChangeItem) -> str:
    if item.action == "unset":
        return "removed"
    if item.action == "comment_clear":
        return "(cleared)"
    if item.after is None:
        return "(key only)"
    return _cell(item.after, 80 if item.field == "comment" else 120)


def _reasons(decision: ItemDecision) -> str:
    texts = [*decision.decision.sensitive_reasons, *decision.decision.reasons, *(f.message for f in decision.findings)]
    return "; ".join(dict.fromkeys(safe_text(t, 200) for t in texts if (t or "").strip()))


def _row(decision: ItemDecision) -> str:
    item = decision.item
    change = f"tag {_cell(item.key, 80)}" if item.field == "tag" else "description"
    label = _DECISION_LABELS[bucket(decision)]
    via = decision.decision.via
    if via and via != "none" and bucket(decision) != DENIED:
        label = f"{label} ({safe_text(via, 20)})"
    cells = [_object(item), change, f"{_before(item)} → {_after(item)}", label, _reasons(decision)]
    return "| " + " | ".join(_escape_pipes(c) for c in cells) + " |"


def _finding_line(finding: Finding) -> str:
    label = _SEVERITY_LABELS.get(finding.severity, "Note")
    return f"- {label}: {safe_text(finding.message, 400)}"


def _approvers(groups: List[str]) -> str:
    if not groups:
        return "- Approvers: no approver group is set for these objects."
    return "- Approvers: members of " + ", ".join(safe_code(g, 120) for g in groups)


def _risk_line(risk: Optional[dict]) -> Optional[str]:
    if not risk or risk.get("band") is None:
        return None
    try:
        score = int(risk.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return f"- Risk: {safe_text(risk.get('band'), 20)} ({score}/100)"


def build_report(preview: ChangePreview, *, reason: str = "", title: str = "Metadata change") -> str:
    summary = (
        f"- {_count(preview.object_count, 'object')}: {preview.direct_count} direct, "
        f"{preview.approval_count} need approval, {preview.denied_count} denied"
    )
    if preview.noop_count or preview.kept_count:
        summary += f", {preview.noop_count + preview.kept_count} already true"
    lines = [f"**{safe_text(title, 120) or 'Metadata change'}**", "", summary + "."]
    if preview.environments:
        lines.append("- Environments: " + ", ".join(safe_code(e, 60) for e in preview.environments))
    text = (reason or "").strip()
    lines.append(f"- Reason: {safe_text(text, 1500)}" if text else "- Reason: none given")
    risk = _risk_line(preview.risk)
    if risk:
        lines.append(risk)
    if preview.blocked:
        lines.append("- **Blocked by policy**: nothing can be saved or requested until the findings below are fixed.")
    if preview.needs_approval:
        lines.append(_approvers(preview.approver_groups))

    if preview.items:
        lines += [
            "",
            "| Object | Change | Before → after | Decision | Reasons |",
            "|---|---|---|---|---|",
            *(_row(d) for d in preview.items[:MAX_ROWS]),
        ]
        if len(preview.items) > MAX_ROWS:
            lines += ["", f"- …and {_count(len(preview.items) - MAX_ROWS, 'more change')}."]

    if preview.findings:
        lines += ["", "**Findings**", *(_finding_line(f) for f in preview.findings[:MAX_FINDINGS])]
        if len(preview.findings) > MAX_FINDINGS:
            lines.append(f"- …and {_count(len(preview.findings) - MAX_FINDINGS, 'more finding')}.")
    return "\n".join(lines).strip() + "\n"
