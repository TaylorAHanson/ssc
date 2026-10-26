"""The Metadata Manager chat and MCP tools (ADR-0006 D6): who the caller is, where a change
came from, who may browse, and how service errors reach the model."""
import json
from unittest.mock import MagicMock

import pytest

import app.db.session as db_session_module
from app.core.config import settings
from app.services.metadata import authz, changes, detail, search
from app.services.metadata.authz import BrowseScope
from app.services.metadata.errors import ChangeBlocked, VersionConflict
from app.services.metadata.types import (
    Actor,
    AuthzDecision,
    ChangeItem,
    ChangePreview,
    ChangeResult,
    Finding,
    ItemDecision,
    ObjectDetail,
    ObjectKey,
    PrincipalScope,
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


@pytest.fixture(autouse=True)
def owned(monkeypatch):
    """Actor id -> its owner-scoped BrowseScope; governance per ``has_full_browse``, else nothing."""
    scopes = {}
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])
    monkeypatch.setattr(settings, "METADATA_EDITOR_ROLES", ["Governance Admin"])

    def browse_scope(db, actor, editable=None):
        if authz.has_full_browse(actor):
            return BrowseScope(level="full")
        return scopes.get(actor.id, BrowseScope(level="none"))

    monkeypatch.setattr(authz, "browse_scope", browse_scope)
    return scopes


@pytest.fixture()
def users(monkeypatch):
    seen = []

    def actor_from_user(db, user):
        seen.append(user)
        sp = user.actor_type == "service_principal"
        return Actor(id=user.id, actor_type=user.actor_type, email=user.email, roles=user.roles,
                     groups=user.entitlements, is_admin=not sp and authz._is_admin_roles(user.roles),
                     principal=PrincipalScope(application_id=user.id) if sp else None)

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

    def fake_search(db, query, actor, *, browse=None):
        seen["query"] = query
        return SearchResult(items=[], total=0)

    monkeypatch.setattr(search, "search", fake_search)

    out = await search_metadata.execute(tag="pii", tags=["!data_owner", "dataset=crm"], limit=100, **IDENTITY)

    assert out["ok"] is True
    ops = [(t.key, t.op, t.value) for t in seen["query"].tags]
    assert ops == [("pii", "exists", None), ("data_owner", "missing", None), ("dataset", "eq", "crm")]
    assert seen["query"].page_size == 100


# --- Browse roles ------------------------------------------------------------------------

READ_CALLS = [
    pytest.param(search_metadata, {"query": "customers"}, id="search_metadata"),
    pytest.param(get_metadata, {"name": "sales_dev.crm.customers"}, id="get_metadata"),
    pytest.param(preview_metadata_change, {"changes": OPS}, id="preview_metadata_change"),
]
OUTSIDER = {"_user_email": "outsider@example.com", "_user_roles": "Requester",
            "_user_entitlements": "outsider@example.com"}


class _Calls(list):
    scopes: list


@pytest.fixture()
def reads(monkeypatch):
    called = _Calls()
    called.scopes = []

    def fake_search(db, query, actor, *, browse=None):
        called.append(actor)
        called.scopes.append(browse)
        return SearchResult(items=[], total=0)

    def fake_detail(db, actor, name, column=None, *, browse=None):
        called.append(actor)
        called.scopes.append(browse)
        key = ObjectKey.parse(name, column)
        return ObjectDetail(key=key, fqn_key=key.fqn_key)

    def fake_preview(db, ask, actor):
        called.append(actor)
        return ChangePreview(direct_count=1, report_markdown="r")

    monkeypatch.setattr(search, "search", fake_search)
    monkeypatch.setattr(detail, "object_detail", fake_detail)
    monkeypatch.setattr(changes, "preview", fake_preview)
    return called


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_fn, args", READ_CALLS)
async def test_a_user_outside_the_browse_roles_is_refused(session, users, reads, monkeypatch, tool_fn, args):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])

    out = await tool_fn.execute(**args, **OUTSIDER)

    assert out["ok"] is False
    assert out["status"] == "forbidden"
    assert "browse roles" in out["error"]
    assert "Data Steward" not in out["error"]
    assert reads == []
    session.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_fn, args", READ_CALLS)
async def test_a_user_in_the_browse_roles_may_read(session, users, reads, monkeypatch, tool_fn, args):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["data_steward"])

    out = await tool_fn.execute(**args, **IDENTITY)

    assert out["ok"] is True
    assert [a.id for a in reads] == ["owner@example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_fn, args", READ_CALLS)
