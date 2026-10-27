"""submit_feedback accepts any kind of report: the user never has to classify it."""
from unittest.mock import patch

import pytest

from app.db.feedback import FeedbackModel
from app.tools.self_service import submit_feedback as mod


@pytest.fixture
def tool_db(db_session):
    """Run the tool against the test session, without it closing the session."""
    class _Keep:
        def __getattr__(self, name):
            return getattr(db_session, name)

        def close(self):
            pass

    with patch.object(mod, "get_db", lambda: iter([_Keep()])):
        yield db_session


def _stored(db, title):
    return db.query(FeedbackModel).filter(FeedbackModel.title == title).one()


def test_type_is_optional_and_defaults_to_feedback(tool_db):
    out = mod.submit_feedback._func(title="Orders look off", _user_email="a@example.com")
    assert out["success"] is True
    assert _stored(tool_db, "Orders look off").type == "feedback"


def test_a_data_issue_label_is_kept_not_rejected(tool_db):
    out = mod.submit_feedback._func(
        title="Stale shipments",
        feedback_type="data_issue",
        description="Nothing new since Friday.",
        about="enterprise_prd.logistics.shipments",
    )
    assert out["success"] is True
    row = _stored(tool_db, "Stale shipments")
    assert row.type == "feedback"
    assert row.description == (
        "About: enterprise_prd.logistics.shipments\n"
        "Reported as: data_issue\n\n"
        "Nothing new since Friday."
    )


def test_known_types_pass_through_in_any_case(tool_db):
    mod.submit_feedback._func(title="Button broken", feedback_type=" BUG ")
    mod.submit_feedback._func(title="Add export", feedback_type="feature")
    assert _stored(tool_db, "Button broken").type == "bug"
    assert _stored(tool_db, "Add export").type == "feature"


def test_about_alone_is_added_without_a_label(tool_db):
    mod.submit_feedback._func(title="Wrong totals", about="Sales dashboard", description="Q3 is double.")
    row = _stored(tool_db, "Wrong totals")
    assert row.description == "About: Sales dashboard\n\nQ3 is double."


def test_description_is_left_alone_when_nothing_is_added(tool_db):
    mod.submit_feedback._func(title="Nice app", description="Thanks!")
    assert _stored(tool_db, "Nice app").description == "Thanks!"


def test_severity_is_still_for_bugs_only(tool_db):
    mod.submit_feedback._func(title="Wrong data", feedback_type="data issue", severity="high")
    assert _stored(tool_db, "Wrong data").severity is None


def test_a_blank_title_is_still_refused(tool_db):
    out = mod.submit_feedback._func(title="   ", feedback_type="feedback")
    assert out["success"] is False


def test_the_same_report_from_the_same_person_is_recorded_once(tool_db):
    from app.db.feedback import FeedbackModel

    args = dict(title="Shipments looks stale", description="Numbers haven't changed in weeks",
                about="main.supply.shipments", _user_email="me@example.com")
    first = mod.submit_feedback._func(**args)
    again = mod.submit_feedback._func(**{**args, "title": "Shipments table appears stale"})

    assert first["success"] and "already_recorded" not in first
    assert again["success"] and again["already_recorded"] is True
    assert "don't say you submitted it again" in again["note"]
    assert tool_db.query(FeedbackModel).filter_by(submitted_by="me@example.com").count() == 1


def test_a_different_person_or_a_new_issue_is_still_recorded(tool_db):
    from app.db.feedback import FeedbackModel

    base = dict(title="Stale", description="Numbers haven't changed in weeks", about="main.supply.shipments")
    mod.submit_feedback._func(**base, _user_email="me@example.com")
    other_person = mod.submit_feedback._func(**base, _user_email="you@example.com")
    new_issue = mod.submit_feedback._func(
        title="Orders wrong", description="Totals don't match the report", about="main.supply.orders",
        _user_email="me@example.com")

    assert "already_recorded" not in other_person and "already_recorded" not in new_issue
    assert tool_db.query(FeedbackModel).count() == 3
