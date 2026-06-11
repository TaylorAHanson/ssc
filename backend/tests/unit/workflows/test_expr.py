"""Unit tests for the safe structured-expression evaluator (app/workflows/expr.py)."""
import pytest

from app.workflows.expr import ExprError, evaluate, is_operation, validate


def ev(node, ctx=None, item=None):
    return evaluate(node, {"ctx": ctx or {}, "item": item})


# --- literals -------------------------------------------------------------

def test_scalars_and_plain_collections_are_literals():
    assert ev("enterprise") == "enterprise"
    assert ev(42) == 42
    assert ev(True) is True
    assert ev(None) is None
    assert ev(["a", "b"]) == ["a", "b"]            # plain list -> literal
    assert ev({"k": "v"}) == {"k": "v"}            # plain dict -> literal
    assert is_operation({"$var": "x"}) is True
    assert is_operation({"a": 1}) is False


# --- $var / $ctx / $item --------------------------------------------------

def test_var_lookup_and_default():
    assert ev({"$var": "scope"}, {"scope": "domain"}) == "domain"
    assert ev({"$var": "missing"}) is None
    assert ev({"$var": {"path": "missing", "default": "fallback"}}) == "fallback"
    # dotted path
    assert ev({"$var": "a.b"}, {"a": {"b": 7}}) == 7


def test_ctx_returns_whole_context():
    ctx = {"a": 1, "b": 2}
    assert ev({"$ctx": True}, ctx) == ctx


def test_item_reference():
    assert ev({"$item": True}, item="bob@corp.com") == "bob@corp.com"
    assert ev({"$item": "child_type"}, item={"child_type": "simple_email"}) == "simple_email"
    assert ev({"$item": {"path": "parameters", "default": {}}}, item={}) == {}


# --- boolean / comparison -------------------------------------------------

def test_eq_or_and_not_bool():
    assert ev({"$eq": [{"$var": "scope"}, "enterprise"]}, {"scope": "enterprise"}) is True
    assert ev({"$eq": [{"$var": "scope"}, "enterprise"]}, {"scope": "domain"}) is False
    assert ev({"$or": [{"$var": "a"}, {"$eq": [{"$var": "scope"}, "enterprise"]}]},
              {"scope": "enterprise"}) is True
    assert ev({"$not": {"$var": "requires_training"}}, {"requires_training": True}) is False
    assert ev({"$not": {"$var": "requires_training"}}, {}) is True
    assert ev({"$bool": {"$var": "x"}}, {"x": "yes"}) is True


def test_in_operator():
    assert ev({"$in": ["manager", {"$var": "roles"}]}, {"roles": ["manager", "admin"]}) is True
    assert ev({"$in": ["x", {"$var": "roles"}]}, {"roles": ["manager"]}) is False


def test_contains_operator_is_inverse_of_in():
    # list membership: tags contains "pii"
    assert ev({"$contains": [{"$var": "tags"}, "pii"]}, {"tags": ["pii", "sox"]}) is True
    assert ev({"$contains": [{"$var": "tags"}, "x"]}, {"tags": ["pii"]}) is False
    # substring membership
    assert ev({"$contains": [{"$var": "topic"}, "Spark"]}, {"topic": "Spark Tuning"}) is True
    # non-iterable haystack -> False, never raises
    assert ev({"$contains": [{"$var": "n"}, "x"]}, {"n": 5}) is False
    validate({"$contains": [{"$var": "tags"}, "pii"]})


def test_unknown_operator_lists_supported_ops():
    with pytest.raises(ExprError, match=r"\$contains"):
        validate({"$regex": ["a", "b"]})


# --- coalesce / obj / list ------------------------------------------------

def test_coalesce_mirrors_or_chains():
    # access_group or workspace
    assert ev({"$coalesce": [{"$var": "access_group"}, {"$var": "workspace"}]},
              {"workspace": "ws-1"}) == "ws-1"
    # recipients or [None]
    assert ev({"$coalesce": [{"$var": "recipients"}, {"$list": [None]}]},
              {"recipients": []}) == [None]
    assert ev({"$coalesce": [{"$var": "recipients"}, {"$list": [None]}]},
              {"recipients": ["a@corp.com"]}) == ["a@corp.com"]


def test_concat_joins_parts_and_drops_none():
    # Literals + ctx vars, with a non-string coerced and a missing var dropped.
    expr = {"$concat": ["New training request: ", {"$var": "topic"},
                        " for ", {"$var": "count"}, " people", {"$var": "missing"}]}
    validate(expr)
    assert ev(expr, {"topic": "Spark Tuning", "count": 12}) == \
        "New training request: Spark Tuning for 12 people"
    # Nested inside a notification-body $obj.
    body = {"$obj": {"subject": {"$concat": ["Req: ", {"$var": "topic"}]}}}
    validate(body)
    assert ev(body, {"topic": "X"}) == {"subject": "Req: X"}


def test_obj_and_list_build_dynamic_structures():
    out = ev(
        {"$obj": {"to_email": {"$item": True}, "subject": {"$var": {"path": "subject", "default": ""}}}},
        {"subject": "Hi"}, item="bob@corp.com",
    )
    assert out == {"to_email": "bob@corp.com", "subject": "Hi"}
    assert ev({"$list": [{"$var": "a"}, "lit"]}, {"a": 1}) == [1, "lit"]


# --- validation -----------------------------------------------------------

def test_validate_rejects_unknown_operator():
    with pytest.raises(ExprError):
        validate({"$danger": [1, 2]})


def test_validate_rejects_item_outside_item_context():
    with pytest.raises(ExprError):
        validate({"$item": True}, allow_item=False)
    validate({"$item": True}, allow_item=True)  # ok


def test_validate_rejects_bad_arity():
    with pytest.raises(ExprError):
        validate({"$eq": [1]})


def test_no_code_execution_path():
    # A string that looks like code is just a literal string, never executed.
    assert ev({"$var": "x"}, {"x": "__import__('os')"}) == "__import__('os')"
