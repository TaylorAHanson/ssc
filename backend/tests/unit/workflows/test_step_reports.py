"""Step reports: a tool result's ``report_markdown`` reaches later approvers.

The step node records a ``step_report`` fact for it (whether or not the step
has a success_fact), and the approvals API returns the newest report per step.
"""
from contextlib import contextmanager

import pytest

import app.db.session as db_session_module
import app.tools.tool_executor as tool_executor_module
from app.api.v1.approvals import _load_step_reports
from app.db import EventModel, RequestModel
from app.workflows.spec import STEP_REPORT_FACT, Step, _step_node


class _Tool:
    name = "review_databricks_app_code"


class _FakeExecutor:
    def __init__(self, result):
        self.result = result

    async def run(self, tool, ctx, **kwargs):
        return self.result


@pytest.fixture
def request_row(db_session):
    db_session.add(RequestModel(id="req-r", type="t", title="t", status="provisioning",
                                requester_email="user@example.com"))
    db_session.commit()
    return "req-r"


@contextmanager
def _patched(monkeypatch, db, result):
    def _get_db():
        yield db
    monkeypatch.setattr(db_session_module, "get_db", _get_db)
    monkeypatch.setattr(tool_executor_module, "executor", _FakeExecutor(result))
    yield


async def _run_step(monkeypatch, db, request_id, result, **step_kwargs):
    with _patched(monkeypatch, db, result):
        node = _step_node(Step(name="code_review", tool=_Tool(), **step_kwargs))
        return await node({"request_id": request_id, "context": {}, "results": {}})


def _reports(db, request_id):
    return db.query(EventModel).filter(EventModel.request_id == request_id,
                                       EventModel.event_type == STEP_REPORT_FACT).all()


@pytest.mark.asyncio
async def test_report_markdown_is_recorded_without_a_success_fact(monkeypatch, db_session, request_row):
    result = {"recommendation": "approve", "report_title": "App code review", "report_markdown": "### Compliant"}
    await _run_step(monkeypatch, db_session, request_row, result)

    [fact] = _reports(db_session, request_row)
    assert {k: fact.event_data[k] for k in ("step", "tool", "title", "markdown")} == {
        "step": "code_review", "tool": "review_databricks_app_code",
        "title": "App code review", "markdown": "### Compliant"}


@pytest.mark.asyncio
async def test_results_without_report_markdown_record_nothing(monkeypatch, db_session, request_row):
    await _run_step(monkeypatch, db_session, request_row, {"ok": True}, success_fact="done")
    assert _reports(db_session, request_row) == []


@pytest.mark.asyncio
async def test_approvals_show_the_newest_report_per_step(monkeypatch, db_session, request_row):
    await _run_step(monkeypatch, db_session, request_row, {"report_markdown": "first run"})
    await _run_step(monkeypatch, db_session, request_row, {"report_markdown": "re-run"})

    reports = _load_step_reports(db_session, [request_row, "req-none"])
    assert [(r.step, r.title, r.markdown) for r in reports[request_row]] == [
        ("code_review", "code_review", "re-run")]
    assert "req-none" not in reports
    assert _load_step_reports(db_session, []) == {}
