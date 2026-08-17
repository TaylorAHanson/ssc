"""Deterministic quality scoring for a workflow's runtime playbook.

``evaluator.py`` scores the *graph* — is it safe, is it complete. Nothing scored
the ``instructions_markdown``, even though that text **is** the prompt the
self-service agent follows at runtime: a workflow could evaluate as excellent
while its runtime instructions were a three-line generated stub, and nothing in
the studio said so.

This is a rubric, not a judge: it checks that the playbook covers the sections a
runtime agent needs, documents every input the graph consumes, explains the
approvals, and isn't still the auto-generated baseline. Deterministic and side
-effect free — no LLM, no DB — so it can run on every save and in the list view.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.workflows.instructions import AUTO_BASELINE_MARKER, _user_inputs

# Section -> the headings that satisfy it (matched case-insensitively as an H2).
# Several phrasings are accepted so a hand-written playbook isn't penalized for
# not matching our generator's exact wording.
_REQUIRED_SECTIONS: Dict[str, List[str]] = {
    "information to gather": ["information to gather", "inputs", "what to gather"],
    "validation": ["validation", "validation & pushback", "checks", "pushback"],
    "flow & approvals": ["flow & approvals", "approvals", "flow", "process"],
    "open questions & risks": ["open questions", "risks", "open questions & risks"],
}

# Signals that an input is actually *documented* rather than merely listed.
_DESCRIPTOR_HINTS = ("required", "optional", "format", "example", "e.g.", "must", "valid")

_MIN_USEFUL_CHARS = 400


def _tier(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _headings(md: str) -> List[str]:
    return [
        m.group(1).strip().lower()
        for m in re.finditer(r"^\s*#{2,3}\s+(.+?)\s*$", md, flags=re.MULTILINE)
    ]


def _mentions_var(md: str, var: str) -> bool:
    """True if the playbook references ``var`` as an identifier, not by accident."""
    return bool(re.search(rf"[`*_\b]{re.escape(var)}[`*_\b]|\b{re.escape(var)}\b", md))


def _var_is_described(md: str, var: str) -> bool:
    """True if the line (or two) introducing ``var`` says something about it."""
    for m in re.finditer(rf"^.*\b{re.escape(var)}\b.*$", md, flags=re.MULTILINE):
        line = m.group(0)
        # The bullet plus a little context — enough prose to be a description.
        if len(line.strip()) > 40 + len(var) or any(h in line.lower() for h in _DESCRIPTOR_HINTS):
            return True
    return False


def score_instructions(
    instructions_markdown: Optional[str],
    graph_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score a playbook 0-100 with findings, mirroring the evaluator's shape.

    Returns ``{score, tier, findings: [{severity, message, fix}], summary}``.
    """
    md = (instructions_markdown or "").strip()
    findings: List[Dict[str, str]] = []

    def add(severity: str, message: str, fix: str = "") -> None:
        findings.append({"severity": severity, "message": message, "fix": fix})

    if not md:
        add(
            "critical",
            "The workflow has no runtime instructions, so the agent has nothing to "
            "follow when a request comes in.",
            fix="Generate a playbook, then edit it to match how this actually works.",
        )
        return {
            "score": 0,
            "tier": "poor",
            "findings": findings,
            "summary": {"chars": 0, "sections": [], "documented_inputs": 0, "total_inputs": 0},
        }

    penalty = 0

    if AUTO_BASELINE_MARKER in md:
        penalty += 35
        add(
            "high",
            "These are still the auto-generated baseline instructions — they only "
            "cover what the graph happens to reference, so anything the steps don't "
            "consume is never gathered.",
            fix="Edit the playbook (or ask the assistant to author it) before publishing.",
        )

    if len(md) < _MIN_USEFUL_CHARS:
        penalty += 15
        add(
            "medium",
            f"The playbook is very short ({len(md)} characters) for something the agent "
            "has to run a governed request from.",
            fix="Describe each input, what to validate, and what the approvals mean.",
        )

    headings = _headings(md)
    present_sections: List[str] = []
    for canonical, aliases in _REQUIRED_SECTIONS.items():
        if any(any(alias in h for alias in aliases) for h in headings):
            present_sections.append(canonical)
        else:
            weight = 12 if canonical == "information to gather" else 8
            penalty += weight
            add(
                "medium" if weight >= 12 else "low",
                f"No '{canonical}' section.",
                fix=f"Add a '## {canonical.title()}' section.",
            )

    # Every input the graph consumes must be documented, or the agent won't know
    # to ask for it — the most common cause of a workflow that stalls at runtime.
    inputs = _user_inputs(graph_spec or {})
    documented = 0
    for var in inputs:
        if not _mentions_var(md, var):
            penalty += 10
            add(
                "high",
                f"Input `{var}` is consumed by a step but never mentioned in the "
                "instructions, so the agent has no reason to collect it.",
                fix=f"Add a numbered entry for `{var}` under Information to Gather.",
            )
        elif not _var_is_described(md, var):
            penalty += 4
            add(
                "low",
                f"Input `{var}` is listed but not described (no format, example, or "
                "whether it's required).",
                fix=f"Say what `{var}` means, its format, and whether it's required.",
            )
            documented += 1
        else:
            documented += 1

    # Gates the requester will hit should be explained; "why is this pending?" is
    # the top support question for governed workflows.
    gates = [
        s for s in (graph_spec or {}).get("stages", []) or []
        if isinstance(s, dict) and s.get("kind") == "gate"
    ]
    if gates and not any("approv" in h or "flow" in h for h in headings):
        penalty += 8
        add(
            "medium",
            f"This workflow has {len(gates)} gate(s) but the instructions never explain "
            "the approvals, so the agent can't set expectations.",
            fix="Add a Flow & Approvals section describing each gate and the wait.",
        )

    manual_tasks = [g for g in gates if g.get("type") == "manual_task"]
    if manual_tasks and "manual" not in md.lower():
        penalty += 6
        add(
            "low",
            "This workflow pauses for a manual task, but the instructions don't tell "
            "the requester that a person has to do something off-platform.",
            fix="Mention the manual step and roughly how long it takes.",
        )

    score = max(0, 100 - penalty)
    _rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: _rank.get(f["severity"], 4))
    return {
        "score": score,
        "tier": _tier(score),
        "findings": findings,
        "summary": {
            "chars": len(md),
            "sections": present_sections,
            "documented_inputs": documented,
            "total_inputs": len(inputs),
            "is_auto_baseline": AUTO_BASELINE_MARKER in md,
        },
    }
