"""Workflow-spec Evaluator — deterministic risk + quality scoring for authoring.

Given a candidate workflow ``graph_spec`` (the same data the editor/agent author),
this produces an advisory **Evaluation Report**:

  * **Risk score** 0–100 (higher = riskier) + tier (low/medium/high/critical) —
    "is this safe?". Driven by the blast radius of each mutating step (its
    ``side_effect_class``), whether risky mutations sit behind a human approval
    gate, fan-out, and missing success markers.
  * **Quality score** 0–100 (higher = better) + tier (poor/fair/good/excellent) —
    "is this complete?". Driven by structural validity, lint warnings (wrong/
    missing tool args, unresolved subworkflow refs), and reliability/completeness
    gaps (a step with no ``success_fact``, a ``data_owner`` gate with no approver
    source, no actionable stage, etc.).
  * **Findings** — each ``{severity, category, message, stage, fix}``.

It is deterministic and side-effect free: it reuses :func:`validate_spec_dict`
and the spec lints and resolves tool metadata from the registry — it never runs a
tool, touches the DB (beyond the optional subworkflow-ref lint), or calls an LLM.
The qualitative "is this safe/complete" reasoning that *does* use the LLM lives in
the authoring assistant, which calls this for the hard numbers (see
``app.tools.authoring.workflow_authoring.evaluate_workflow_spec``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Blast-radius points per side-effect class for a *mutating* step. Mirrors the
# approval tiering in policies/agent_tools.rego (destructive > grants/membership/
# infra > app_write > notify) so the editor and the policy agree on what's risky.
RISK_BY_CLASS: Dict[str, int] = {
    "destructive": 45,
    "data_grant": 28,
    "membership": 22,
    "infra": 22,
    "app_write": 8,
    "notify": 3,
    "read": 0,
}
# Classes that genuinely change external/governed state and therefore really want
# a human approval gate in front of them.
RISKY_CLASSES = {"destructive", "data_grant", "membership", "infra"}
# Gate kinds that constitute *authorization* (a human signs off). ``training`` and
# ``manual_task`` are completion gates, not approvals — a manual task is
# deliberately excluded so an author can't silence "risky mutation with no
# approval" by dropping a task in front of it; ``children`` is deprecated.
APPROVAL_GATE_TYPES = {"manager", "platform_admin", "data_owner", "pr_merge"}
DEPRECATED_GATE_TYPES = {"children"}

# How much each finding severity subtracts from the 100-point quality budget.
_QUALITY_PENALTY = {"critical": 100, "high": 20, "medium": 10, "low": 4, "info": 0}
# Findings in these categories describe *risk*, not *quality*, so they don't tax
# the quality score (they're already reflected in the risk number).
_RISK_ONLY_CATEGORIES = {"safety"}


def _risk_tier(score: int) -> str:
    if score >= 70:
        return "critical"
    if score >= 45:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def _quality_tier(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _is_always_true(node: Any) -> bool:
    """True if an ``auto_approve`` expression is *statically* always true.

    Catches the footgun of a gate that auto-approves unconditionally (a literal
    ``true``/``{"$literal": true}``/``{"$bool": true}``), which means there's no
    real human review even though a gate is present.
    """
    if node is True:
        return True
    if isinstance(node, dict) and len(node) == 1:
        key = next(iter(node))
        val = node[key]
        if key == "$literal":
            return val is True
        if key == "$bool":
            return _is_always_true(val)
    return False


def _tool_meta(tool_name: Optional[str]) -> tuple[str, bool]:
    """``(side_effect_class, is_mutating)`` for a step tool, defaulting safely."""
    from app.workflows.tool_registry import get_tool, has_tool

    if not tool_name or not has_tool(tool_name):
        return ("read", False)
    tool = get_tool(tool_name)
    return (getattr(tool, "side_effect_class", "read"), bool(getattr(tool, "is_mutating", False)))


def evaluate_spec(spec_dict: Dict[str, Any], db: Any = None) -> Dict[str, Any]:
    """Evaluate ``spec_dict`` and return an advisory report (see module docstring).

    ``db`` is optional and used only to resolve composable workflow keys for the
    subworkflow-ref lint; everything else is computed structurally.
    """
    from app.workflows.spec_loader import (
        SpecError,
        lint_step_tool_args,
        lint_subworkflow_refs,
        validate_spec_dict,
    )

    findings: List[Dict[str, Any]] = []

    def add(severity: str, category: str, message: str, *, stage: Optional[str] = None, fix: str = "") -> None:
        findings.append(
            {"severity": severity, "category": category, "message": message, "stage": stage, "fix": fix}
        )

    # Structural validity is the gate: an invalid spec can't be meaningfully
    # scored, so report it as a single critical finding with quality 0.
    try:
        validate_spec_dict(spec_dict)
    except SpecError as e:
        add(
            "critical",
            "validity",
            f"Spec is structurally invalid: {e}",
            fix="Fix the structural error, then re-evaluate.",
        )
        return {
            "valid": False,
            "error": str(e),
            "risk": {"score": 0, "tier": "unknown"},
            "quality": {"score": 0, "tier": "poor"},
            "findings": findings,
            "summary": {},
        }

    stages = spec_dict.get("stages", []) or []
    complete_fact = spec_dict.get("complete_fact")

    risk_raw = 0.0
    effective_approval_gates: List[str] = []  # authorization gates seen so far (not always-auto)
    gate_count = 0
    step_count = 0
    mutating_count = 0
    subworkflow_refs: List[str] = []
    seen_success_facts: Dict[str, str] = {}

    for stage in stages:
        kind = stage.get("kind")
        name = stage.get("name")

        if kind == "gate":
            gate_count += 1
            gtype = stage.get("type")
            if gtype in DEPRECATED_GATE_TYPES:
                risk_raw += 8
                add(
                    "high",
                    "maintainability",
                    f"Gate '{name}' uses the deprecated '{gtype}' kind.",
                    stage=name,
                    fix="Replace it with a 'subworkflow' stage (compound composition).",
                )
            always_auto = stage.get("auto_approve") is not None and _is_always_true(stage["auto_approve"])
            if gtype in APPROVAL_GATE_TYPES:
                if always_auto:
                    add(
                        "high",
                        "safety",
                        f"Approval gate '{name}' auto-approves unconditionally — there is no real human review.",
                        stage=name,
                        fix="Remove the always-true auto_approve, or scope it to a specific condition.",
                    )
                    risk_raw += 10
                else:
                    effective_approval_gates.append(gtype)
            if gtype == "manual_task":
                # A manual task parks the request until a person acts, so an
                # unassigned or unexplained one is a request that quietly stalls
                # forever. Its own finding rather than the approval-gate checks,
                # since completing work isn't authorizing it.
                if not stage.get("approver") and not stage.get("approvers_from"):
                    add(
                        "high",
                        "reliability",
                        f"Manual task '{name}' has no assignee, so the request will park "
                        "with nobody responsible for finishing it.",
                        stage=name,
                        fix="Set an 'approver' (e.g. {'source':'group','group':'platform-ops'}).",
                    )
                if not str(stage.get("instructions") or "").strip():
                    add(
                        "high",
                        "completeness",
                        f"Manual task '{name}' has no instructions, so whoever it lands on "
                        "won't know what to do.",
                        stage=name,
                        fix="Describe the off-platform work the assignee must complete.",
                    )
                if not stage.get("due_in_days"):
                    add(
                        "low",
                        "reliability",
                        f"Manual task '{name}' has no due_in_days, so an ignored task is "
                        "never visibly overdue.",
                        stage=name,
                        fix="Set due_in_days to make aging visible in the approvals inbox.",
                    )
            if gtype == "data_owner" and not stage.get("approver") and not stage.get("approvers_from"):
                add(
                    "medium",
                    "reliability",
                    f"Data-owner gate '{name}' has no approver source, so it may not route to anyone.",
                    stage=name,
                    fix="Set an 'approver' (e.g. source 'approver_group_tag') or 'approvers_from'.",
                )

        elif kind == "subworkflow":
            ref = stage.get("ref")
            if ref:
                subworkflow_refs.append(ref)
            add(
                "info",
                "info",
                f"Stage '{name}' composes workflow '{ref}', which is evaluated on its own.",
                stage=name,
            )

        elif kind == "step":
            step_count += 1
            tool_name = stage.get("tool")
            cls, mutating = _tool_meta(tool_name)
            base = RISK_BY_CLASS.get(cls, 10 if mutating else 0)
            contrib = float(base)

            if mutating:
                mutating_count += 1
                gated = bool(effective_approval_gates) or bool(stage.get("approvals"))
                if cls in RISKY_CLASSES and not gated:
                    contrib += base  # ungated risky mutation — double the blast radius
                    sev = "critical" if cls == "destructive" else "high"
                    add(
                        sev,
                        "safety",
                        f"Step '{name}' runs a '{cls}' tool ('{tool_name}') with no preceding approval gate.",
                        stage=name,
                        fix="Add a human gate (manager/platform_admin/data_owner) before this step.",
                    )
                if stage.get("for_each") is not None:
                    contrib += 8
                    add(
                        "medium",
                        "safety",
                        f"Step '{name}' fans out (for_each) a mutating tool — larger blast radius per run.",
                        stage=name,
                        fix="Confirm the fan-out source is bounded and intended.",
                    )
                # A notify is fire-and-forget; only state-changing steps really
                # need a success_fact to guard against a silent false-success.
                if cls != "notify" and not stage.get("success_fact"):
                    contrib += 4
                    add(
                        "medium",
                        "reliability",
                        f"Mutating step '{name}' has no success_fact, so a false-success can't halt the graph.",
                        stage=name,
                        fix="Set a success_fact (and ideally a success_predicate on the tool).",
                    )

            risk_raw += contrib

            sf = stage.get("success_fact")
            if sf:
                if sf in seen_success_facts:
                    add(
                        "medium",
                        "maintainability",
                        f"success_fact '{sf}' is reused by steps '{seen_success_facts[sf]}' and '{name}'.",
                        stage=name,
                        fix="Give each step a distinct success_fact.",
                    )
                seen_success_facts[sf] = name
                if complete_fact and sf == complete_fact:
                    add(
                        "medium",
                        "maintainability",
                        f"Step '{name}' success_fact equals the spec's complete_fact ('{sf}').",
                        stage=name,
                        fix="Use a step-specific success_fact distinct from complete_fact.",
                    )

    # The rejection path. Its steps run precisely because a gate said NO, so they
    # attest no approvals — a mutating tool here asks the policy layer to act on an
    # unapproved request, which is a different (and easier to miss) shape of risk
    # than an ungated step on the happy path.
    for i, stage in enumerate(spec_dict.get("on_reject") or []):
        if not isinstance(stage, dict):
            continue
        name = stage.get("name") or f"on_reject[{i}]"
        cls, mutating = _tool_meta(stage.get("tool"))
        if not mutating:
            continue
        risk_raw += float(RISK_BY_CLASS.get(cls, 10))
        add(
            "critical" if cls == "destructive" else "high",
            "safety",
            f"Rejection step '{name}' runs a '{cls}' tool ('{stage.get('tool')}') on a request "
            "that was DENIED — nothing here is approved.",
            stage=name,
            fix="Keep the rejection path to notifications/cleanup, or move this action onto the "
                "approved path behind a gate.",
        )

    # Spec-level completeness checks.
    if step_count == 0 and not subworkflow_refs:
        add(
            "high",
            "completeness",
            "Workflow has no steps or subworkflows — it gates but never performs an action.",
            fix="Add at least one step (or compose a subworkflow).",
        )
    if not complete_fact:
        add(
            "low",
            "completeness",
            "No complete_fact set — there's no timeline marker when the workflow finishes.",
            fix="Set a complete_fact on the spec.",
        )

    # Lints (same ones validate/preview surface): wrong/missing tool args and
    # unresolved subworkflow refs both silently break at runtime/publish.
    for warning in lint_step_tool_args(spec_dict):
        add("medium", "completeness", warning, fix="Use the tool's real arg names (list_workflow_building_blocks).")
    if db is not None and subworkflow_refs:
        try:
            from app.tools.authoring.workflow_authoring import _composable_keys

            for warning in lint_subworkflow_refs(spec_dict, _composable_keys(db)):
                add("high", "completeness", warning, fix="Reference a real (ideally published) workflow key.")
        except Exception as exc:  # noqa: BLE001 - never fail evaluation on a lint hiccup
            logger.debug("subworkflow ref lint skipped: %s", exc)

    # ---- scores ----------------------------------------------------------
    risk_score = min(100, int(round(risk_raw)))
    # A critical safety finding must read as Critical risk regardless of the raw sum.
    if any(f["severity"] == "critical" and f["category"] == "safety" for f in findings):
        risk_score = max(risk_score, 70)

    quality_penalty = sum(
        _QUALITY_PENALTY.get(f["severity"], 0)
        for f in findings
        if f["category"] not in _RISK_ONLY_CATEGORIES
    )
    quality_score = max(0, 100 - quality_penalty)

    _severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: _severity_rank.get(f["severity"], 5))

    return {
        "valid": True,
        "risk": {"score": risk_score, "tier": _risk_tier(risk_score)},
        "quality": {"score": quality_score, "tier": _quality_tier(quality_score)},
        "findings": findings,
        "summary": {
            "stage_count": len(stages),
            "gate_count": gate_count,
            "step_count": step_count,
            "mutating_steps": mutating_count,
            "approval_gates": effective_approval_gates,
            "composes": subworkflow_refs,
            "on_reject_steps": len(spec_dict.get("on_reject") or []),
        },
    }
