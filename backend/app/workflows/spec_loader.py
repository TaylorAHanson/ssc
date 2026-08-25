"""
Load a data-defined workflow spec (JSON) into a runtime :class:`WorkflowSpec`.

This is the bridge that makes "workflows as data" real: an admin authors (or the
seed imports) a JSON spec, and :func:`spec_from_dict` compiles it into the same
``WorkflowSpec`` the generic graph builder already consumes — replacing the
hand-written Python lambdas with closures over the safe expression evaluator
(:mod:`app.workflows.expr`) and resolving each step's tool by name via the registry.

:func:`validate_spec_dict` is the author-time gate (used by the API and the seed)
so malformed specs are rejected before they can be published or run.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.workflows import expr
from app.workflows.spec import Gate, Step, SubWorkflow, WorkflowSpec
from app.workflows.tool_registry import get_tool, has_tool

# Gate kinds the renderer + executor understand (see render.gate_satisfied).
# NOTE: ``children`` is DEPRECATED — the sibling-spawn model is superseded by
# compound workflows (a ``subworkflow`` stage composes children as nested
# subgraphs). It stays here so older published specs still validate; new
# authoring no longer offers it.
# ``manual_task`` is a *completion* gate, not an authorization one: it holds the
# request while a human does something the platform has no tool for, then they
# mark it done. Modeled as a gate so it inherits interrupt/pause/resume, the
# approvals inbox, and the audit trail — before it, authors faked this with a
# notification step that didn't actually wait for anything.
GATE_TYPES = {
    "manager", "platform_admin", "data_owner", "training", "pr_merge",
    "manual_task", "children",
}

# Tokens that strongly suggest a value is a group/role name rather than a gate kind.
_GROUP_NAME_TOKENS = ("admin", "group", "team", "approver", "owner", "_grp", "role")


def _looks_like_group_name(value: str) -> bool:
    """Heuristic: does an invalid gate type look like a group/role name?

    Used only to enrich the validation error so authors are pointed at the
    declarative ``approver`` block instead of guessing.
    """
    v = value.strip().lower()
    if not v:
        return False
    if any(tok in v for tok in _GROUP_NAME_TOKENS):
        return True
    # e.g. "edh_training_admin", "acme_data_stewards" — multi-segment identifiers
    # that aren't one of our known kinds are almost always group names.
    return v.count("_") >= 2


class SpecError(ValueError):
    """Raised when a data spec is structurally invalid (author-time)."""


# Allowed keys per stage kind. Anything else is almost always an authoring typo
# (e.g. `when` instead of `run_if`) that — because the loader only reads keys it
# knows — would be SILENTLY DROPPED and quietly change behavior. We reject them so
# the mistake surfaces immediately instead of shipping a no-op condition.
_GATE_KEYS = {
    "kind", "name", "type", "waiting_status", "auto_approve", "approvers_from",
    "approver", "course_code", "course_name",
    # manual_task only: what the assignee has to actually do, and an optional
    # SLA in days used for aging/escalation visibility in the inbox.
    "instructions", "due_in_days",
}
_STEP_KEYS = {
    "kind", "name", "tool", "approvals", "running_status", "success_fact",
    "args", "for_each", "item_args", "run_if", "writes_context",
}
_SUBWORKFLOW_KEYS = {"kind", "name", "ref", "input", "writes_context", "running_status", "run_if"}

# Common wrong key -> the right one, to make the error actionable.
_KEY_HINTS = {
    "when": "run_if",
    "if": "run_if",
    "condition": "run_if",
    "cond": "run_if",
    "workflow": "ref",
    "workflow_key": "ref",
    "child": "ref",
}


def _reject_unknown_keys(stage: Dict[str, Any], allowed: set, where: str) -> None:
    unknown = [k for k in stage if k not in allowed]
    if not unknown:
        return
    parts = []
    for k in sorted(unknown):
        hint = _KEY_HINTS.get(k)
        parts.append(f"'{k}' (did you mean '{hint}'?)" if hint else f"'{k}'")
    raise SpecError(
        f"{where} has unknown field(s): {', '.join(parts)}. "
        f"Allowed: {sorted(allowed)}. Unknown fields are ignored at runtime, so "
        "they'd silently no-op — fix or remove them."
    )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate_spec_dict(data: Any) -> None:
    """Raise :class:`SpecError` if ``data`` is not a well-formed workflow spec."""
    if not isinstance(data, dict):
        raise SpecError("spec must be an object")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise SpecError("spec.name is required")
    stages = data.get("stages", [])
    if not isinstance(stages, list):
        raise SpecError("spec.stages must be a list")

    seen: set = set()
    for i, stage in enumerate(stages):
        where = f"stages[{i}]"
        if not isinstance(stage, dict):
            raise SpecError(f"{where} must be an object")
        kind = stage.get("kind")
        name = stage.get("name")
        if kind not in ("gate", "step", "subworkflow"):
            raise SpecError(f"{where}.kind must be 'gate', 'step', or 'subworkflow'")
        if not isinstance(name, str) or not name.strip():
            raise SpecError(f"{where}.name is required")
        if name in seen:
            raise SpecError(f"{where}: duplicate stage name '{name}'")
        if name in ("complete", "rejected", "pending", "completed"):
            raise SpecError(f"{where}: '{name}' is reserved")
        seen.add(name)

        if kind == "gate":
            _validate_gate(stage, where)
        elif kind == "subworkflow":
            _validate_subworkflow(stage, where)
        else:
            _validate_step(stage, where)

    _validate_on_reject(data, seen)


def _validate_on_reject(data: Dict[str, Any], stage_names: set) -> None:
    """Validate the rejection path: steps that run when a gate denies the request.

    Steps only — a gate here would ask for approval of a decision already made, and
    a nested workflow would be a second graph on a terminal path. Names share one
    namespace with ``stages`` because both write into the same ``results`` map.
    """
    on_reject = data.get("on_reject")
    if on_reject is None:
        return
    if not isinstance(on_reject, list):
        raise SpecError("spec.on_reject must be a list of steps")

    seen: set = set()
    for i, stage in enumerate(on_reject):
        where = f"on_reject[{i}]"
        if not isinstance(stage, dict):
            raise SpecError(f"{where} must be an object")
        kind = stage.get("kind", "step")
        if kind != "step":
            raise SpecError(
                f"{where}.kind must be 'step' — the rejection path runs steps only "
                "(the decision is already final, so there is nothing left to approve)"
            )
        name = stage.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SpecError(f"{where}.name is required")
        if name in ("complete", "rejected", "pending", "completed"):
            raise SpecError(f"{where}: '{name}' is reserved")
        if name in seen or name in stage_names:
            raise SpecError(f"{where}: duplicate stage name '{name}'")
        seen.add(name)
        if stage.get("approvals"):
            raise SpecError(
                f"{where}.approvals cannot be set: a gate DENIED this request, so "
                "attesting an approval here would tell the policy layer something "
                "that did not happen. Remove it."
            )
        _validate_step(stage, where)


def _validate_gate(stage: Dict[str, Any], where: str) -> None:
    _reject_unknown_keys(stage, _GATE_KEYS, where)
    gtype = stage.get("type")
    if gtype not in GATE_TYPES:
        msg = f"{where}.type must be one of {sorted(GATE_TYPES)}"
        # Common authoring mistake: putting a group/role name in `type`
        # (e.g. "edh_training_admin" or "training_admin"). Point to the
        # declarative approver block instead of leaving them stuck.
        if isinstance(gtype, str) and _looks_like_group_name(gtype):
            msg += (
                f". '{gtype}' looks like a group/role name, not a gate kind. "
                "To require approval from a specific group, use a human gate "
                'type like "manager" and set '
                f'"approver": {{"source": "group", "group": "{gtype}"}}.'
            )
        raise SpecError(msg)
    if "auto_approve" in stage and stage["auto_approve"] is not None:
        _validate_expr(stage["auto_approve"], f"{where}.auto_approve", allow_item=False)
    if "approvers_from" in stage and stage["approvers_from"] is not None:
        _validate_expr(stage["approvers_from"], f"{where}.approvers_from", allow_item=False)
    if "approver" in stage and stage["approver"] is not None:
        _validate_gate_approver(stage["approver"], f"{where}.approver")
    # Training gates may pin a specific LMS course. ``course_code`` is the
    # machine identifier matched against the requester's completions;
    # ``course_name`` is optional display copy.
    if "course_code" in stage and stage["course_code"] is not None:
        if gtype != "training":
            raise SpecError(f"{where}.course_code is only valid on a 'training' gate")
        if not isinstance(stage["course_code"], str) or not stage["course_code"].strip():
            raise SpecError(f"{where}.course_code must be a non-empty string")
    if "course_name" in stage and stage["course_name"] is not None:
        if not isinstance(stage["course_name"], str):
            raise SpecError(f"{where}.course_name must be a string")
    # A manual task with no instructions is a dead end: the request pauses and
    # whoever it lands on has no idea what to do, so require the text up front.
    if gtype == "manual_task":
        instructions = stage.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise SpecError(
                f"{where}.instructions is required on a 'manual_task' gate — describe "
                "what the assignee must do before marking it done."
            )
    elif "instructions" in stage and stage["instructions"] is not None:
        # Say what to do instead: an author reaching for `instructions` on an
        # approval gate wants either approver guidance (which belongs in the
        # workflow's playbook) or an off-platform work step (a manual_task).
        raise SpecError(
            f"{where}.instructions is only valid on a 'manual_task' gate (this one is "
            f"'{gtype}'). Drop the field and put guidance for approvers in the "
            f"workflow's instructions_markdown, or — if a PERSON has to do work "
            f"off-platform here — change this gate's type to 'manual_task'."
        )
    if "due_in_days" in stage and stage["due_in_days"] is not None:
        if gtype != "manual_task":
            raise SpecError(
                f"{where}.due_in_days is only valid on a 'manual_task' gate (this one "
                f"is '{gtype}'). Drop the field, or make it a 'manual_task' gate."
            )
        due = stage["due_in_days"]
        if not isinstance(due, int) or isinstance(due, bool) or due <= 0:
            raise SpecError(f"{where}.due_in_days must be a positive integer (days)")


def _validate_gate_approver(approver: Any, where: str) -> None:
    """Validate a gate's declarative ``approver`` block (group | tag source)."""
    if not isinstance(approver, dict):
        raise SpecError(f"{where} must be an object")
    source = approver.get("source")
    if source == "group":
        group = approver.get("group")
        if not isinstance(group, str) or not group.strip():
            raise SpecError(f"{where}.group is required when source is 'group'")
    elif source == "approver_group_tag":
        if "assets_from" in approver and approver["assets_from"] is not None:
            _validate_expr(approver["assets_from"], f"{where}.assets_from", allow_item=False)
        if "fallback_to_owner" in approver and not isinstance(approver["fallback_to_owner"], bool):
            raise SpecError(f"{where}.fallback_to_owner must be a boolean")
    else:
        raise SpecError(f"{where}.source must be 'group' or 'approver_group_tag'")


