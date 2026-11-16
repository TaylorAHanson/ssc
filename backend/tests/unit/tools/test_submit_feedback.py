"""submit_feedback accepts any kind of report: the user never has to classify it.

It files nothing until the report says something and the user has agreed to send it.
"""
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
    out = mod.submit_feedback._func(title="Orders look off", description="The orders page shows last week's totals.",
                                    confirmed=True, _user_email="a@example.com")
    assert out["success"] is True
    assert _stored(tool_db, "Orders look off").type == "feedback"


def test_a_data_issue_label_is_kept_not_rejected(tool_db):
    out = mod.submit_feedback._func(
        title="Stale shipments",
        feedback_type="data_issue",
        description="Nothing new since Friday.",
        about="enterprise_prd.logistics.shipments",
        confirmed=True,
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
    mod.submit_feedback._func(title="Button broken", feedback_type=" BUG ", confirmed=True,
                              description="The save button does nothing when I click it.")
    mod.submit_feedback._func(title="Add export", feedback_type="feature", confirmed=True,
                              description="Let me export my requests list to CSV.")
    assert _stored(tool_db, "Button broken").type == "bug"
    assert _stored(tool_db, "Add export").type == "feature"


def test_about_alone_is_added_without_a_label(tool_db):
    mod.submit_feedback._func(title="Wrong totals", about="Sales dashboard", description="Q3 is double.", confirmed=True)
    row = _stored(tool_db, "Wrong totals")
    assert row.description == "About: Sales dashboard\n\nQ3 is double."


def test_description_is_left_alone_when_nothing_is_added(tool_db):
    mod.submit_feedback._func(title="Nice app", description="Thanks, the new catalog view saves me time!", confirmed=True)
    assert _stored(tool_db, "Nice app").description == "Thanks, the new catalog view saves me time!"


def test_severity_is_still_for_bugs_only(tool_db):
    mod.submit_feedback._func(title="Wrong data", feedback_type="data issue", severity="high", confirmed=True,
                              description="The totals do not match the source system.")
    assert _stored(tool_db, "Wrong data").severity is None


def test_a_blank_title_is_still_refused(tool_db):
    out = mod.submit_feedback._func(title="   ", feedback_type="feedback")
    assert out["success"] is False


def test_the_same_report_from_the_same_person_is_recorded_once(tool_db):
    from app.db.feedback import FeedbackModel

    args = dict(title="Shipments looks stale", description="Numbers haven't changed in weeks",
                about="main.supply.shipments", confirmed=True, _user_email="me@example.com")
    first = mod.submit_feedback._func(**args)
    again = mod.submit_feedback._func(**{**args, "title": "Shipments table appears stale"})

    assert first["success"] and "already_recorded" not in first
    assert again["success"] and again["already_recorded"] is True
    assert "don't say you submitted it again" in again["note"]
    assert tool_db.query(FeedbackModel).filter_by(submitted_by="me@example.com").count() == 1


def test_a_different_person_or_a_new_issue_is_still_recorded(tool_db):
    from app.db.feedback import FeedbackModel

    base = dict(title="Stale", description="Numbers haven't changed in weeks", about="main.supply.shipments", confirmed=True)
    mod.submit_feedback._func(**base, _user_email="me@example.com")
    other_person = mod.submit_feedback._func(**base, _user_email="you@example.com")
    new_issue = mod.submit_feedback._func(
        title="Orders wrong", description="Totals don't match the report", about="main.supply.orders",
        confirmed=True, _user_email="me@example.com")

    assert "already_recorded" not in other_person and "already_recorded" not in new_issue
    assert tool_db.query(FeedbackModel).count() == 3


# --- nothing is filed until there is something to file and the user has agreed ---

def test_a_vague_remark_is_not_filed_it_sends_the_assistant_back_to_ask(tool_db):
    out = mod.submit_feedback._func(title="App sucks", description="this app sucks", confirmed=True,
                                    _user_email="a@example.com")

    assert out["filed"] is False and out["needs_details"] is True and "success" not in out
    assert "what happened" in out["note"]
    assert tool_db.query(FeedbackModel).count() == 0


def test_a_title_alone_is_not_enough_even_when_confirmed(tool_db):
    out = mod.submit_feedback._func(title="Please fix the thing that is broken", confirmed=True)
    assert out["needs_details"] is True
    assert tool_db.query(FeedbackModel).count() == 0


def test_naming_what_it_is_about_is_enough_detail_on_its_own(tool_db):
    out = mod.submit_feedback._func(title="Stale", about="main.supply.shipments", confirmed=True)
    assert out["success"] is True


def test_an_unconfirmed_report_comes_back_as_a_draft_and_files_nothing(tool_db):
    out = mod.submit_feedback._func(
        title="Requests page is slow", feedback_type="bug", about="Requests page",
        description="The requests page takes about a minute to load for me.",
    )

    assert out["filed"] is False and out["needs_confirmation"] is True and "success" not in out
    assert out["draft"]["type"] == "bug" and out["draft"]["title"] == "Requests page is slow"
    assert "takes about a minute" in out["draft"]["description"]
    assert "confirmed=true" in out["note"]
    assert tool_db.query(FeedbackModel).count() == 0


def test_confirming_the_same_fields_files_it(tool_db):
    fields = dict(title="Requests page is slow", feedback_type="bug",
                  description="The requests page takes about a minute to load for me.",
                  _user_email="a@example.com")
    assert mod.submit_feedback._func(**fields)["needs_confirmation"] is True

    filed = mod.submit_feedback._func(**fields, confirmed=True)

    assert filed["success"] is True and filed["type"] == "bug"
    assert tool_db.query(FeedbackModel).count() == 1


# --- the conversation has to show the user asked, whatever the assistant says ---

STALE = dict(
    title="Stale shipments data", feedback_type="feedback",
    description="The data in main.supply.shipments looks stale. Nothing new has landed in weeks.",
    about="main.supply.shipments", _user_email="me@example.com",
)


def _chat(*turns):
    """A conversation from alternating (role, text) pairs."""
    return [{"role": role, "content": text} for role, text in turns]


def test_describing_a_problem_is_not_asking_to_report_it_even_if_the_assistant_says_confirmed(tool_db):
    history = _chat(("user", "The data in main.supply.shipments looks stale. Nothing new has landed in weeks."))

    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=history)

    assert out["filed"] is False and out["needs_confirmation"] is True and "success" not in out
    assert "hasn't asked for this to go to the team" in out["note"]
    assert tool_db.query(FeedbackModel).count() == 0


