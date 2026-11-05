"""Context Catalog AI review: grading, verdict parsing, Confluence allowlist, the loop and the service."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core import config as cfg
from app.db.context_catalog import ContextDocumentReviewModel
from app.services.context_catalog_review import confluence as conf
from app.services.context_catalog_review import reviewer as rv
from app.services.context_catalog_review import service as svc
from app.services.context_catalog_service import ContextCatalogService as Catalog


# ------------------------------------------------------------------ helpers

class _NoClose:
    """The test session, handed out by a session factory whose callers close what they get."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass


def _factory(db):
    return lambda: _NoClose(db)


class FakeLLM:
    endpoint_name = "fake-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_response(self, messages, tools=None, **_):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "tools": [t["function"]["name"] for t in tools or []]})
        return self.responses.pop(0)


def _call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _submit(**overrides):
    args = {
        "summary": "Mostly fine.",
        "scores": [{"metric": m, "score": 4, "rationale": "ok"} for m in rv.METRICS],
        "recommendations": [],
    }
    args.update(overrides)
    return args


def _input(body="Request access in the portal.\n\nThen wait.", title="Access", tags=None):
    return rv.ReviewInput(document_id="doc-1", title=title, body=body, tags=tags or [], metadata={},
                          domain={"path": ["Ops"]}, owner_signals=[], needs_owner=True)


@asynccontextmanager
async def _no_confluence(_token):
    yield conf.ConfluenceAccess("not_configured", "No connection set.")


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_REVIEW_MAX_TURNS", 4)
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_REVIEW_TIME_LIMIT_SECONDS", 60)
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_REVIEW_CONCURRENCY", 2)
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_CONFLUENCE_CONNECTION", "")
    svc._active.clear()
    yield
    svc._active.clear()


# ------------------------------------------------------------------ grading

def test_overall_score_maps_the_metric_average_onto_0_to_100():
    assert rv.overall_score([{"score": 5}] * 5) == 100
    assert rv.overall_score([{"score": 1}] * 5) == 0
    assert rv.overall_score([{"score": 4}, {"score": 3}]) == 62
    assert rv.overall_score([]) is None


@pytest.mark.parametrize("score,grade", [(100, "A"), (85, "A"), (84, "B"), (70, "B"), (55, "C"), (40, "D"),
                                         (39, "F"), (None, None)])
def test_grade_bands(score, grade):
    assert rv.grade_for(score) == grade


# ------------------------------------------------------------------ parsing

def test_parse_clamps_scores_and_keeps_metric_order():
    verdict = rv.parse_verdict({
        "summary": "s",
        "scores": [{"metric": "findability", "score": 9}, {"metric": "completeness", "score": "0"},
                   {"metric": "made_up", "score": 3}],
        "recommendations": [],
    }, corpus="", inp=_input(), rewrite_allowed=True)
    assert [(s["metric"], s["score"]) for s in verdict.scores] == [("completeness", 1), ("findability", 5)]
    assert verdict.overall_score == 50 and verdict.grade == "D"


def test_parse_drops_emails_and_links_the_reviewer_never_saw():
    corpus = "Page by jane.doe@example.com at https://wiki.example/x"
    verdict = rv.parse_verdict(_submit(
        suggested_owners=[
            {"name": "Jane Doe", "email": "Jane.Doe@example.com", "reason": "author", "source": "confluence",
             "confidence": 0.9},
            {"name": "Guessed", "email": "guessed@example.com", "reason": "?", "source": "other"},
        ],
        related_sources=[
            {"title": "Real", "url": "https://wiki.example/x", "kind": "confluence", "relation": "source"},
            {"title": "Invented", "url": "https://wiki.example/fake", "kind": "confluence", "relation": "newer"},
        ],
    ), corpus=corpus, inp=_input(), rewrite_allowed=True)
    assert verdict.suggested_owners[0]["email"] == "jane.doe@example.com"
    assert verdict.suggested_owners[1] == {**verdict.suggested_owners[1], "name": "Guessed", "email": None}
    assert [s["title"] for s in verdict.related_sources] == ["Real"]


def test_parse_marks_whether_an_excerpt_is_really_in_the_document_and_orders_by_priority():
    verdict = rv.parse_verdict(_submit(recommendations=[
        {"metric": "actionability", "priority": "low", "issue": "vague", "suggestion": "x",
         "excerpt": "Then   wait."},
        {"metric": "completeness", "priority": "high", "issue": "no link", "suggestion": "y",
         "excerpt": "not in the doc"},
    ]), corpus="", inp=_input(), rewrite_allowed=True)
    assert [r["priority"] for r in verdict.recommendations] == ["high", "low"]
    assert [r["excerpt_found"] for r in verdict.recommendations] == [False, True]


