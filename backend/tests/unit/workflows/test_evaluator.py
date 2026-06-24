"""Tests for the workflow-spec evaluator (app/workflows/evaluator.py)."""
from app.workflows.evaluator import evaluate_spec


def _step(name, tool, **extra):
    return {"kind": "step", "name": name, "tool": tool, "args": {}, **extra}


def _gate(name, gtype, **extra):
    return {"kind": "gate", "name": name, "type": gtype, **extra}


def _find_categories(report):
    return {f["category"] for f in report["findings"]}


def test_invalid_spec_scores_zero_quality():
    report = evaluate_spec({"name": "", "stages": []})
    assert report["valid"] is False
    assert report["quality"]["score"] == 0
    assert any(f["category"] == "validity" for f in report["findings"])


def test_ungated_destructive_is_critical_risk():
    spec = {
        "name": "danger",
        "stages": [_step("enforce", "sentinel_enforce", success_fact="enforced")],
    }
    report = evaluate_spec(spec)
    assert report["valid"] is True
    assert report["risk"]["tier"] == "critical"
    assert report["risk"]["score"] >= 70
    safety = [f for f in report["findings"] if f["category"] == "safety"]
    assert any(f["severity"] == "critical" for f in safety)


def test_gate_before_risky_step_lowers_risk():
    ungated = {
        "name": "grant",
        "stages": [_step("grant", "grant_uc_access", success_fact="granted")],
    }
    gated = {
        "name": "grant",
        "stages": [
            _gate("mgr", "manager"),
            _step("grant", "grant_uc_access", success_fact="granted"),
        ],
    }
    r_ungated = evaluate_spec(ungated)
    r_gated = evaluate_spec(gated)
    assert r_gated["risk"]["score"] < r_ungated["risk"]["score"]
    # The ungated one flags a safety finding; the gated one does not.
    assert any(f["category"] == "safety" for f in r_ungated["findings"])
    assert not any(
        "no preceding approval gate" in f["message"] for f in r_gated["findings"]
    )


def test_notify_only_workflow_is_low_risk():
    spec = {
        "name": "ping",
        "stages": [
            _step("notify", "send_notification", args={"subject": "hi", "body": "there"}),
        ],
    }
    report = evaluate_spec(spec)
    assert report["risk"]["tier"] == "low"
    # A notify step is mutating but not state-changing, so it carries no
    # safety/reliability findings.
    assert not any(f["category"] == "safety" for f in report["findings"])


def test_always_true_auto_approve_is_flagged():
    spec = {
        "name": "rubber_stamp",
        "stages": [
            _gate("mgr", "manager", auto_approve={"$literal": True}),
            _step("grant", "grant_uc_access", success_fact="granted"),
        ],
    }
    report = evaluate_spec(spec)
    msgs = " ".join(f["message"] for f in report["findings"])
    assert "auto-approves unconditionally" in msgs


def test_missing_success_fact_is_flagged():
    spec = {
        "name": "membership",
        "stages": [
            _gate("mgr", "manager"),
            _step("add", "add_group_membership"),  # no success_fact
        ],
    }
    report = evaluate_spec(spec)
    assert any(
        f["category"] == "reliability" and "success_fact" in f["message"]
        for f in report["findings"]
    )


def test_data_owner_gate_without_approver_is_flagged():
    spec = {
        "name": "owned",
        "stages": [
            _gate("owner", "data_owner"),
            _step("grant", "grant_uc_access", success_fact="granted"),
        ],
    }
    report = evaluate_spec(spec)
    assert any(
        "approver source" in f["message"] for f in report["findings"]
    )


def test_no_action_workflow_flags_completeness():
    spec = {"name": "gate_only", "stages": [_gate("mgr", "manager")]}
    report = evaluate_spec(spec)
    assert any(
        f["category"] == "completeness" and "no steps" in f["message"].lower()
        for f in report["findings"]
    )


def test_clean_gated_workflow_has_no_safety_findings():
    spec = {
        "name": "clean",
        "complete_fact": "done",
        "stages": [
            _gate("mgr", "manager"),
            _step(
                "grant",
                "grant_uc_access",
                success_fact="granted",
                args={
                    "asset_type": "table",
                    "asset_name": "main.sales.orders",
                    "principal": {"$var": "requested_by_email"},
                    "access_level": "SELECT",
                },
            ),
        ],
    }
    report = evaluate_spec(spec)
    assert not any(f["category"] == "safety" for f in report["findings"])
    assert report["quality"]["score"] >= 85
