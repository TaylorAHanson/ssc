"""Unit tests for workflow behavioral-test cases and their run posture.

``health`` is what the studio list, the Tests tab, and the publish confirmation all
read, so the distinctions it draws matter: "never run" is not "failing", an empty
suite is not "ready", and an assistant proposal must never wipe cases an admin
wrote by hand.
"""
from datetime import datetime, timedelta

from app.db.workflow_test import WorkflowTestRunModel
from app.services.workflow_service import WorkflowService
from app.services.workflow_test_service import WorkflowTestService


def _workflow(db, key="wf_tests"):
    return WorkflowService.create(db, key=key, name=key, graph_spec={"name": key, "stages": []})


def _case(db, workflow_id, name="case", **kwargs):
    return WorkflowTestService.create_test(
        db, workflow_id,
        name=name,
        question=kwargs.get("question", "I need access to the sales catalog"),
        expected_outcome=kwargs.get(
            "expected_outcome", "Asks for a business justification before submitting"
        ),
        enabled=kwargs.get("enabled", True),
        source=kwargs.get("source", "user"),
        created_by=kwargs.get("created_by"),
    )


def _complete_run(db, case, *, verdict, score, age_hours=0):
    run = WorkflowTestRunModel(
        id=f"run-{case.id}-{verdict}-{score}-{age_hours}",
        run_group_id=f"grp-{case.id}-{age_hours}",
        workflow_id=case.workflow_id,
        test_id=case.id,
        test_name=case.name,
        question=case.question,
        expected_outcome=case.expected_outcome,
        status="complete",
        verdict=verdict,
        score=score,
        created_at=datetime.utcnow() - timedelta(hours=age_hours),
    )
    db.add(run)
    db.commit()
    return run


def test_never_run_is_reported_separately_from_failing(db_session):
    """"We don't know" and "we know it's broken" call for different reactions;
    collapsing them is how a workflow ships untested."""
    wf = _workflow(db_session)
    _case(db_session, wf.id, name="unrun")
    failing = _case(db_session, wf.id, name="failing")
    _complete_run(db_session, failing, verdict="fail", score=10)

    health = WorkflowTestService.health(db_session, wf.id)
    assert health["total"] == 2
    assert health["never_run"] == 1
    assert health["failing"] == 1
    assert health["passing"] == 0
    assert health["ready"] is False


def test_empty_suite_is_not_ready(db_session):
    """Nothing to fail is not the same as verified."""
    wf = _workflow(db_session, key="wf_empty")
    health = WorkflowTestService.health(db_session, wf.id)
    assert health["total"] == 0
    assert health["ready"] is False


def test_all_passing_is_ready(db_session):
    wf = _workflow(db_session, key="wf_green")
    for i in range(2):
        _complete_run(db_session, _case(db_session, wf.id, name=f"c{i}"),
                      verdict="pass", score=90)
    health = WorkflowTestService.health(db_session, wf.id)
    assert (health["passing"], health["total"], health["ready"]) == (2, 2, True)


def test_disabled_cases_are_excluded(db_session):
    wf = _workflow(db_session, key="wf_disabled")
    _complete_run(db_session, _case(db_session, wf.id, name="on"), verdict="pass", score=90)
    _case(db_session, wf.id, name="off", enabled=False)
    health = WorkflowTestService.health(db_session, wf.id)
    assert health["total"] == 1 and health["ready"] is True


def test_partial_verdict_is_decided_by_the_score_threshold(db_session):
    wf = _workflow(db_session, key="wf_partial")
    high = _case(db_session, wf.id, name="high")
    low = _case(db_session, wf.id, name="low")
    high_run = _complete_run(db_session, high, verdict="partial", score=95)
    low_run = _complete_run(db_session, low, verdict="partial", score=30)
    assert WorkflowTestService.is_pass(high_run, 70) is True
    assert WorkflowTestService.is_pass(low_run, 70) is False