def test_parse_drops_a_rewrite_that_changes_nothing_or_is_not_allowed():
    inp = _input()
    same = rv.parse_verdict(_submit(proposed_body_markdown=inp.body + "\n", proposed_title="Access"),
                            corpus="", inp=inp, rewrite_allowed=True)
    assert same.proposed_body_markdown is None and same.proposed_title is None
    blocked = rv.parse_verdict(_submit(proposed_body_markdown="# New"), corpus="", inp=inp, rewrite_allowed=False)
    assert blocked.proposed_body_markdown is None
    kept = rv.parse_verdict(_submit(proposed_body_markdown="# New", rewrite_summary="restructured"),
                            corpus="", inp=inp, rewrite_allowed=True)
    assert kept.proposed_body_markdown == "# New" and kept.rewrite_summary == "restructured"


# ------------------------------------------------------------------ Confluence

class FakeMcp:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    async def call_tool(self, name, arguments=None, **_):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=f"result of {name}")], isError=False)


def _tool(name, props=None):
    return SimpleNamespace(name=name, description=f"{name} tool",
                           inputSchema={"type": "object", "properties": props or {}})


@pytest.mark.asyncio
async def test_confluence_session_offers_only_read_only_tools_and_fills_cloud_id():
    raw = FakeMcp([])
    session = conf.ConfluenceSession(raw, [
        _tool("searchConfluence", {"cloudId": {"type": "string"}, "cql": {"type": "string"}}),
        _tool("createConfluenceContent"),
        _tool("executeWrite"),
    ], cloud_id="cloud-1")
    assert session.tool_names == ["searchConfluence"]
    assert [s["function"]["name"] for s in session.tool_specs()] == ["confluence__searchConfluence"]

    assert "not one of the read-only" in await session.call("confluence__createConfluenceContent", {})
    assert await session.call("confluence__searchConfluence", {"cql": "text ~ x"}) == "result of searchConfluence"
    assert raw.calls == [("searchConfluence", {"cql": "text ~ x", "cloudId": "cloud-1"})]


def test_pick_site_matches_url_host_or_cloud_id():
    sites = [{"id": "a1", "url": "https://one.atlassian.net"}, {"id": "b2", "url": "https://two.atlassian.net"}]
    assert conf.pick_site(sites)["id"] == "a1"
    assert conf.pick_site(sites, "two.atlassian.net")["id"] == "b2"
    assert conf.pick_site(sites, "https://two.atlassian.net/")["id"] == "b2"
    assert conf.pick_site(sites, "B2")["id"] == "b2"
    assert conf.pick_site([], "x") is None


@pytest.mark.asyncio
async def test_open_confluence_without_a_connection_is_not_configured():
    async with conf.open_confluence("tok") as access:
        assert access.status == "not_configured" and access.session is None


def _unreachable_confluence(monkeypatch):
    import app.providers.databricks_mcp as mcp

    @asynccontextmanager
    async def refuse(*_args, **_kwargs):
        raise ConnectionError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_CONFLUENCE_CONNECTION", "atlassian")
    monkeypatch.setattr(mcp, "resolve_genie_bearer_token", lambda tok: (tok, "obo"))
    monkeypatch.setattr(mcp, "open_mcp_session", refuse)
    monkeypatch.setattr(mcp, "build_external_mcp_url", lambda name: f"https://host/api/2.0/mcp/external/{name}")


@pytest.mark.asyncio
async def test_an_unreachable_confluence_connection_is_reported_not_raised(monkeypatch):
    _unreachable_confluence(monkeypatch)
    async with conf.open_confluence("tok") as access:
        assert access.status == "unavailable" and access.session is None
        assert "atlassian" in access.note


@pytest.mark.asyncio
async def test_a_token_lookup_failure_leaves_confluence_unavailable(monkeypatch):
    import app.providers.databricks_mcp as mcp

    def boom(_tok):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_CONFLUENCE_CONNECTION", "atlassian")
    monkeypatch.setattr(mcp, "resolve_genie_bearer_token", boom)
    async with conf.open_confluence(None) as access:
        assert access.status == "unavailable"


# ------------------------------------------------------------------ loop

