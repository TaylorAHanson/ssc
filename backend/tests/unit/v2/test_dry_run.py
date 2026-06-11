"""Tests for the workflow dry-run projection (app/v2/dry_run.py)."""
import pytest

from app.v2.dry_run import project_run
from app.v2.graphs.specs import SPECS
from app.v2.spec_loader import SpecError


def test_projects_workspace_access_auto_approve():
    spec = SPECS["workspace_access"]
    # Enterprise scope should auto-approve the manager gate via the $or predicate.
    out = project_run(spec, {"scope": "enterprise", "requested_by_email": "a@b.com",
                             "access_group": "grp"})
    assert out["name"] == "workspace_access"
    gate = next(s for s in out["stages"] if s["kind"] == "gate")
    assert gate["decision"] == "auto_approve"
    assert gate["can_auto_approve"] is True

    step = next(s for s in out["stages"] if s["kind"] == "step")
    assert step["tool"] == "add_group_membership"
    # Args are evaluated against the sample context, no tool executed.
    assert step["calls"][0]["group"] == "grp"
    assert step["calls"][0]["members"] == ["a@b.com"]


def test_projects_requires_approval_when_condition_false():
    spec = SPECS["workspace_access"]
    out = project_run(spec, {"scope": "team", "is_auto_approve": False,
                             "requested_by_email": "a@b.com"})
    gate = next(s for s in out["stages"] if s["kind"] == "gate")
    assert gate["decision"] == "requires_approval"
    assert out["requires_approval"] is True


def test_missing_fields_do_not_crash():
    # Empty sample context: $var lookups return None; projection still succeeds.
    spec = SPECS["workspace_access"]
    out = project_run(spec, {})
    assert len(out["stages"]) == len(spec["stages"])
    step = next(s for s in out["stages"] if s["kind"] == "step")
    assert "calls" in step


def test_reports_mutating_step_count():
    out = project_run(SPECS["workspace_access"], {})
    assert out["mutating_steps"] >= 1


def test_invalid_spec_raises():
    with pytest.raises(SpecError):
        project_run({"name": "", "stages": []}, {})
