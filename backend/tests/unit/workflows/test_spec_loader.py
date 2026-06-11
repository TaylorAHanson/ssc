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


def test_run_if_validates_and_compiles_predicate():
    spec = _good_spec()
    spec["stages"][1]["run_if"] = {"$eq": [{"$var": "tier"}, "high"]}
    validate_spec_dict(spec)  # no raise
    compiled = spec_from_dict(spec)
    step = compiled.stages[1]
    assert step.run_if is not None
    assert step.run_if({"tier": "high"}) is True
    assert step.run_if({"tier": "low"}) is False


def test_run_if_rejects_bad_expression():
    spec = _good_spec()
    spec["stages"][1]["run_if"] = {"$nope": [1]}
    with pytest.raises(SpecError, match="run_if"):
        validate_spec_dict(spec)


def test_run_if_absent_means_always_runs():
    compiled = spec_from_dict(_good_spec())
    assert compiled.stages[1].run_if is None


def test_approvers_from_validates_and_compiles():
    spec = _good_spec()
    spec["stages"][0]["approvers_from"] = {"$var": "data_owners"}
    validate_spec_dict(spec)  # no raise
    gate = spec_from_dict(spec).stages[0]
    assert gate.approvers_from is not None
    assert gate.approvers_from({"data_owners": ["grp-a", "grp-b"]}) == ["grp-a", "grp-b"]
    assert gate.approvers_from({}) is None  # raw value, not bool-coerced


def test_approvers_from_rejects_bad_expression():
    spec = _good_spec()
    spec["stages"][0]["approvers_from"] = {"$nope": [1]}
    with pytest.raises(SpecError, match="approvers_from"):
        validate_spec_dict(spec)


def test_approvers_from_absent_means_static_gate():
    assert spec_from_dict(_good_spec()).stages[0].approvers_from is None


def test_writes_context_validates_and_compiles():
    spec = _good_spec()
    spec["stages"][1]["writes_context"] = ["data_owners"]
    validate_spec_dict(spec)  # no raise
    step = spec_from_dict(spec).stages[1]
    assert step.writes_context == ["data_owners"]


@pytest.mark.parametrize("bad", ["data_owners", [1, 2], [""], {}])
def test_writes_context_rejects_non_string_list(bad):
    spec = _good_spec()
    spec["stages"][1]["writes_context"] = bad
    with pytest.raises(SpecError, match="writes_context"):
        validate_spec_dict(spec)


# --- data access: now data, not a dedicated code graph -------------------

def test_data_access_is_data_defined_with_resolve_gate_grant():
    """The former dedicated data_access graph is now a graph_spec: a
    resolve_owners step lifts data_owners into context, the data_owner gate reads
    them via approvers_from, and grants fan out per asset."""
    from app.models.request import RequestType

    data = SPECS[RequestType.DATA_ACCESS_REQUEST.value]
    spec = spec_from_dict(data)            # validates + compiles
    build_spec_graph(spec)                 # builds a StateGraph
    resolve, gate, grant = spec.stages

    assert resolve.tool.name == "resolve_data_owners"
    assert resolve.writes_context == ["data_owners"]
    assert resolve.tool.is_mutating is False  # owner discovery is read-only

    assert gate.type == "data_owner"
    assert gate.approvers_from is not None
    assert gate.approvers_from({"data_owners": ["owners-grp"]}) == ["owners-grp"]

    assert grant.tool.name == "grant_uc_access"
    assert grant.for_each is not None
    assets = [{"asset_name": "main.sales.orders", "asset_type": "table"}]
    assert grant.for_each({"assets": assets}) == assets
    # Single flat asset is synthesized into the list when `assets` is absent.
    one = grant.for_each({"asset_name": "main.x.y", "asset_type": "schema"})
    assert one == [{"asset_name": "main.x.y", "asset_type": "schema"}]
    item = grant.item_args(
        {"requested_by_email": "u@corp.com", "access_level": "read"}, assets[0])
    assert item == {"asset_type": "table", "asset_name": "main.sales.orders",
                    "principal": "u@corp.com", "access_level": "read"}


def test_resolve_data_owners_is_registered_read_tool():
    assert has_tool("resolve_data_owners")
    meta = {t["name"]: t for t in available_tools()}["resolve_data_owners"]
    assert meta["is_mutating"] is False
    assert meta["side_effect_class"] == "read"
    assert set(meta["args"]) == {"assets", "data_owners"}


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
