"""Tests for the data-spec loader/validator and the serializable catalog.

These guard the no-code core: every catalog spec must validate, resolve its
tools, and compile to a graph; and the validator must reject malformed specs
before they can be published or run.
"""
import pytest

from app.v2.graphs.specs import SPECS, stage_specs
from app.v2.spec import Gate, Step, build_spec_graph
from app.v2.spec_loader import (
    SpecError,
    spec_from_dict,
    stage_specs_from_dict,
    validate_spec_dict,
)
from app.v2.tool_registry import available_tools, get_tool, has_tool


# --- tool registry --------------------------------------------------------

def test_tool_registry_resolves_known_tools():
    assert has_tool("add_group_membership")
    tool = get_tool("add_group_membership")
    assert tool.name == "add_group_membership"
    assert tool.is_mutating is True
    with pytest.raises(KeyError):
        get_tool("not_a_real_tool")


def test_available_tools_lists_metadata():
    tools = {t["name"]: t for t in available_tools()}
    assert "terraform_apply" in tools
    assert tools["terraform_apply"]["is_mutating"] is True
    assert "side_effect_class" in tools["terraform_apply"]


# --- validation -----------------------------------------------------------

def _good_spec():
    return {
        "name": "demo",
        "complete_fact": "done",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "auto_approve": {"$eq": [{"$var": "scope"}, "enterprise"]}},
            {"kind": "step", "name": "provision", "tool": "add_group_membership",
             "approvals": ["manager"], "success_fact": "done",
             "args": {"group": {"$var": "group"},
                      "members": {"$list": [{"$var": "requested_by_email"}]}}},
        ],
    }


def test_validate_accepts_good_spec():
    validate_spec_dict(_good_spec())  # no raise


@pytest.mark.parametrize("mutate,match", [
    (lambda s: s.pop("name"), "name is required"),
    (lambda s: s["stages"][1].__setitem__("tool", "ghost_tool"), "not a known V2 tool"),
    (lambda s: s["stages"][0].__setitem__("type", "wizard"), "type must be one of"),
    (lambda s: s["stages"][1].__setitem__("args", {"x": {"$nope": [1]}}), "unknown operator"),
    (lambda s: s["stages"].append(s["stages"][0]), "duplicate stage name"),
    (lambda s: s["stages"][0].__setitem__("name", "completed"), "reserved"),
])
def test_validate_rejects_bad_specs(mutate, match):
    spec = _good_spec()
    mutate(spec)
    with pytest.raises(SpecError, match=match):
        validate_spec_dict(spec)


def test_item_expr_only_valid_in_item_args():
    spec = _good_spec()
    spec["stages"][1]["args"]["bad"] = {"$item": True}
    with pytest.raises(SpecError, match="for_each"):
        validate_spec_dict(spec)


# --- compilation + closures ----------------------------------------------

def test_spec_from_dict_builds_runtime_spec_and_closures():
    spec = spec_from_dict(_good_spec())
    gate, step = spec.stages
    assert isinstance(gate, Gate) and gate.type == "manager"
    assert gate.auto_approve({"scope": "enterprise"}) is True
    assert gate.auto_approve({"scope": "domain"}) is False
    assert isinstance(step, Step)
    assert step.tool.name == "add_group_membership"
    assert step.args({"group": "g", "requested_by_email": "u@corp.com"}) == {
        "group": "g", "members": ["u@corp.com"]}


def test_for_each_and_item_args_closures():
    spec = spec_from_dict({
        "name": "fan_out",
        "stages": [
            {"kind": "step", "name": "spawn", "tool": "spawn_child_request",
             "for_each": {"$coalesce": [{"$var": "recipients"}, {"$list": [None]}]},
             "item_args": {"child_type": "simple_email",
                           "parameters": {"$obj": {"to_email": {"$item": True}}}}},
        ],
    })
    step = spec.stages[0]
    assert step.for_each({"recipients": ["a", "b"]}) == ["a", "b"]
    assert step.for_each({}) == [None]
    assert step.item_args({}, "a@corp.com") == {
        "child_type": "simple_email", "parameters": {"to_email": "a@corp.com"}}


# --- catalog parity -------------------------------------------------------

def test_every_catalog_spec_validates_compiles_and_resolves_tools():
    assert len(SPECS) >= 20
    for rt, data in SPECS.items():
        validate_spec_dict(data)                 # structurally valid
        spec = spec_from_dict(data)              # compiles closures + tools
        build_spec_graph(spec)                   # builds a StateGraph
        for stage in data.get("stages", []):
            if stage["kind"] == "step":
                assert has_tool(stage["tool"]), f"{rt}:{stage['name']} -> {stage['tool']}"


def test_catalog_stage_specs_match_renderer_view():
    for rt, data in SPECS.items():
        assert stage_specs_from_dict(data) == stage_specs(rt)