@pytest.mark.asyncio
async def test_reviewer_reads_then_submits_and_tool_output_counts_as_seen(db_session):
    domain = Catalog.create_domain(db_session, name="Ops")
    Catalog.create_document(db_session, domain_id=domain.id, title="Portal guide",
                            body_markdown="Access portal owner sam@example.com", owner="sam@example.com")
    llm = FakeLLM([
        {"content": "", "tool_calls": [_call("search_catalog", {"query": "access portal"})]},
        {"content": "", "tool_calls": [_call("submit_review", _submit(suggested_owners=[
            {"name": "Sam", "email": "sam@example.com", "reason": "owns the portal guide", "source": "catalog"}]),
            "c2")]},
    ])
    verdict = await rv.run_reviewer(
        llm, _input(), rubric="Grade it.", catalog=rv.CatalogTools(_factory(db_session), "doc-1"),
        confluence=conf.ConfluenceAccess("not_configured"), max_turns=4, time_limit_seconds=60,
    )
    assert verdict is not None and verdict.grade == "B" and verdict.tool_calls == 1
    assert verdict.suggested_owners[0]["email"] == "sam@example.com"
    assert "Portal guide" in llm.calls[1]["messages"][-1]["content"]
    assert "Grade it." in llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_reviewer_holds_a_submit_batched_with_reads(db_session):
    llm = FakeLLM([
        {"content": "", "tool_calls": [_call("search_catalog", {"query": "x"}, "a"),
                                       _call("submit_review", _submit(), "b")]},
        {"content": "", "tool_calls": [_call("submit_review", _submit(summary="second"), "c")]},
    ])
    verdict = await rv.run_reviewer(
        llm, _input(), rubric="", catalog=rv.CatalogTools(_factory(db_session), "doc-1"),
        confluence=conf.ConfluenceAccess("not_configured"), max_turns=4, time_limit_seconds=60,
    )
    assert verdict.summary == "second"
    assert "Not submitted" in llm.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_reviewer_is_forced_to_submit_when_rounds_run_out(db_session):
    llm = FakeLLM([
        {"content": "", "tool_calls": [_call("search_catalog", {"query": "x"})]},
        {"content": "", "tool_calls": [_call("submit_review", _submit(), "s")]},
    ])
    verdict = await rv.run_reviewer(
        llm, _input(), rubric="", catalog=rv.CatalogTools(_factory(db_session), "doc-1"),
        confluence=conf.ConfluenceAccess("not_configured"), max_turns=1, time_limit_seconds=60,
    )
    assert verdict.forced is True
    assert llm.calls[1]["tools"] == ["submit_review"]


# ------------------------------------------------------------------ service

def _doc(db, *, owner=None, title="Access", body="Request access in the portal."):
    domain = Catalog.create_domain(db, name="Ops", primary_owner="lead@example.com")
    return Catalog.create_document(db, domain_id=domain.id, title=title, body_markdown=body, owner=owner,
                                   created_by="author@example.com")


async def _run(db, doc, verdict_args):
    rows = svc.queue_reviews(db, [doc.id], requested_by="admin@example.com")
    llm = FakeLLM([{"content": "", "tool_calls": [_call("submit_review", verdict_args)]}])
    await svc.run_review(rows[0].id, None, llm=llm, session_factory=_factory(db),
                         confluence_opener=_no_confluence)
    return db.get(ContextDocumentReviewModel, rows[0].id)


def test_owner_signals_name_the_domain_owner_and_the_creator(db_session):
    doc = _doc(db_session)
    signals = {s["who"]: s["why"] for s in svc.owner_signals(db_session, doc, Catalog.get_domain(db_session,
                                                                                                  doc.domain_id))}
    assert "primary owner" in signals["lead@example.com"]
    assert signals["author@example.com"] == "created this document"


@pytest.mark.asyncio
async def test_run_review_stores_the_verdict_and_why_confluence_was_skipped(db_session):
    doc = _doc(db_session)
    review = await _run(db_session, doc, _submit(proposed_body_markdown="# Access\n\nBetter."))
    assert review.status == "completed" and review.grade == "B" and review.model == "fake-model"
    assert review.confluence_status == "not_configured" and review.confluence_note == "No connection set."
    assert review.content_hash == svc.doc_hash(doc)
    assert review.id not in svc._active