def _validate_step(stage: Dict[str, Any], where: str) -> None:
    _reject_unknown_keys(stage, _STEP_KEYS, where)
    tool_name = stage.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise SpecError(f"{where}.tool is required")
    if not has_tool(tool_name):
        raise SpecError(f"{where}.tool '{tool_name}' is not a known V2 tool")
    approvals = stage.get("approvals", [])
    if not isinstance(approvals, list) or not all(isinstance(a, str) for a in approvals):
        raise SpecError(f"{where}.approvals must be a list of strings")

    if "run_if" in stage and stage["run_if"] is not None:
        _validate_expr(stage["run_if"], f"{where}.run_if", allow_item=False)

    if "writes_context" in stage and stage["writes_context"] is not None:
        wc = stage["writes_context"]
        if not isinstance(wc, list) or not all(isinstance(k, str) and k for k in wc):
            raise SpecError(f"{where}.writes_context must be a list of non-empty strings")

    args = stage.get("args", {})
    if not isinstance(args, dict):
        raise SpecError(f"{where}.args must be an object")
    for k, v in args.items():
        _validate_expr(v, f"{where}.args.{k}", allow_item=False)

    if "for_each" in stage and stage["for_each"] is not None:
        _validate_expr(stage["for_each"], f"{where}.for_each", allow_item=False)
        item_args = stage.get("item_args", {})
        if not isinstance(item_args, dict):
            raise SpecError(f"{where}.item_args must be an object")
        for k, v in item_args.items():
            _validate_expr(v, f"{where}.item_args.{k}", allow_item=True)