def test_asking_for_it_to_be_reported_in_the_same_message_files_it(tool_db):
    history = _chat(("user", "Please report this to the team: main.supply.shipments looks stale"))
    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=history)
    assert out["success"] is True


def test_logging_a_feature_request_is_asking(tool_db):
    history = _chat(("user", "Please log a feature request: show the roadmap in this app, with each item's stage."))
    out = mod.submit_feedback._func(
        title="Show the roadmap", feedback_type="feature", confirmed=True, conversation_history=history,
        description="Show the product roadmap in this app, with each item's lifecycle stage.",
    )
    assert out["success"] is True


def test_yes_to_the_assistants_offer_files_it(tool_db):
    history = _chat(
        ("user", "The data in main.supply.shipments looks stale. Nothing new has landed in weeks."),
        ("assistant", "That sounds frustrating. Want me to report this to the team?"),
        ("user", "Yes, please."),
    )
    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=history)
    assert out["success"] is True


def test_the_latest_message_is_what_counts_not_an_earlier_yes(tool_db):
    history = _chat(
        ("user", "Yes, report it."),
        ("assistant", "Done."),
        ("user", "Orders also look stale and the totals haven't moved since the first of the month."),
    )
    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=history)
    assert out["needs_confirmation"] is True


def test_report_and_file_as_nouns_do_not_count_as_asking(tool_db):
    for text in ("The sales report is wrong and shows last month's numbers twice over.",
                 "The file in the shipments volume is missing and nothing new has landed."):
        out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=_chat(("user", text)))
        assert out["needs_confirmation"] is True, text
    assert tool_db.query(FeedbackModel).count() == 0


def test_a_yes_buried_in_a_long_description_is_not_an_agreement(tool_db):
    text = ("Yes the shipments table is stale, nothing new has landed in weeks and the dashboards "
            "built on it are showing the same numbers as before the long weekend.")
    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=_chat(("user", text)))
    assert out["needs_confirmation"] is True


def test_message_content_given_as_blocks_is_read(tool_db):
    history = [{"role": "user", "content": [{"type": "text", "text": "Yes, send it."}]}]
    out = mod.submit_feedback._func(**STALE, confirmed=True, conversation_history=history)
    assert out["success"] is True


def test_with_no_conversation_to_check_the_callers_confirmed_stands(tool_db):
    # The MCP server and direct calls have no chat history.
    assert mod.submit_feedback._func(**STALE, confirmed=True)["success"] is True
