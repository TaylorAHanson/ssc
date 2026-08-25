"""
Dry-run / projection for data-defined workflow specs.

Lets an author *test* a draft workflow before publishing: given a sample request
context, it compiles the spec and walks the stages, evaluating the same
expressions the executor would (gate auto-approve predicates, step ``args``,
``for_each`` fan-out, per-item ``item_args``) **without executing any tool or
touching the database**. The result is a stage-by-stage projection an admin can
read to confirm "with this input, who approves and what each step receives". The
projection also covers the rejection path (``on_reject``), which the author would
otherwise only ever see by having a request denied in production.

This is deliberately side-effect free: it only resolves tools by name and runs
the safe expression evaluator (:mod:`app.workflows.expr`), so it is safe to call on an
unsaved draft from the editor.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflows.spec import Gate, Step, SubWorkflow
from app.workflows.spec_loader import spec_from_dict, validate_spec_dict

logger = logging.getLogger(__name__)

# Cap fan-out enumeration so a sample that produces a huge list can't blow up the
# projection payload; we report the true count and flag truncation.
_MAX_ITEMS = 25


def project_run(
    spec_dict: Dict[str, Any],
    sample_context: Optional[Dict[str, Any]] = None,
    *,
    max_items: int = _MAX_ITEMS,
) -> Dict[str, Any]:
    """Project how ``spec_dict`` would run for ``sample_context`` (no execution).

    Raises ``SpecError`` (via :func:`spec_from_dict`) if the spec is malformed.
    Per-stage expression failures are captured as ``error`` on that stage rather
    than aborting the whole projection, so authors see exactly where input is
    missing.
    """
    validate_spec_dict(spec_dict)
    spec = spec_from_dict(spec_dict)
    ctx: Dict[str, Any] = dict(sample_context or {})

    stages_out: List[Dict[str, Any]] = []
    requires_approval = False
    mutating_steps = 0

    for stage in spec.stages:
        if isinstance(stage, Gate):
            entry: Dict[str, Any] = {
                "kind": "gate",
                "name": stage.name,
                "type": stage.type,
                "waiting_status": stage.waiting_status,
                "can_auto_approve": stage.auto_approve is not None,
            }
            if stage.type == "manual_task":
                entry["instructions"] = stage.instructions
                if stage.due_in_days:
                    entry["due_in_days"] = stage.due_in_days
            try:
                if stage.auto_approve is not None and stage.auto_approve(ctx):
                    entry["decision"] = "auto_approve"
                elif stage.type == "manual_task":
                    # Not an approval: a person does work off-platform and marks it
                    # done. Reporting it as "requires approval" would tell the
                    # author this workflow is authorized when it isn't.
                    entry["decision"] = "awaits_manual_completion"
                else:
                    entry["decision"] = "requires_approval"
            except Exception as e:  # noqa: BLE001 - surface eval errors to the author
                entry["decision"] = "requires_approval"
                entry["error"] = str(e)
            if entry["decision"] == "requires_approval":
                requires_approval = True
            stages_out.append(entry)
        elif isinstance(stage, SubWorkflow):
            # A nested workflow runs inline (its own gates/steps execute under
            # this request). We can't fully project the child without resolving
            # it, so report the composition and the mapped inputs.
            entry = {
                "kind": "subworkflow",
                "name": stage.name,
                "ref": stage.ref,
                "running_status": stage.running_status,
                "conditional": stage.run_if is not None,
            }
            # Conditional composition: will this nested workflow run for this input?
            will_run = True
            if stage.run_if is not None:
                try:
                    will_run = bool(stage.run_if(ctx))
                except Exception as e:  # noqa: BLE001 - surface eval errors to the author
                    will_run = True
                    entry["error"] = str(e)
            entry["will_run"] = will_run
            entry["decision"] = "run" if will_run else "skip"
            try:
                entry["input"] = stage.input(ctx) if stage.input else {}
            except Exception as e:  # noqa: BLE001 - surface eval errors to the author
                entry["input"] = {}
                entry["error"] = str(e)
            # A nested workflow typically contains its own approval gate(s) — but
            # only counts toward "requires approval" when it actually runs.
            if will_run:
                requires_approval = True
            stages_out.append(entry)
        else:
            entry = _project_step(stage, ctx, max_items)
            if entry["is_mutating"] and entry["will_run"]:
                mutating_steps += 1
            stages_out.append(entry)

    # The rejection path, projected under the context the executor gives it: the
    # gate writes the approver's reason and its own name into context, so an author
    # can see whether their rejection message actually picks them up.
    reject_ctx = {
        **ctx,
        "rejection_reason": ctx.get("rejection_reason", "<the approver's reason>"),
        "rejected_gate": ctx.get("rejected_gate", "<the gate that denied it>"),
    }
    on_reject_out = [_project_step(s, reject_ctx, max_items) for s in spec.on_reject]

    return {
        "name": spec.name,
        "completed_status": spec.completed_status,
        "complete_fact": spec.complete_fact,
        "stages": stages_out,
        "requires_approval": requires_approval,
        "mutating_steps": mutating_steps,
        # What runs if a gate denies the request. Always present (possibly empty):
        # every workflow has this path whether or not the author put steps on it.
        "on_reject": on_reject_out,
        "on_reject_mutating_steps": sum(
            1 for e in on_reject_out if e["is_mutating"] and e["will_run"]
        ),
    }


def _project_step(step: Step, ctx: Dict[str, Any], max_items: int) -> Dict[str, Any]:
    """Project one step: will it run, and with what arguments."""
    entry: Dict[str, Any] = {
        "kind": "step",
        "name": step.name,
        "tool": getattr(step.tool, "name", "?"),
        "is_mutating": bool(getattr(step.tool, "is_mutating", False)),
        "side_effect_class": getattr(step.tool, "side_effect_class", "read"),
        "approvals": list(step.approvals),
        "success_fact": step.success_fact,
        "conditional": step.run_if is not None,
    }
    # Conditional branching projection: will this step run for this input?
    will_run = True
    if step.run_if is not None:
        try:
            will_run = bool(step.run_if(ctx))
        except Exception as e:  # noqa: BLE001 - surface eval errors to the author
            will_run = True
            entry["error"] = str(e)
    entry["will_run"] = will_run
    entry["decision"] = "run" if will_run else "skip"
    if not will_run:
        # Skipped: no tool call, no fan-out — make that explicit.
        entry["fan_out"] = 0
        entry["calls"] = []
        return entry
    try:
        if step.for_each is not None:
            items = step.for_each(ctx) or []
            entry["fan_out"] = len(items)
            calls = []
            for item in items[:max_items]:
                calls.append(
                    step.item_args(ctx, item) if step.item_args else step.args(ctx)
                )
            entry["calls"] = calls
            if len(items) > max_items:
                entry["truncated"] = True
        else:
            entry["fan_out"] = 1
            entry["calls"] = [step.args(ctx)]
    except Exception as e:  # noqa: BLE001
        entry["error"] = str(e)
        entry["calls"] = []
    return entry