def _validate_subworkflow(stage: Dict[str, Any], where: str) -> None:
    """Shape-only validation for a nested-workflow (compound) stage.

    The referenced workflow's existence, cycles, and nesting depth are checked at
    compile time in :func:`app.workflows.spec.build_spec_graph` (it has the
    resolver); here we only enforce the authored shape.
    """
    _reject_unknown_keys(stage, _SUBWORKFLOW_KEYS, where)
    ref = stage.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise SpecError(f"{where}.ref is required (the referenced workflow key)")
    if "input" in stage and stage["input"] is not None:
        inp = stage["input"]
        if not isinstance(inp, dict):
            raise SpecError(f"{where}.input must be an object of context mappings")
        for k, v in inp.items():
            if not isinstance(k, str) or not k:
                raise SpecError(f"{where}.input keys must be non-empty strings")
            _validate_expr(v, f"{where}.input.{k}", allow_item=False)
    if "writes_context" in stage and stage["writes_context"] is not None:
        wc = stage["writes_context"]
        if not isinstance(wc, list) or not all(isinstance(k, str) and k for k in wc):
            raise SpecError(f"{where}.writes_context must be a list of non-empty strings")
    if "run_if" in stage and stage["run_if"] is not None:
        _validate_expr(stage["run_if"], f"{where}.run_if", allow_item=False)