@pytest.mark.asyncio
async def test_review_completes_against_the_catalog_when_confluence_is_down(db_session, monkeypatch):
    _unreachable_confluence(monkeypatch)
    doc = _doc(db_session)
    rows = svc.queue_reviews(db_session, [doc.id], requested_by="admin@example.com")
    llm = FakeLLM([{"content": "", "tool_calls": [_call("submit_review", _submit())]}])
    await svc.run_review(rows[0].id, "tok", llm=llm, session_factory=_factory(db_session))
    review = db_session.get(ContextDocumentReviewModel, rows[0].id)
    assert review.status == "completed" and review.grade == "B"
    assert review.confluence_status == "unavailable" and "atlassian" in review.confluence_note
    assert not any(name.startswith(conf.TOOL_PREFIX) for name in llm.calls[0]["tools"])
    assert "search_catalog" in llm.calls[0]["tools"]


@pytest.mark.asyncio
async def test_run_review_records_a_failure_when_the_model_never_answers(db_session, monkeypatch):
    monkeypatch.setattr(rv, "_FAILURE_BACKOFF_SECONDS", 0)
    doc = _doc(db_session)
    rows = svc.queue_reviews(db_session, [doc.id], requested_by=None)
    llm = FakeLLM([{"content": "x", "is_error": True}] * 3)
    await svc.run_review(rows[0].id, None, llm=llm, session_factory=_factory(db_session),
                         confluence_opener=_no_confluence)
    review = db_session.get(ContextDocumentReviewModel, rows[0].id)
    assert review.status == "failed" and "didn't return a review" in review.error


@pytest.mark.asyncio
async def test_review_all_skips_documents_unchanged_since_their_last_review(db_session):
    doc = _doc(db_session)
    await _run(db_session, doc, _submit())
    assert svc.select_documents(db_session) == ([], 1)
    Catalog.update_document(db_session, doc.id, body_markdown="Edited.")
    assert svc.select_documents(db_session) == ([doc.id], 0)
    assert svc.select_documents(db_session, skip_unchanged=False) == ([doc.id], 0)


@pytest.mark.asyncio
async def test_apply_review_updates_the_document_and_refuses_after_an_edit(db_session):
    doc = _doc(db_session)
    review = await _run(db_session, doc, _submit(proposed_body_markdown="# Access\n\nBetter.",
                                                 proposed_tags=["access"]))
    updated = svc.apply_review(db_session, review.id, "admin@example.com", title=True, body=True, tags=True)
    assert updated.body_markdown == "# Access\n\nBetter." and updated.tags == ["access"]
    assert db_session.get(ContextDocumentReviewModel, review.id).applied_by == "admin@example.com"
    with pytest.raises(svc.ReviewConflict):
        svc.apply_review(db_session, review.id, "admin@example.com", title=False, body=True, tags=False)


@pytest.mark.asyncio
async def test_apply_with_nothing_to_apply_is_refused(db_session):
    doc = _doc(db_session)
    review = await _run(db_session, doc, _submit())
    with pytest.raises(ValueError):
        svc.apply_review(db_session, review.id, None, title=True, body=True, tags=True)


def test_rows_left_running_by_a_restart_are_closed_and_dont_block_a_new_review(db_session):
    doc = _doc(db_session)
    orphan = ContextDocumentReviewModel(id="orphan", document_id=doc.id, status="running")
    db_session.add(orphan)
    db_session.commit()
    assert len(svc.queue_reviews(db_session, [doc.id], requested_by=None)) == 1
    assert svc.recover_interrupted(db_session) == 1
    assert db_session.get(ContextDocumentReviewModel, "orphan").status == "failed"


def test_a_document_with_a_review_in_flight_is_not_queued_twice(db_session):
    doc = _doc(db_session)
    assert len(svc.queue_reviews(db_session, [doc.id], requested_by=None)) == 1
    assert svc.queue_reviews(db_session, [doc.id], requested_by=None) == []


@pytest.mark.asyncio
async def test_deleting_a_document_removes_its_reviews(db_session):
    doc = _doc(db_session)
    await _run(db_session, doc, _submit())
    Catalog.delete_document(db_session, doc.id)
    assert db_session.query(ContextDocumentReviewModel).count() == 0


@pytest.mark.asyncio
async def test_latest_reviews_flags_a_review_the_document_has_moved_past(db_session):
    doc = _doc(db_session)
    await _run(db_session, doc, _submit())
    assert svc.latest_reviews(db_session)[0]["is_outdated"] is False
    Catalog.update_document(db_session, doc.id, title="Access requests")
    assert svc.latest_reviews(db_session)[0]["is_outdated"] is True