def test_explicit_fail_is_never_a_pass_whatever_the_score(db_session):
    """The judge's own verdict wins; a high score with a `fail` verdict is a
    contradiction we resolve conservatively."""
    wf = _workflow(db_session, key="wf_fail")
    case = _case(db_session, wf.id)
    run = _complete_run(db_session, case, verdict="fail", score=99)
    assert WorkflowTestService.is_pass(run, 70) is False


def test_errored_run_is_not_a_pass_and_is_counted_separately(db_session):
    wf = _workflow(db_session, key="wf_error")
    case = _case(db_session, wf.id)
    run = WorkflowTestRunModel(
        id="run-err", run_group_id="grp-err", workflow_id=wf.id, test_id=case.id,
        test_name=case.name, question=case.question,
        expected_outcome=case.expected_outcome,
        status="error", error="the judge could not score this run",
    )
    db_session.add(run)
    db_session.commit()

    health = WorkflowTestService.health(db_session, wf.id)
    assert health["errored"] == 1
    assert health["ready"] is False
    assert WorkflowTestService.is_pass(run) is False


def test_stale_pass_is_flagged_but_still_counts_as_passing(db_session):
    wf = _workflow(db_session, key="wf_stale")
    case = _case(db_session, wf.id)
    _complete_run(db_session, case, verdict="pass", score=90, age_hours=24 * 30)
    health = WorkflowTestService.health(db_session, wf.id)
    assert health["stale"] == 1
    assert health["passing"] == 1


def test_assistant_proposals_never_replace_hand_written_cases(db_session):
    """The same "don't clobber the admin's work" rule the studio applies to
    instructions: only previously agent-sourced cases are replaced."""
    wf = _workflow(db_session, key="wf_replace")
    _case(db_session, wf.id, name="mine", source="user")
    _case(db_session, wf.id, name="old agent case", source="agent")

    saved = WorkflowTestService.replace_tests(
        db_session, wf.id,
        [{"name": "new agent case", "question": "q", "expected_outcome": "e"}],
        source="agent", created_by="assistant@corp.com",
    )
    names = {c.name for c in WorkflowTestService.list_tests(db_session, wf.id)}
    assert "mine" in names
    assert "old agent case" not in names
    assert "new agent case" in names
    assert len(saved) == 1


def test_replace_tests_drops_unjudgeable_cases(db_session):
    """A case missing either half can never be judged, so persisting it would only
    produce a permanently erroring row."""
    wf = _workflow(db_session, key="wf_halfcase")
    saved = WorkflowTestService.replace_tests(
        db_session, wf.id,
        [
            {"name": "no expectation", "question": "q", "expected_outcome": "  "},
            {"name": "no question", "question": "", "expected_outcome": "e"},
            {"name": "fine", "question": "q", "expected_outcome": "e"},
        ],
        source="agent",
    )
    assert [c.name for c in saved] == ["fine"]


def test_health_map_matches_health_for_each_workflow(db_session):
    """The list view uses the bulk path; if it disagrees with the per-workflow one
    the studio badge and the publish gate tell different stories."""
    green = _workflow(db_session, key="wf_bulk_green")
    red = _workflow(db_session, key="wf_bulk_red")
    _complete_run(db_session, _case(db_session, green.id), verdict="pass", score=90)
    _complete_run(db_session, _case(db_session, red.id), verdict="fail", score=5)

    bulk = WorkflowTestService.health_map(db_session, [green.id, red.id])
    for wid in (green.id, red.id):
        single = WorkflowTestService.health(db_session, wid)
        for key in ("total", "passing", "failing", "never_run", "errored", "ready"):
            assert bulk[wid][key] == single[key], f"{key} disagreed for {wid}"


def test_recent_run_count_only_counts_this_actor(db_session):
    """Backs the per-admin rate limit on an endpoint that invokes the agent."""
    wf = _workflow(db_session, key="wf_rate")
    case = _case(db_session, wf.id)
    WorkflowTestService.create_run_group(db_session, wf.id, [case], triggered_by="a@corp.com")
    WorkflowTestService.create_run_group(db_session, wf.id, [case], triggered_by="b@corp.com")

    assert WorkflowTestService.recent_run_count(db_session, "a@corp.com") == 1
    assert WorkflowTestService.recent_run_count(db_session, None) == 0
