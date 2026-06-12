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

Separation of concerns: the *prose* (goal, what to gather, naming conventions)
is human-authorable, but the **``execute_workflow`` call contract** — the
``workflow_type`` and parameter keys — is always derived from the graph, never
hand-typed. ``execution_contract`` / ``render_execution_block`` produce it, and
``with_canonical_execution`` splices the canonical block into any (even
hand-written) instructions so the runtime call can never drift from the spec.

Pure + side-effect free so it is trivially testable and safe to call on save.
"""
import re
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


def _user_inputs(spec: Dict[str, Any]) -> List[str]:
    """The user-supplied ``$var`` inputs a spec's steps reference, in first-seen order."""
    all_vars: List[str] = []
    _collect_vars((spec or {}).get("stages", []) or [], all_vars)
    return [v for v in all_vars if v not in _PLATFORM_VARS]


def execution_contract(
    spec: Dict[str, Any], *, request_type: str | None = None
) -> Dict[str, Any]:
    """The deterministic ``execute_workflow`` call contract derived from the graph.

    This is the single source of truth for *how to call the workflow*: the
    ``workflow_type`` and the parameter keys. It is computed from the spec (the
    ``$var`` inputs its steps consume), never hand-authored, so the runtime call
    can't drift from the graph.
    """
    spec = spec or {}
    return {
        "workflow_type": request_type or spec.get("name") or "workflow",
        "parameters": _user_inputs(spec),
    }


def render_execution_block(
    spec: Dict[str, Any], *, request_type: str | None = None
) -> str:
    """Render the ``## Execution`` markdown block deterministically from the graph."""
    contract = execution_contract(spec, request_type=request_type)
    user_inputs = contract["parameters"]
    lines: List[str] = ["## Execution", "Call `execute_workflow` with:", "```json", "{"]
    lines.append(f'  "workflow_type": "{contract["workflow_type"]}",')
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
    return "\n".join(lines)


def _strip_execution_section(md: str) -> str:
    """Remove a ``## Execution`` section (its heading through the next H2 / EOF).

    Lets us replace any hand-authored execute_workflow example with the canonical
    generated one, so the served call is always derived from the graph.
    """
    out: List[str] = []
    skipping = False
    for line in (md or "").splitlines():
        if re.match(r"^\s*##\s+Execution\b", line):
            skipping = True
            continue
        if skipping and re.match(r"^\s*##\s+", line):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip()


def with_canonical_execution(
    instructions_md: str, spec: Dict[str, Any], *, request_type: str | None = None
) -> str:
    """Return ``instructions_md`` with its Execution block replaced by the canonical,
    graph-derived one. The human-authored prose (goal, what to gather, naming
    conventions) is preserved; only the ``execute_workflow`` call is regenerated.
    """
    spec = spec or {}
    if not spec.get("stages"):
        return instructions_md or ""
    body = _strip_execution_section(instructions_md or "")
    block = render_execution_block(spec, request_type=request_type)
    return f"{body}\n\n{block}\n" if body else f"{block}\n"


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

    user_inputs = _user_inputs(spec)

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
                cond = " — _conditional; runs only when its rule matches_" if s.get("run_if") else ""
                lines.append(f"- **Step** — `{tool}` ({_humanize(s.get('name', ''))}){cond}")
        lines.append("")

    # Execution template — generated deterministically from the graph.
    lines.append(render_execution_block(spec, request_type=request_type))
    lines.append("")

    return "\n".join(lines)
