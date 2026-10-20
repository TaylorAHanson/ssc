"""The Metadata Manager chat and MCP tools (ADR-0006 D6): who the caller is, where a change
came from, and how service errors reach the model."""
from unittest.mock import MagicMock

import pytest

import app.db.session as db_session_module
from app.services.metadata import authz, changes, search
from app.services.metadata.errors import ChangeBlocked, VersionConflict
from app.services.metadata.types import (
    Actor,
    ChangePreview,
    ChangeResult,
    Finding,
    SearchResult,
    VersionConflictItem,
)
from app.tools.governance.metadata import (
    get_metadata,
    preview_metadata_change,
    search_metadata,
    submit_metadata_change,
)

OPS = [{"op": "set_tags", "name": "sales_dev.crm.customers", "column": "email", "tags": {"pii": None}}]
IDENTITY = {"_user_email": "owner@example.com", "_user_roles": "Data Steward, Requester",
            "_user_entitlements": "owner@example.com, crm-owners"}
ALL_TOOLS = (search_metadata, get_metadata, preview_metadata_change, submit_metadata_change)


@pytest.fixture()
def session(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(db_session_module, "get_lakebase_session", lambda: db)
    return db


@pytest.fixture()
def users(monkeypatch):
    seen = []

    def actor_from_user(db, user):
        seen.append(user)
        return Actor(id=user.id, actor_type=user.actor_type, email=user.email, roles=user.roles,
                     groups=user.entitlements)

    monkeypatch.setattr(authz, "actor_from_user", actor_from_user)
    return seen


@pytest.fixture()
def submitted(monkeypatch):
    seen = {}

    def submit(db, ask, actor, *, source):
        seen.update(ask=ask, actor=actor, source=source)
        return ChangeResult(status="pending_approval", request_id="req-1",
                            preview=ChangePreview(approval_count=1, report_markdown="**report**"))

    monkeypatch.setattr(changes, "submit", submit)
    return seen


def test_all_tools_are_published_over_mcp_behind_the_flag_and_accept_principals():
    for t in ALL_TOOLS:
        assert t.feature_flag == "metadata_v2"
        assert t.external is True
        assert t.allow_service_principals is True
        assert t.requires_identity is True
    assert submit_metadata_change.is_mutating
    assert not any(t.is_mutating for t in (search_metadata, get_metadata, preview_metadata_change))


@pytest.mark.asyncio
async def test_the_caller_is_rebuilt_from_the_injected_identity(session, users, submitted):
    await submit_metadata_change.execute(changes=OPS, reason="Flag PII", **IDENTITY)

    user = users[0]
    assert user.email == "owner@example.com"
    assert user.roles == ["Data Steward", "Requester"]
    assert user.entitlements == ["owner@example.com", "crm-owners"]
    assert user.actor_type == "user"
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_a_service_principal_keeps_its_type_and_application_id(session, users, submitted):
    await submit_metadata_change.execute(changes=OPS, _user_email="app-1", _actor_type="service_principal",
                                         _principal_id="app-1", _channel="mcp")

    assert users[0].actor_type == "service_principal"
    assert users[0].application_id == "app-1"
    assert submitted["actor"].actor_type == "service_principal"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel, source", [("mcp", "mcp"), (None, "agent")])
async def test_the_source_follows_the_channel(session, users, submitted, channel, source):
    await submit_metadata_change.execute(changes=OPS, reason="Flag PII", _channel=channel, **IDENTITY)

    assert submitted["source"] == source


@pytest.mark.asyncio
async def test_submit_returns_the_status_request_and_report_without_the_full_preview(session, users, submitted):
    out = await submit_metadata_change.execute(changes=OPS, reason="Flag PII", idempotency_key="k-1", **IDENTITY)

    assert out["ok"] is True
    assert out["status"] == "pending_approval"
    assert out["request_id"] == "req-1"
    assert out["report_markdown"] == "**report**"
    assert "preview" not in out
    assert submitted["ask"].idempotency_key == "k-1"
    assert submitted["ask"].on_approval_needed == "request"


@pytest.mark.asyncio
async def test_nothing_runs_without_an_identity(session, users, submitted):
    out = await submit_metadata_change.execute(changes=OPS, reason="Flag PII")

    assert out["ok"] is False
    assert "who is asking" in out["error"]
    assert submitted == {}


@pytest.mark.asyncio
async def test_a_conflict_tells_the_model_to_reread(session, users, monkeypatch):
    def submit(*a, **k):
        raise VersionConflict([VersionConflictItem(fqn_key="x", name="x", key="pii", expected_version=1,
                                                   current_version=2)])

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, **IDENTITY)

    assert out["status"] == "conflict"
    assert out["conflicts"][0]["current_version"] == 2
    assert "get_metadata" in out["next_step"]
    session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_a_blocked_change_returns_the_report_and_deny_findings(session, users, monkeypatch):
    preview = ChangePreview(blocked=True, report_markdown="blocked report",
                            findings=[Finding(code="key_unknown", severity="deny", message="Unknown key.")])

    def submit(*a, **k):
        raise ChangeBlocked(preview)

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, **IDENTITY)

    assert out["status"] == "blocked"
    assert out["report_markdown"] == "blocked report"
    assert out["findings"][0]["code"] == "key_unknown"


@pytest.mark.asyncio
async def test_preview_says_what_to_do_next(session, users, monkeypatch):
    monkeypatch.setattr(changes, "preview", lambda db, ask, actor: ChangePreview(
        approval_count=1, needs_approval=True, approver_groups=["crm-owners"], report_markdown="r"))

    out = await preview_metadata_change.execute(changes=OPS, **IDENTITY)

    assert out["ok"] is True
    assert "crm-owners" in out["next_step"]
    assert "submit_metadata_change" in out["next_step"]


@pytest.mark.asyncio
async def test_search_parses_tag_filters_and_caps_the_page(session, users, monkeypatch):
    seen = {}

    def fake_search(db, query, actor):
        seen["query"] = query
        return SearchResult(items=[], total=0)

    monkeypatch.setattr(search, "search", fake_search)

    out = await search_metadata.execute(tag="pii", tags=["!data_owner", "dataset=crm"], limit=100, **IDENTITY)

    assert out["ok"] is True
    ops = [(t.key, t.op, t.value) for t in seen["query"].tags]
    assert ops == [("pii", "exists", None), ("data_owner", "missing", None), ("dataset", "eq", "crm")]
    assert seen["query"].page_size == 100