async def test_blank_browse_roles_adds_nobody(session, users, reads, monkeypatch, tool_fn, args):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", [])

    outsider = await tool_fn.execute(**args, **OUTSIDER)
    steward = await tool_fn.execute(**args, **IDENTITY)

    assert outsider["status"] == steward["status"] == "forbidden"
    assert reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", ["Platform Admin", "Governance Admin"])
@pytest.mark.parametrize("tool_fn, args", READ_CALLS)
async def test_admins_and_editor_roles_always_read_everything(session, users, reads, monkeypatch, tool_fn, args,
                                                              roles):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", [])

    out = await tool_fn.execute(**args, **{**OUTSIDER, "_user_roles": roles})

    assert out["ok"] is True
    assert all(s is None or s.full for s in reads.scopes)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_fn, args", READ_CALLS[:2])
async def test_an_owner_reads_within_its_scope(session, users, reads, owned, tool_fn, args):
    scope = BrowseScope(level="owned", owned=["sales_dev.crm"])
    owned["outsider@example.com"] = scope

    out = await tool_fn.execute(**args, **OUTSIDER)

    assert out["ok"] is True
    assert reads.scopes == [scope]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_fn, args", READ_CALLS)
async def test_a_registered_service_principal_may_read_like_on_the_api(session, users, reads, monkeypatch,
                                                                       tool_fn, args):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])

    out = await tool_fn.execute(**args, _user_email="app-1", _actor_type="service_principal",
                                _principal_id="app-1", _channel="mcp")

    assert out["ok"] is True
    assert reads[0].actor_type == "service_principal"


@pytest.mark.asyncio
async def test_submit_is_left_to_edit_authz_like_on_the_api(session, users, submitted, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])

    out = await submit_metadata_change.execute(changes=OPS, reason="Flag PII", **OUTSIDER)

    assert out["status"] == "pending_approval"
    assert submitted["actor"].id == "outsider@example.com"


# --- Submit outside the browse scope ----------------------------------------------------------

HIDDEN = "hr_dev.people.staff"


def _hidden_item(before="people_secret") -> ItemDecision:
    item = ChangeItem(fqn_key=HIDDEN, name=HIDDEN, securable_type="table", field="tag", key="domain", action="set",
                      before=before, before_exists=True, before_version=3, after="hr", object_exists=True)
    return ItemDecision(item=item, decision=AuthzDecision(allowed=False, needs_approval=True, can_request=True,
                                                          reasons=["No owner."]))


@pytest.fixture()
def an_owner(owned):
    owned["outsider@example.com"] = BrowseScope(level="owned", owned=["sales_dev.crm"])


@pytest.mark.asyncio
async def test_submit_hides_current_values_outside_the_scope(session, users, an_owner, monkeypatch):
    def submit(db, ask, actor, *, source):
        return ChangeResult(status="pending_approval", request_id="req-1",
                            preview=ChangePreview(items=[_hidden_item()], approval_count=1,
                                                  report_markdown="| people_secret → hr |"))

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, reason="Rename", **OUTSIDER)

    assert out["status"] == "pending_approval"
    assert "people_secret" not in json.dumps(out)
    assert "(hidden)" in out["report_markdown"]


@pytest.mark.asyncio
async def test_a_governance_submit_keeps_current_values(session, users, monkeypatch):
    def submit(db, ask, actor, *, source):
        return ChangeResult(status="pending_approval", request_id="req-1",
                            preview=ChangePreview(items=[_hidden_item()], report_markdown="| people_secret → hr |"))

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, reason="Rename", **IDENTITY)

    assert "people_secret" in out["report_markdown"]


@pytest.mark.asyncio
async def test_a_conflict_outside_the_scope_hides_the_current_value(session, users, an_owner, monkeypatch):
    def submit(*a, **k):
        raise VersionConflict([VersionConflictItem(fqn_key=HIDDEN, name=HIDDEN, key="domain", expected_version=1,
                                                   current_version=2, current_value="people_secret")])

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, **OUTSIDER)

    assert out["status"] == "conflict"
    assert out["conflicts"][0]["current_value"] is None
    assert "people_secret" not in json.dumps(out)


@pytest.mark.asyncio
async def test_a_blocked_submit_outside_the_scope_hides_current_values(session, users, an_owner, monkeypatch):
    def submit(*a, **k):
        raise ChangeBlocked(ChangePreview(blocked=True, items=[_hidden_item()],
                                          report_markdown="| people_secret → hr |",
                                          findings=[Finding(code="key_unknown", severity="deny", message="No.")]))

    monkeypatch.setattr(changes, "submit", submit)

    out = await submit_metadata_change.execute(changes=OPS, **OUTSIDER)

    assert out["status"] == "blocked"
    assert "people_secret" not in json.dumps(out)
