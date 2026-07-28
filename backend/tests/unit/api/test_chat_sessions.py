"""Chat transcripts: round-trip, retention, and cross-user isolation.

Isolation is the load-bearing test here. Transcripts are keyed by a
client-generated id, so any lookup that trusted the id alone would let one user
read another's conversation.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.chat_session import ChatSessionModel
from app.services import chat_session_service as svc

ALICE = "alice@example.com"
BOB = "bob@example.com"


def _messages(*texts: str):
    return [
        {"id": f"m{i}", "kind": "user", "content": t, "timestamp": "2026-07-01T00:00:00Z"}
        for i, t in enumerate(texts)
    ]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------
def test_upsert_then_get_round_trips_messages(db_session):
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("hello", "again"))

    loaded = svc.get_session(db_session, ALICE, "s1")
    assert loaded is not None
    assert [m["content"] for m in loaded.messages] == ["hello", "again"]
    assert loaded.message_count == 2


def test_upsert_replaces_rather_than_appends(db_session):
    """The client owns the array and sends its current state, so writes replace."""
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("first"))
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("first", "second"))

    loaded = svc.get_session(db_session, ALICE, "s1")
    assert loaded.message_count == 2
    assert db_session.query(ChatSessionModel).count() == 1


def test_title_comes_from_the_first_user_message(db_session):
    session = svc.upsert_session(
        db_session, ALICE, "s1",
        messages=[
            {"id": "a", "kind": "agent", "content": "Hi there"},
            {"id": "b", "kind": "user", "content": "How do I request table access?"},
            {"id": "c", "kind": "user", "content": "never mind"},
        ],
    )
    assert session.title == "How do I request table access?"


def test_unknown_surface_falls_back_to_the_default(db_session):
    session = svc.upsert_session(db_session, ALICE, "s1", messages=[], surface="not-a-surface")
    assert session.surface == svc.DEFAULT_SURFACE


def test_list_is_scoped_and_filterable_by_surface(db_session):
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("a"), surface="unified")
    svc.upsert_session(db_session, ALICE, "s2", messages=_messages("b"), surface="authoring")
    svc.upsert_session(db_session, BOB, "s3", messages=_messages("c"), surface="unified")

    assert {s.id for s in svc.list_sessions(db_session, ALICE)} == {"s1", "s2"}
    assert [s.id for s in svc.list_sessions(db_session, ALICE, surface="authoring")] == ["s2"]


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------
def test_one_user_cannot_read_anothers_transcript(db_session):
    svc.upsert_session(db_session, ALICE, "shared-id", messages=_messages("alice secret"))

    assert svc.get_session(db_session, BOB, "shared-id") is None


def test_one_user_cannot_delete_anothers_transcript(db_session):
    svc.upsert_session(db_session, ALICE, "shared-id", messages=_messages("alice secret"))

    assert svc.delete_session(db_session, BOB, "shared-id") is False
    assert svc.get_session(db_session, ALICE, "shared-id") is not None


def test_one_user_cannot_overwrite_anothers_transcript(db_session):
    """A colliding id must create a second row, never hijack the first."""
    svc.upsert_session(db_session, ALICE, "shared-id", messages=_messages("alice"))
    svc.upsert_session(db_session, BOB, "shared-id", messages=_messages("bob"))

    assert [m["content"] for m in svc.get_session(db_session, ALICE, "shared-id").messages] == ["alice"]
    assert [m["content"] for m in svc.get_session(db_session, BOB, "shared-id").messages] == ["bob"]


def test_the_owner_key_is_case_insensitive(db_session):
    """One human is one owner, whatever case the IdP hands us.

    ``user_profiles`` lowercases the email it keys on. This table used to store
    whatever it was given, so a mixed-case address produced two spellings of the
    same person: their transcripts were invisible to the profile's "recently asked
    about" lookup, and a case change would fragment their history.
    """
    svc.upsert_session(db_session, "Alice@Example.com", "s1", messages=_messages("hello"))

    stored = db_session.query(ChatSessionModel).one()
    assert stored.user_email == ALICE

    # Reachable under either spelling, and still exactly one row.
    assert svc.get_session(db_session, ALICE, "s1") is not None
    assert svc.get_session(db_session, "ALICE@EXAMPLE.COM", "s1") is not None
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("hello", "again"))
    assert db_session.query(ChatSessionModel).count() == 1
    assert [s.id for s in svc.list_sessions(db_session, "Alice@Example.COM")] == ["s1"]


def test_case_folding_does_not_merge_different_people(db_session):
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("alice"))
    svc.upsert_session(db_session, BOB, "s2", messages=_messages("bob"))

    assert svc.get_session(db_session, "ALICE@EXAMPLE.COM", "s2") is None
    assert svc.delete_sessions(db_session, "Alice@Example.com") == 1
    assert svc.get_session(db_session, BOB, "s2") is not None


def test_clearing_leaves_other_users_untouched(db_session):
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("a"))
    svc.upsert_session(db_session, BOB, "s2", messages=_messages("b"))

    assert svc.delete_sessions(db_session, ALICE) == 1
    assert svc.get_session(db_session, BOB, "s2") is not None


# ---------------------------------------------------------------------------
# The turn's history source
# ---------------------------------------------------------------------------
def _conversation(session_id: str):
    from app.api.v1.agent import ConversationRequest

    return ConversationRequest(query="follow-up", session_id=session_id)


def _user(email: str):
    return SimpleNamespace(email=email, full_name="T", roles=[], entitlements=[])


def test_a_turn_resumes_the_callers_own_transcript(db_session):
    from app.api.v1.agent import _resolve_history

    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("what I asked before"))

    history = _resolve_history(_conversation("s1"), _user(ALICE), db_session)

    assert [h["content"] for h in history] == ["what I asked before"]


def test_a_turn_cannot_resume_someone_elses_transcript(db_session):
    """The last mile of the isolation story.

    ``session_id`` arrives from the client on every turn, so guessing another
    user's id would otherwise replay their conversation into the asker's prompt —
    and the model would happily summarize it back to them.
    """
    from app.api.v1.agent import _resolve_history

    svc.upsert_session(db_session, ALICE, "shared-id", messages=_messages("alice's secret"))

    history = _resolve_history(_conversation("shared-id"), _user(BOB), db_session)

    assert history == []


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def test_prune_removes_only_transcripts_past_retention(db_session, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_SESSION_RETENTION_DAYS", 30)
    svc.upsert_session(db_session, ALICE, "recent", messages=_messages("a"))
    old = svc.upsert_session(db_session, ALICE, "old", messages=_messages("b"))
    old.updated_at = datetime.utcnow() - timedelta(days=90)
    db_session.commit()

    assert svc.prune_sessions(db_session) == 1
    assert svc.get_session(db_session, ALICE, "recent") is not None
    assert svc.get_session(db_session, ALICE, "old") is None


def test_prune_is_a_noop_when_retention_is_disabled(db_session):
    svc.upsert_session(db_session, ALICE, "s1", messages=_messages("a"))
    assert svc.prune_sessions(db_session, retention_days=0) == 0
    assert svc.get_session(db_session, ALICE, "s1") is not None


# ---------------------------------------------------------------------------
# Transcript -> model history
# ---------------------------------------------------------------------------
def test_transcript_translates_user_and_agent_turns(db_session):
    history = svc.transcript_to_history([
        {"id": "u1", "kind": "user", "content": "hi"},
        {"id": "a1", "kind": "agent", "content": "hello"},
    ])
    assert [(h["role"], h["content"]) for h in history] == [("user", "hi"), ("assistant", "hello")]


def test_completed_tool_call_replays_as_a_linked_pair():
    """A ``role='tool'`` message without a preceding ``tool_calls`` is rejected
    upstream with HTTP 400, so the synthetic assistant turn is mandatory."""
    history = svc.transcript_to_history([
        {
            "id": "t1",
            "kind": "tool",
            "status": "success",
            "toolCallId": "call_abc",
            "toolName": "get_table_list",
            "toolArguments": {"catalog": "main"},
            "toolResult": {"tables": ["a", "b"]},
        }
    ])

    assert history[0]["role"] == "assistant"
    assert history[0]["tool_calls"][0]["id"] == "call_abc"
    assert history[0]["tool_calls"][0]["function"]["name"] == "get_table_list"
    assert history[1]["role"] == "tool"
    assert history[1]["tool_call_id"] == "call_abc"
    assert "tables" in history[1]["content"]


def test_in_flight_and_display_only_entries_are_skipped():
    """A pill still running has no result to replay; reasoning is display-only."""
    history = svc.transcript_to_history([
        {"id": "t1", "kind": "tool", "status": "running", "toolCallId": "c1", "toolName": "x"},
        {"id": "r1", "kind": "reasoning", "content": "thinking..."},
        {"id": "u1", "kind": "user", "content": "hi"},
    ])
    assert [h["role"] for h in history] == ["user"]


def test_tool_result_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_OUTPUT_CHARS", 100)
    history = svc.transcript_to_history([
        {
            "id": "t1", "kind": "tool", "status": "success",
            "toolCallId": "c1", "toolName": "x",
            "toolResult": {"rows": ["x" * 5000]},
        }
    ])
    assert len(history[1]["content"]) < 300
    assert "truncated" in history[1]["content"]


def test_malformed_entries_do_not_break_translation():
    assert svc.transcript_to_history([None, "nonsense", 42]) == []
    assert svc.transcript_to_history([]) == []
