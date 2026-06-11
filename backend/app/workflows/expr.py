"""
Safe structured-expression evaluator for data-defined workflow specs.

The V1/V2 specs used Python ``lambda``s for the dynamic bits of a workflow —
``args`` mapping, gate ``auto_approve`` predicates, ``for_each`` lists, and
per-item ``item_args``. Lambdas can't be authored in a UI or stored in the DB,
so the no-code spec replaces them with a small **JSON expression language** that
is fully serializable and evaluated here without ``eval``/``exec`` (no arbitrary
code execution).

An expression is plain JSON. A JSON object with exactly one key that starts with
``$`` is an *operation*; everything else (scalars, plain objects, arrays) is a
*literal*. Operations:

    {"$var": "scope"}                      ctx["scope"]               (None if missing)
    {"$var": "scope", "default": "x"}      ctx.get("scope", "x")
    {"$item": true}                        the whole for_each item
    {"$item": "child_type"}                item.get("child_type")
    {"$item": "k", "default": {}}          item.get("k", {})
    {"$ctx": true}                         the entire context dict
    {"$literal": <any>}                    escape hatch: value returned as-is
    {"$eq": [a, b]}                        eval(a) == eval(b)
    {"$ne": [a, b]}                        eval(a) != eval(b)
    {"$in": [a, b]}                        eval(a) in eval(b)
    {"$and": [a, b, ...]}                  all truthy  -> bool
    {"$or":  [a, b, ...]}                  any truthy  -> bool
    {"$not": a}                            not truthy  -> bool
    {"$bool": a}                           bool(eval(a))
    {"$coalesce": [a, b, ...]}             first truthy eval (mirrors `a or b`)
    {"$obj": {"k": expr, ...}}             dict literal with evaluated values
    {"$list": [expr, ...]}                 list with evaluated elements

Dotted paths are supported for ``$var``/``$item`` (e.g. ``"a.b"``). Unknown
operators and malformed nodes raise :class:`ExprError`, which the spec validator
surfaces at author time rather than at run time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

# Operators that take a list of operand expressions.
_LIST_OPS = {"$eq", "$ne", "$in", "$and", "$or", "$coalesce", "$list"}
_ALL_OPS = _LIST_OPS | {"$var", "$item", "$ctx", "$literal", "$not", "$bool", "$obj"}


class ExprError(ValueError):
    """Raised for malformed or unsupported expressions (author-time error)."""


def is_operation(node: Any) -> bool:
    """True if ``node`` is a single-key dict whose key is a ``$`` operator."""
    return (
        isinstance(node, dict)
        and len(node) == 1
        and next(iter(node)).startswith("$")
    )


def _op_name(node: Mapping[str, Any]) -> str:
    return next(iter(node))


def _resolve_path(container: Any, path: Any, default: Any) -> Any:
    """Walk a dotted ``path`` into ``container`` (a mapping), else ``default``."""
    if path is True or path is None or path == "":
        return container if container is not None else default
    if not isinstance(path, str):
        raise ExprError(f"path must be a string, got {type(path).__name__}")
    cur = container
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def evaluate(node: Any, env: Dict[str, Any]) -> Any:
    """Evaluate an expression ``node`` against ``env`` ({"ctx": ..., "item": ...})."""
    if not is_operation(node):
        # Literal: scalars, plain dicts, and arrays pass through unchanged.
        return node

    op = _op_name(node)
    arg = node[op]

    if op == "$ctx":
        return env.get("ctx", {})
    if op == "$literal":
        return arg
    if op == "$var":
        return _var(node, env.get("ctx", {}))
    if op == "$item":
        return _var(node, env.get("item"), key="$item")
    if op == "$not":
        return not _truthy(evaluate(arg, env))
    if op == "$bool":
        return bool(_truthy(evaluate(arg, env)))
    if op == "$obj":
        if not isinstance(arg, Mapping):
            raise ExprError("$obj expects an object")
        return {k: evaluate(v, env) for k, v in arg.items()}
    if op == "$list":
        return [evaluate(v, env) for v in _as_list(arg, op)]
    if op == "$and":
        return all(_truthy(evaluate(v, env)) for v in _as_list(arg, op))
    if op == "$or":
        return any(_truthy(evaluate(v, env)) for v in _as_list(arg, op))
    if op == "$coalesce":
        items = _as_list(arg, op)
        result: Any = None
        for v in items:
            result = evaluate(v, env)
            if _truthy(result):
                return result
        return result
    if op in ("$eq", "$ne", "$in"):
        operands = _as_list(arg, op)
        if len(operands) != 2:
            raise ExprError(f"{op} expects exactly 2 operands")
        a, b = evaluate(operands[0], env), evaluate(operands[1], env)
        if op == "$eq":
            return a == b
        if op == "$ne":
            return a != b
        try:
            return a in b
        except TypeError:
            return False

    raise ExprError(f"unknown operator '{op}'")


def _var(node: Mapping[str, Any], container: Any, key: str = "$var") -> Any:
    path = node[key]
    # ``default`` can't share the single-key form, so it's read from the value
    # when the value is itself a {path, default} object. We instead support the
    # compact form {"$var": "x"} and the explicit {"$var": {"path": "x",
    # "default": ...}}.
    if isinstance(path, Mapping):
        return _resolve_path(container, path.get("path"), path.get("default"))
    return _resolve_path(container, path, None)


def _truthy(value: Any) -> bool:
    return bool(value)


def _as_list(arg: Any, op: str) -> List[Any]:
    if not isinstance(arg, list):
        raise ExprError(f"{op} expects a list of operands")
    return arg


def validate(node: Any, *, allow_item: bool = False, _depth: int = 0) -> None:
    """Static-check an expression tree; raise :class:`ExprError` on problems.

    ``allow_item`` permits ``$item`` references (only valid inside ``item_args``
    / ``for_each`` item context). Recursion is depth-limited to reject
    pathological nesting from a UI.
    """
    if _depth > 50:
        raise ExprError("expression nested too deeply")
    if not is_operation(node):
        return  # literal (incl. plain dict/list) is always valid
    op = _op_name(node)
    if op not in _ALL_OPS:
        raise ExprError(f"unknown operator '{op}'")
    if op == "$item" and not allow_item:
        raise ExprError("$item is only valid in per-item (for_each) context")
    arg = node[op]
    if op in ("$var", "$item"):
        path = arg
        if isinstance(path, Mapping):
            validate(path.get("default"), allow_item=allow_item, _depth=_depth + 1)
        elif not (isinstance(path, str) or path is True or path is None):
            raise ExprError(f"{op} path must be a string")
        return
    if op in ("$ctx", "$literal"):
        return
    if op == "$obj":
        if not isinstance(arg, Mapping):
            raise ExprError("$obj expects an object")
        for v in arg.values():
            validate(v, allow_item=allow_item, _depth=_depth + 1)
        return
    if op in ("$not", "$bool"):
        validate(arg, allow_item=allow_item, _depth=_depth + 1)
        return
    operands = _as_list(arg, op)
    if op in ("$eq", "$ne", "$in") and len(operands) != 2:
        raise ExprError(f"{op} expects exactly 2 operands")
    for v in operands:
        validate(v, allow_item=allow_item, _depth=_depth + 1)
