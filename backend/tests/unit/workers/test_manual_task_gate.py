"""Tests for the ``manual_task`` gate's pause/resume plumbing.

A manual task is how a workflow says "no tool exists for this — hold here while a
person does it off-platform, then mark it done". It rides the human-gate machinery
rather than inventing a queue, so the two things that can silently strand a
request are: no inbox row created (nobody can ever mark it done) and a resume
condition that never matches.
"""
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.db.approval import ApprovalModel
from app.workers.poller import _ensure_approval_task, _v2_resume_value

REQUEST = types.SimpleNamespace(
    id="req-manual-1",
    requester_email="requester@corp.com",
    request_type="training_request",
    title="Custom training",
)


def _payload(**overrides):
    payload = {
        "type": "manual_task",
        "instructions": "Book the training room and confirm with the instructor.",
        "approvers": ["facilities"],
    }
    payload.update(overrides)
    return payload


def _created_row(db_mock) -> ApprovalModel:
    added = [c.args[0] for c in db_mock.add.call_args_list]
    rows = [a for a in added if isinstance(a, ApprovalModel)]
    assert rows, "no approval row was created — nobody could mark the task done"
    return rows[0]


def _db_with_no_existing_approval():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def test_manual_task_creates_an_inbox_row_carrying_its_instructions():
    """Without the instructions on the row the assignee sees an unexplained
    "something is waiting on you"."""
    db = _db_with_no_existing_approval()
    _ensure_approval_task(db, REQUEST, _payload())

    row = _created_row(db)
    assert row.approval_type == "manual_task"
    assert row.status == "pending"
    assert "Book the training room" in (row.instructions or "")
    # A group approver becomes a role assignment, not a person.
    assert row.assigned_to_role == "facilities"


def test_due_in_days_becomes_a_concrete_due_date():
    """A manual task can park a request indefinitely, so the optional SLA has to
    reach the inbox as something it can highlight."""
    db = _db_with_no_existing_approval()
    _ensure_approval_task(db, REQUEST, _payload(due_in_days=3))

    row = _created_row(db)
    assert row.due_at is not None
    assert 2 <= (row.due_at - datetime.utcnow()).days <= 3


def test_bad_due_in_days_is_ignored_rather_than_failing_the_gate():
    db = _db_with_no_existing_approval()
    _ensure_approval_task(db, REQUEST, _payload(due_in_days="soon"))
    assert _created_row(db).due_at is None


def test_approval_gates_do_not_get_instructions():
    """`instructions` is only meaningful for manual work; copying it onto an
    approval row would imply the approver was told what to do."""
    db = _db_with_no_existing_approval()
    _ensure_approval_task(db, REQUEST, {
        "type": "manager", "instructions": "leaked", "approvers": ["mgr@corp.com"],
    })
    row = _created_row(db)
    assert row.instructions is None
    assert row.assigned_to_email == "mgr@corp.com"


def test_only_one_pending_row_per_request_and_type():
    """The poller re-enters the gate on every tick; a second row would duplicate
    the task in the inbox."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ApprovalModel(
        id="appr-existing", request_id=REQUEST.id, approval_type="manual_task",
        status="pending",
    )
    _ensure_approval_task(db, REQUEST, _payload())
    assert not [c for c in db.add.call_args_list
                if isinstance(c.args[0], ApprovalModel)]


@pytest.mark.asyncio
async def test_marking_it_done_resumes_the_workflow():
    """"Mark done" writes the same approval_received fact the human gates use, so
    the gate needs no new endpoint."""
    result = types.SimpleNamespace(interrupted=True, interrupt_payload=_payload())
    fact = types.SimpleNamespace(event_type="approval_received",
                                 event_data={"approval_type": "manual_task"})
    with patch("app.state_machines.facts.has_fact", return_value=False), \
            patch("app.state_machines.facts.get_facts", return_value=[fact]):
        value = await _v2_resume_value(MagicMock(), REQUEST, result)
    assert value == {"approved": True}


@pytest.mark.asyncio
async def test_an_unrelated_approval_does_not_complete_the_task():
    """A manager approval on the same request must not stand in for the work."""
    result = types.SimpleNamespace(interrupted=True, interrupt_payload=_payload())
    fact = types.SimpleNamespace(event_type="approval_received",
                                 event_data={"approval_type": "manager"})
    with patch("app.state_machines.facts.has_fact", return_value=False), \
            patch("app.state_machines.facts.get_facts", return_value=[fact]):
        value = await _v2_resume_value(MagicMock(), REQUEST, result)
    assert value is None


@pytest.mark.asyncio
async def test_cant_complete_rejects_the_request():
    """The inbox's "Can't complete" has to end the request rather than leave it
    parked forever."""
    result = types.SimpleNamespace(interrupted=True, interrupt_payload=_payload())
    with patch("app.state_machines.facts.has_fact",
               side_effect=lambda _db, _rid, ft: ft == "request_rejected"):
        value = await _v2_resume_value(MagicMock(), REQUEST, result)
    assert value["approved"] is False
