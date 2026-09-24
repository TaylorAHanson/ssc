"""has_fact / get_latest_fact return the same answers after the query rewrite."""

from datetime import datetime, timedelta

from app.db import EventModel, RequestModel
from app.state_machines.facts import get_fact_data, get_facts, get_latest_fact, has_fact


def _seed(db):
    db.add(RequestModel(id="req-f", type="t", title="t", status="pending"))
    base = datetime(2026, 1, 1)
    for i, (ftype, data) in enumerate([
        ("approval_received", {"approval_type": "manager", "n": 1}),
        ("approval_received", {"approval_type": "data_owner", "n": 2}),
        ("training_completed", {"n": 3}),
        ("approval_received", {"approval_type": "manager", "n": 4}),
    ]):
        db.add(EventModel(
            id=f"fact-{i}", request_id="req-f", event_type=ftype,
            event_data=data, created_at=base + timedelta(minutes=i),
        ))
    db.commit()


def test_has_fact_without_conditions(db_session):
    _seed(db_session)
    assert has_fact(db_session, "req-f", "approval_received")
    assert has_fact(db_session, "req-f", "training_completed")
    assert not has_fact(db_session, "req-f", "workspace_created")
    assert not has_fact(db_session, "req-other", "approval_received")


def test_has_fact_with_conditions(db_session):
    _seed(db_session)
    assert has_fact(db_session, "req-f", "approval_received", approval_type="data_owner")
    assert not has_fact(db_session, "req-f", "approval_received", approval_type="platform_admin")


def test_get_latest_fact_returns_newest(db_session):
    _seed(db_session)
    assert get_latest_fact(db_session, "req-f", "approval_received").event_data["n"] == 4
    assert get_latest_fact(db_session, "req-f", "missing") is None


def test_get_latest_fact_with_conditions_returns_newest_match(db_session):
    _seed(db_session)
    fact = get_latest_fact(db_session, "req-f", "approval_received", approval_type="data_owner")
    assert fact.event_data["n"] == 2
    fact = get_latest_fact(db_session, "req-f", "approval_received", approval_type="manager")
    assert fact.event_data["n"] == 4
    assert get_latest_fact(db_session, "req-f", "approval_received", approval_type="nope") is None


def test_get_fact_data_and_get_facts_order(db_session):
    _seed(db_session)
    assert get_fact_data(db_session, "req-f", "training_completed")["n"] == 3
    assert get_fact_data(db_session, "req-f", "missing", default="d") == "d"
    assert [f.event_data["n"] for f in get_facts(db_session, "req-f")] == [1, 2, 3, 4]