def _validate_expr(node: Any, where: str, *, allow_item: bool) -> None:
    try:
        expr.validate(node, allow_item=allow_item)
    except expr.ExprError as e:
        raise SpecError(f"{where}: {e}")


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------
def _args_fn(args_spec: Dict[str, Any]):
    def fn(ctx: Dict[str, Any]) -> Dict[str, Any]:
        env = {"ctx": ctx, "item": None}
        return {k: expr.evaluate(v, env) for k, v in args_spec.items()}
    return fn


def _item_args_fn(item_args_spec: Dict[str, Any]):
    def fn(ctx: Dict[str, Any], item: Any) -> Dict[str, Any]:
        env = {"ctx": ctx, "item": item}
        return {k: expr.evaluate(v, env) for k, v in item_args_spec.items()}
    return fn


def _for_each_fn(for_each_spec: Any):
    def fn(ctx: Dict[str, Any]) -> List[Any]:
        result = expr.evaluate(for_each_spec, {"ctx": ctx, "item": None})
        return result if isinstance(result, list) else ([] if result is None else [result])
    return fn


def _auto_approve_fn(auto_spec: Any):
    def fn(ctx: Dict[str, Any]) -> bool:
        return bool(expr.evaluate(auto_spec, {"ctx": ctx, "item": None}))
    return fn


def _value_fn(value_spec: Any):
    """Closure that evaluates an expression to its raw value (no bool coercion)."""
    def fn(ctx: Dict[str, Any]) -> Any:
        return expr.evaluate(value_spec, {"ctx": ctx, "item": None})
    return fn


