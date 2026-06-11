"""
Generate self-service agent instructions from a declarative workflow spec.

A no-code ``graph_spec`` fully describes *how* a workflow runs (its gates,
steps, tools, and the ``$var`` inputs each step consumes). The conversational
self-service agent, however, reads ``instructions_markdown`` to learn *what to
gather from the user* and *how to format the* ``execute_workflow`` *call*.

When an admin authors a workflow purely from the visual editor / authoring
agent, no one hand-writes that markdown — so it would otherwise be blank and
the runtime agent would have nothing to follow. This module derives a sensible
baseline from the spec itself so instructions are never empty. Admins (or the
authoring agent) can refine the result; it is plain markdown.

Pure + side-effect free so it is trivially testable and safe to call on save.
"""
from typing import Any, Dict, List, Set

# Context variables the platform injects automatically — they are NOT things
# the agent should ask the user for, so we exclude them from "Information to
# Gather". Keep this conservative: anything not in here is treated as a
# user-supplied input.
_PLATFORM_VARS: Set[str] = {
    "request_id",
    "requested_by",
    "requested_by_email",
    "requester_email",
    "requested_by_is_platform_admin",
    "requested_by_is_manager",
}


def _collect_vars(node: Any, found: List[str]) -> None:
    """Recursively collect ``$var`` references in first-seen order."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$var" and isinstance(value, str):
                if value not in found:
                    found.append(value)
            else:
                _collect_vars(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_vars(item, found)


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


_GATE_DESCRIPTIONS = {
    "manager": "Manager approval",
    "platform_admin": "Platform Admin approval",
    "data_owner": "Data Owner approval",
    "training": "Training completion",
    "pr_merge": "Pull request merge",
    "children": "Child request completion",
}


def render_instructions_markdown(
    spec: Dict[str, Any],
    *,
    request_type: str | None = None,
    goal: str | None = None,
) -> str:
    """Derive baseline agent instructions (markdown) from a workflow spec."""
    spec = spec or {}
    name = request_type or spec.get("name") or "workflow"
    title = _humanize(name)
    stages: List[Dict[str, Any]] = spec.get("stages", []) or []

    all_vars: List[str] = []
    _collect_vars(stages, all_vars)
    user_inputs = [v for v in all_vars if v not in _PLATFORM_VARS]

    lines: List[str] = []
    lines.append(f"# {title} Instructions")
    lines.append("")
    lines.append(f"**Goal**: {goal or f'Fulfill a {title.lower()} request.'}")
    lines.append("")
    lines.append(
        "> Auto-generated from the workflow definition. Refine the wording, add "
        "naming conventions, validation hints, or required existence checks as needed."
    )
    lines.append("")

    # Information to gather (from $var inputs the steps consume).
    lines.append("## Information to Gather")
    if user_inputs:
        for idx, var in enumerate(user_inputs, start=1):
            lines.append(f"{idx}. **{_humanize(var)}** (`{var}`)")
    else:
        lines.append(
            "_No request-specific inputs are referenced by this workflow's steps. "
            "Confirm the user's intent, then proceed._"
        )
    lines.append("")

    # Approvals / flow overview so the agent can set expectations.
    gates = [s for s in stages if s.get("kind") == "gate"]
    steps = [s for s in stages if s.get("kind") == "step"]
    if gates or steps:
        lines.append("## Flow & Approvals")
        for s in stages:
            if s.get("kind") == "gate":
                gtype = s.get("type") or "manager"
                desc = _GATE_DESCRIPTIONS.get(gtype, f"{_humanize(gtype)} approval")
                auto = " (auto-approves when its condition is met)" if s.get("auto_approve") else ""
                lines.append(f"- **Gate** — {desc}{auto}")
            else:
                tool = s.get("tool") or "(provision)"
                lines.append(f"- **Step** — `{tool}` ({_humanize(s.get('name', ''))})")
        lines.append("")

    # Execution template.
    lines.append("## Execution")
    lines.append(f"Call `execute_workflow` with:")
    lines.append("```json")
    lines.append("{")
    lines.append(f'  "workflow_type": "{name}",')
    if user_inputs:
        lines.append('  "parameters": {')
        for i, var in enumerate(user_inputs):
            comma = "," if i < len(user_inputs) - 1 else ""
            lines.append(f'    "{var}": "..."{comma}')
        lines.append("  }")
    else:
        lines.append('  "parameters": {}')
    lines.append("}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)
