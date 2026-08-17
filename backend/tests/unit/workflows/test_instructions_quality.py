"""Tests for scoring a workflow's runtime playbook.

``instructions_markdown`` *is* the prompt the self-service agent follows, so the
gap this rubric exists to catch is a workflow that evaluates as an excellent
graph while shipping a three-line generated stub. The score is advisory, but
these are the specific things it must never call fine.
"""
from app.workflows.instructions import render_instructions_markdown
from app.workflows.instructions_quality import score_instructions


def _spec():
    return {
        "name": "custom_training",
        "stages": [
            {"kind": "gate", "name": "approval", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "args": {
                 "to_email": {"$literal": "scheduler@corp.com"},
                 "subject": {"$literal": "Training"},
                 "body": {"$concat": ["Topics: ", {"$var": "topics"},
                                      ", headcount: ", {"$var": "headcount"}]},
             }},
        ],
    }


_GOOD = """# Custom Training

**Goal**: schedule an instructor-led training session for a team.

## Information to Gather
1. **Topics** (`topics`) — The subject areas to cover. Required. Free text; list
   1-5 topics, e.g. "Spark tuning, Delta Lake". Ask: "Which topics should the
   session cover?"
2. **Headcount** (`headcount`) — How many people will attend. Required. A whole
   number between 3 and 40; below 3 we fold the group into an existing session.

## Validation & Guidance
Reject a headcount over 40 and suggest splitting into two sessions. If the topics
are vague ("data stuff"), ask which tools the team uses day to day rather than
guessing.

## Flow & Approvals
The requester's manager approves first, then the scheduler is notified. Expect a
day or two for the approval.

## Open Questions & Risks
Confirm whether contractors count toward headcount.
"""


def test_empty_instructions_score_zero_and_flag_critical():
    report = score_instructions("", _spec())
    assert report["score"] == 0
    assert report["tier"] == "poor"
    assert report["findings"][0]["severity"] == "critical"


def test_auto_baseline_is_penalized_heavily():
    """The whole point: the generated stub must not look publishable."""
    baseline = render_instructions_markdown(_spec(), request_type="custom_training")
    report = score_instructions(baseline, _spec())
    assert report["summary"]["is_auto_baseline"] is True
    assert report["score"] < 65, "a generated stub must not score as 'good'"
    assert any("baseline" in f["message"].lower() for f in report["findings"])


def test_authored_playbook_scores_well():
    report = score_instructions(_GOOD, _spec())
    assert report["score"] >= 85
    assert report["tier"] == "excellent"
    assert report["summary"]["documented_inputs"] == report["summary"]["total_inputs"]


def test_undocumented_input_is_a_high_finding():
    """An input the graph consumes but the prose never mentions is the classic
    "the workflow just stalls at runtime" bug."""
    md = _GOOD.replace("headcount", "attendee_count")
    report = score_instructions(md, _spec())
    missing = [f for f in report["findings"] if "headcount" in f["message"]]
    assert missing and missing[0]["severity"] == "high"


def test_manual_task_hold_must_be_mentioned():
    spec = _spec()
    spec["stages"].insert(0, {
        "kind": "gate", "name": "book_room", "type": "manual_task",
        "instructions": "Book a room and confirm the date with the instructor.",
    })
    report = score_instructions(_GOOD, spec)
    assert any("manual" in f["message"].lower() for f in report["findings"])