def spec_from_dict(data: Dict[str, Any]) -> WorkflowSpec:
    """Compile a validated JSON spec into a runtime :class:`WorkflowSpec`."""
    validate_spec_dict(data)
    stages: List[Any] = []
    # Gate types seen so far, in first-seen order. A step that doesn't declare
    # its own ``approvals`` inherits every gate that precedes it: the graph
    # guarantees those gates were satisfied before the step runs (a rejected gate
    # routes to "rejected" and the step never executes), so attesting them to the
    # policy layer is truthful — and it means authors don't have to hand-wire the
    # link (the #1 "forgot to check the box -> denied under enforcement" footgun).
    preceding_gate_types: List[str] = []
    for stage in data.get("stages", []):
        if stage["kind"] == "gate":
            # A manual task isn't awaiting approval, so it gets its own default
            # status: "manager_approval" on a held request would mislead both the
            # requester's status page and anyone triaging the queue.
            default_waiting = (
                "manual_task_pending" if stage["type"] == "manual_task" else "manager_approval"
            )
            gate = Gate(
                name=stage["name"],
                type=stage["type"],
                waiting_status=stage.get("waiting_status", default_waiting),
                auto_approve=_auto_approve_fn(stage["auto_approve"])
                if stage.get("auto_approve") is not None else None,
                approvers_from=_value_fn(stage["approvers_from"])
                if stage.get("approvers_from") is not None else None,
                course_code=stage.get("course_code"),
                course_name=stage.get("course_name"),
                instructions=stage.get("instructions"),
                due_in_days=stage.get("due_in_days"),
            )
            approver = stage.get("approver")
            if approver:
                source = approver["source"]
                gate.approver_source = source
                if source == "group":
                    gate.approver_group = approver["group"]
                elif source == "approver_group_tag":
                    assets_spec = approver.get("assets_from", {"$var": "assets"})
                    gate.approver_assets_from = _value_fn(assets_spec)
                    gate.approver_fallback_to_owner = approver.get("fallback_to_owner", True)
            stages.append(gate)
            if stage["type"] not in preceding_gate_types:
                preceding_gate_types.append(stage["type"])
        elif stage["kind"] == "subworkflow":
            sub = SubWorkflow(
                name=stage["name"],
                ref=stage["ref"],
                input=_args_fn(stage["input"])
                if stage.get("input") else None,
                writes_context=list(stage["writes_context"])
                if stage.get("writes_context") else None,
                running_status=stage.get("running_status", "provisioning"),
                run_if=_auto_approve_fn(stage["run_if"])
                if stage.get("run_if") is not None else None,
            )
            stages.append(sub)
        else:
            # Explicit ``approvals`` (an advanced override) win; otherwise inherit
            # every gate that precedes this step.
            explicit = stage.get("approvals")
            approvals = list(explicit) if explicit else list(preceding_gate_types)
            stages.append(_step_from_dict(stage, approvals))

    return WorkflowSpec(
        name=data["name"],
        stages=stages,
        completed_status=data.get("completed_status", "completed"),
        complete_fact=data.get("complete_fact"),
        # Rejection-path steps attest NOTHING. Inheriting the preceding gates the
        # way a normal step does would attest the very gate that denied the
        # request, so a mutating cleanup tool here faces policy with an empty hand.
        on_reject=[_step_from_dict(s, []) for s in (data.get("on_reject") or [])],
    )


def _step_from_dict(stage: Dict[str, Any], approvals: List[str]) -> Step:
    """Compile one step, with its approvals attestation supplied by the caller."""
    step = Step(
        name=stage["name"],
        tool=get_tool(stage["tool"]),
        args=_args_fn(stage.get("args", {})),
        running_status=stage.get("running_status", "provisioning"),
        approvals=approvals,
        success_fact=stage.get("success_fact"),
    )
    if stage.get("for_each") is not None:
        step.for_each = _for_each_fn(stage["for_each"])
        step.item_args = _item_args_fn(stage.get("item_args", {}))
    if stage.get("run_if") is not None:
        step.run_if = _auto_approve_fn(stage["run_if"])  # bool predicate over ctx
    if stage.get("writes_context") is not None:
        step.writes_context = list(stage["writes_context"])
    return step


# --------------------------------------------------------------------------
# Author-time arg linting (non-blocking warnings)
# --------------------------------------------------------------------------
# Context keys the executor/tool layer injects automatically — an author may
# reference them in a step's args without the tool declaring them as a named
# parameter, so they must never be flagged as "unknown".
_INJECTED_ARG_KEYS = {"request_id", "parameters"}


