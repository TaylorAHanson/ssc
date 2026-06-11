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
from app.workflows.spec import Gate, Step, WorkflowSpec
from app.workflows.tool_registry import get_tool, has_tool

# Gate kinds the renderer + executor understand (see render.gate_satisfied).
GATE_TYPES = {"manager", "platform_admin", "data_owner", "training", "pr_merge", "children"}


class SpecError(ValueError):
    """Raised when a data spec is structurally invalid (author-time)."""


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
        if kind not in ("gate", "step"):
            raise SpecError(f"{where}.kind must be 'gate' or 'step'")
        if not isinstance(name, str) or not name.strip():
            raise SpecError(f"{where}.name is required")
        if name in seen:
            raise SpecError(f"{where}: duplicate stage name '{name}'")
        if name in ("complete", "rejected", "pending", "completed"):
            raise SpecError(f"{where}: '{name}' is reserved")
        seen.add(name)

        if kind == "gate":
            _validate_gate(stage, where)
        else:
            _validate_step(stage, where)


def _validate_gate(stage: Dict[str, Any], where: str) -> None:
    gtype = stage.get("type")
    if gtype not in GATE_TYPES:
        raise SpecError(f"{where}.type must be one of {sorted(GATE_TYPES)}")
    if "auto_approve" in stage and stage["auto_approve"] is not None:
        _validate_expr(stage["auto_approve"], f"{where}.auto_approve", allow_item=False)
    if "approvers_from" in stage and stage["approvers_from"] is not None:
        _validate_expr(stage["approvers_from"], f"{where}.approvers_from", allow_item=False)


def _validate_step(stage: Dict[str, Any], where: str) -> None:
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
    for stage in data.get("stages", []):
        if stage["kind"] == "gate":
            stages.append(Gate(
                name=stage["name"],
                type=stage["type"],
                waiting_status=stage.get("waiting_status", "manager_approval"),
                auto_approve=_auto_approve_fn(stage["auto_approve"])
                if stage.get("auto_approve") is not None else None,
                approvers_from=_value_fn(stage["approvers_from"])
                if stage.get("approvers_from") is not None else None,
            ))
        else:
            step = Step(
                name=stage["name"],
                tool=get_tool(stage["tool"]),
                args=_args_fn(stage.get("args", {})),
                running_status=stage.get("running_status", "provisioning"),
                approvals=list(stage.get("approvals", [])),
                success_fact=stage.get("success_fact"),
            )
            if stage.get("for_each") is not None:
                step.for_each = _for_each_fn(stage["for_each"])
                step.item_args = _item_args_fn(stage.get("item_args", {}))
            if stage.get("run_if") is not None:
                step.run_if = _auto_approve_fn(stage["run_if"])  # bool predicate over ctx
            if stage.get("writes_context") is not None:
                step.writes_context = list(stage["writes_context"])
            stages.append(step)

    return WorkflowSpec(
        name=data["name"],
        stages=stages,
        completed_status=data.get("completed_status", "completed"),
        complete_fact=data.get("complete_fact"),
    )


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


def stage_specs_from_dict(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """UI-renderer stage introspection straight from the data spec (no compile)."""
    out: List[Dict[str, Any]] = []
    for stage in data.get("stages", []):
        if stage.get("kind") == "gate":
            out.append({"name": stage["name"], "kind": "gate",
                        "gate_type": stage.get("type"), "success_fact": None})
        else:
            out.append({"name": stage["name"], "kind": "step",
                        "gate_type": None, "success_fact": stage.get("success_fact")})
    return out
