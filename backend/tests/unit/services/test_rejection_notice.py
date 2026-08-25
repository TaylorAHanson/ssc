"""Tests for the notice a requester gets when their request is denied.

A workflow's rejection path is terminal — every gate's failure edge lands on one
built-in node that records the fact and ends the graph — so this notice is the only
thing that tells the requester anything. The failure modes that matter: silence
(nobody told), a useless reason (the poller's ``"rejected"`` marker shown as if a
human wrote it), and a mail failure taking the rejection down with it.
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.rejection_notice import (
    notify_requester_of_rejection,
    resolve_rejection_details,
)

REQUEST = types.SimpleNamespace(
    id="req-1",
    type="workspace_access",
    title="Access to prod workspace",
    requester_email="requester@corp.com",
)


def _db(request=REQUEST, facts=()):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = request
    db._facts = list(facts)
    return db


def _fact(**data):
    return types.SimpleNamespace(event_type="request_rejected", event_data=data)


def _sent(mail: AsyncMock) -> dict:
    assert mail.await_count == 1, "the requester was not emailed"
    return mail.await_args.kwargs


async def _notify(db, reason=None, facts=()):
    """Run the notice with the provider and fact lookup stubbed."""
    mail = AsyncMock()
    getter = MagicMock(return_value=MagicMock(send_email=mail))
    with patch("app.workflows.tools._get_notification_provider", getter), \
            patch("app.state_machines.facts.get_facts", return_value=list(facts)):
        result = await notify_requester_of_rejection(db, REQUEST.id, reason)
    return result, mail


@pytest.mark.asyncio
async def test_the_requester_is_told_and_gets_the_approvers_note():
    """The whole point: the person who asked finds out, and learns why."""
    result, mail = await _notify(
        _db(), reason="rejected",
        facts=[_fact(rejection_note="Use the shared workspace instead.",
                     rejected_by="approver@corp.com")],
    )
    assert result["sent"] is True
    kwargs = _sent(mail)
    assert kwargs["to"] == "requester@corp.com"
    assert "Access to prod workspace" in kwargs["subject"]
    assert "Use the shared workspace instead." in kwargs["body"]
    assert "approver@corp.com" in kwargs["body"]


@pytest.mark.asyncio
async def test_the_pollers_marker_never_reads_as_a_human_reason():
    """`{"approved": False, "reason": "rejected"}` is plumbing. Showing it would
    tell the requester their request was denied because it was denied."""
    result, mail = await _notify(_db(), reason="rejected", facts=[_fact()])
    body = _sent(mail)["body"]
    assert "No reason was recorded." in body
    assert result["had_reason"] is False


@pytest.mark.asyncio
async def test_a_graph_reason_is_used_when_no_note_was_recorded():
    """An auto/child rejection has no approval row, but may carry a real reason."""
    _, mail = await _notify(_db(), reason="Budget owner declined the cost.", facts=[])
    assert "Budget owner declined the cost." in _sent(mail)["body"]


@pytest.mark.asyncio
async def test_a_mail_failure_does_not_fail_the_rejection():
    """The request is denied whether or not the email lands; raising here would
    surface as a graph failure on an already-decided request."""
    mail = AsyncMock(side_effect=RuntimeError("SMTP down"))
    getter = MagicMock(return_value=MagicMock(send_email=mail))
    with patch("app.workflows.tools._get_notification_provider", getter), \
            patch("app.state_machines.facts.get_facts", return_value=[]):
        result = await notify_requester_of_rejection(_db(), REQUEST.id, "why")
    assert result == {"sent": False, "reason": "send_failed", "to": "requester@corp.com"}


@pytest.mark.asyncio
async def test_no_requester_email_is_reported_rather_than_crashing():
    anonymous = types.SimpleNamespace(**{**REQUEST.__dict__, "requester_email": None})
    result, mail = await _notify(_db(request=anonymous))
    assert result["sent"] is False and result["reason"] == "no_recipient"
    assert mail.await_count == 0


@pytest.mark.asyncio
async def test_admins_can_turn_it_off():
    original = settings.REJECTION_NOTIFY_REQUESTER
    settings.REJECTION_NOTIFY_REQUESTER = False
    try:
        result, mail = await _notify(_db())
    finally:
        settings.REJECTION_NOTIFY_REQUESTER = original
    assert result == {"sent": False, "reason": "disabled"}
    assert mail.await_count == 0


@pytest.mark.asyncio
async def test_a_missing_request_does_not_raise():
    result, mail = await _notify(_db(request=None))
    assert result["sent"] is False and result["reason"] == "request_not_found"
    assert mail.await_count == 0


def test_resolve_rejection_details_prefers_the_typed_note():
    facts = [_fact(reason="rejected"),
             _fact(rejection_note="Wrong catalog.", rejected_by="a@corp.com")]
    with patch("app.state_machines.facts.get_facts", return_value=facts):
        note, reviewer = resolve_rejection_details(MagicMock(), REQUEST.id)
    assert note == "Wrong catalog."
    assert reviewer == "a@corp.com"


def test_resolve_rejection_details_survives_a_fact_read_failure():
    """Used on the poller's hot path — it must degrade, not throw."""
    with patch("app.state_machines.facts.get_facts", side_effect=RuntimeError("db gone")):
        assert resolve_rejection_details(MagicMock(), REQUEST.id) == (None, None)