def lint_step_tool_args(data: Dict[str, Any]) -> List[str]:
    """Non-blocking lint: flag step args that don't match their tool's schema.

    The structural validator can't catch this because every tool accepts a
    ``**kwargs`` catch-all (for executor-injected context), so a wrong arg name
    (e.g. ``to`` instead of ``to_email``) is silently dropped at runtime. Here we
    compare each step's authored ``args``/``item_args`` keys against the tool's
    *declared* named parameters and return human-readable warnings. Tools whose
    only parameter is ``**kwargs`` have an open contract and are skipped.
    """
    warnings: List[str] = []
    for i, stage in enumerate(data.get("stages", []) or []):
        if not isinstance(stage, dict) or stage.get("kind") != "step":
            continue
        tool_name = stage.get("tool")
        if not isinstance(tool_name, str) or not has_tool(tool_name):
            continue  # unknown tool already caught by validate_spec_dict
        accepted = get_tool(tool_name).accepted_args
        named = accepted["named"]
        if not named:
            continue  # open contract (**kwargs only) — nothing to check
        where = f"stage '{stage.get('name', f'#{i}')}' (tool '{tool_name}')"

        provided = set((stage.get("args") or {}).keys()) | set((stage.get("item_args") or {}).keys())
        allowed = named | _INJECTED_ARG_KEYS
        for key in sorted(provided - allowed):
            warnings.append(
                f"{where}: arg '{key}' is not accepted by the tool "
                f"(accepts: {', '.join(sorted(named))})"
            )
        missing = accepted["required"] - provided - _INJECTED_ARG_KEYS
        for key in sorted(missing):
            warnings.append(f"{where}: required arg '{key}' is not set")
    return warnings


def lint_subworkflow_refs(data: Dict[str, Any], known_keys) -> List[str]:
    """Non-blocking lint: flag subworkflow ``ref``s that don't name a known workflow.

    ``known_keys`` is the set/collection of workflow keys that can be composed
    (published workflows + the seed catalog). An unknown ref (e.g. a hallucinated
    ``git_repo_provision`` when the real one is ``github_repo_creation``) compiles
    to a hard error at publish, so we surface it early — with the closest known
    keys as a hint — during validate/preview/save.
    """
    import difflib

    known = set(known_keys or [])
    warnings: List[str] = []
    for i, stage in enumerate(data.get("stages", []) or []):
        if not isinstance(stage, dict) or stage.get("kind") != "subworkflow":
            continue
        ref = stage.get("ref")
        if not isinstance(ref, str) or not ref.strip() or ref in known:
            continue
        where = f"stage '{stage.get('name', f'#{i}')}'"
        suggestions = difflib.get_close_matches(ref, known, n=3, cutoff=0.4)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        warnings.append(
            f"{where}: subworkflow ref '{ref}' is not a known workflow."
            f"{hint} It must be the key of an existing (ideally published) workflow."
        )
    return warnings


def is_compound_spec(data: Any) -> bool:
    """True if a workflow spec composes another workflow (a ``subworkflow`` stage).

    "Compound" = nested-subgraph composition; "atomic" = only gates/steps. Used
    to drive the atomic/compound badge in the UI and the editor's lint.
    """
    if not isinstance(data, dict):
        return False
    return any(
        isinstance(s, dict) and s.get("kind") == "subworkflow"
        for s in data.get("stages", [])
    )


def subworkflow_refs(data: Any) -> List[str]:
    """The workflow keys this spec composes (in order), for UI/lint display."""
    if not isinstance(data, dict):
        return []
    return [
        s["ref"]
        for s in data.get("stages", [])
        if isinstance(s, dict) and s.get("kind") == "subworkflow" and s.get("ref")
    ]


def stage_specs_from_dict(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """UI-renderer stage introspection straight from the data spec (no compile)."""
    out: List[Dict[str, Any]] = []
    for stage in data.get("stages", []):
        if stage.get("kind") == "gate":
            out.append({"name": stage["name"], "kind": "gate",
                        "gate_type": stage.get("type"), "success_fact": None})
        elif stage.get("kind") == "subworkflow":
            out.append({"name": stage["name"], "kind": "subworkflow",
                        "gate_type": None, "success_fact": None,
                        "ref": stage.get("ref")})
        else:
            out.append({"name": stage["name"], "kind": "step",
                        "gate_type": None, "success_fact": stage.get("success_fact")})
    return out
