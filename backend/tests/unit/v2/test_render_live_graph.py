"""Unit tests for the live request-graph view (request-detail visual runner).

Validates that ``render.live_graph`` annotates the authored graph nodes with the
correct done/current/pending/rejected status from the fact log + request status,
without needing a database (facts + published-spec lookup are monkeypatched).
"""
import pytest

from app.models.request import RequestType
from app.v2 import render


class _Fact:
    def __init__(self, event_type, event_data=None):
        self.event_type = event_type
        self.event_data = event_data or {}


class _Req:
    def __init__(self, status):
        self.id = "req-1"
        self.status = status
        self.type = RequestType.WORKSPACE_ACCESS.value  # gate(manager) -> step(provision)
        self.requester_email = "u@corp.com"
        self.created_at = None


def _patch(monkeypatch, facts):
    monkeypatch.setattr("app.state_machines.facts.get_facts", lambda db, rid: facts)
    monkeypatch.setattr("app.v2.graphs.published_graph_spec", lambda db, rt: None)


def test_fresh_request_waits_on_first_gate(monkeypatch):
    _patch(monkeypatch, [])
    out = render.live_graph(_Req("manager_approval"), db=None)
    ns = out["node_states"]
    assert ns["pending"] == "done"
    assert ns["manager_approval"] == "current"
    assert ns["provision"] == "pending"
    assert ns["complete"] == "pending"
    assert out["current"] == "manager_approval"
    # The graph_spec is the same shape the editor/preview consumes.
    assert out["graph_spec"]["stages"][0]["name"] == "manager_approval"


def test_after_approval_advances_to_provision(monkeypatch):
    _patch(monkeypatch, [_Fact("approval_received", {"approval_type": "manager"})])
    out = render.live_graph(_Req("provisioning"), db=None)
    ns = out["node_states"]
    assert ns["manager_approval"] == "done"
    assert ns["provision"] == "current"
    assert out["current"] == "provision"


def test_completed_marks_all_done(monkeypatch):
    _patch(monkeypatch, [
        _Fact("approval_received", {"approval_type": "manager"}),
        _Fact("access_granted"),
    ])
    out = render.live_graph(_Req("completed"), db=None)
    ns = out["node_states"]
    assert ns["manager_approval"] == "done"
    assert ns["provision"] == "done"
    assert ns["complete"] == "done"


def test_rejected_marks_gate_rejected(monkeypatch):
    _patch(monkeypatch, [])
    out = render.live_graph(_Req("rejected"), db=None)
    ns = out["node_states"]
    assert ns["manager_approval"] == "rejected"
    assert ns["provision"] == "pending"
    assert ns["rejected"] == "rejected"
