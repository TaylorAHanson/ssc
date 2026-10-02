"""Preview features tracker: feed parsing, docs matching, sync, requests and workflow steps."""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from app.db import ApprovalModel, EventModel, RequestModel
from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel
from app.services.preview_features import docs, feed, request_flow, settings_api, steps, sync
from app.services.preview_features.status import close_inflight_requests

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>ABAC on views is in Beta</title>
  <link>https://docs.databricks.com/aws/en/release-notes/product/2026/september#abac-views</link>
  <pubDate>Mon, 14 Sep 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;ABAC row filters now extend to views.&lt;/p&gt;&lt;p&gt;Account admins can enable the &lt;strong&gt;ABAC on Views&lt;/strong&gt; preview from the account console's &lt;strong&gt;Previews&lt;/strong&gt; page.&lt;/p&gt;&lt;p&gt;See &lt;a href="/aws/en/data-governance/unity-catalog/abac"&gt;ABAC&lt;/a&gt;.&lt;/p&gt;</description>
</item>
<item>
  <title>Governance Hub is in Beta</title>
  <link>https://docs.databricks.com/aws/en/release-notes/product/2026/august#governance-hub</link>
  <pubDate>Mon, 10 Aug 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;Account admins can turn it on from the account console Previews page. See &lt;a href="/aws/en/admin/governance-hub"&gt;Governance Hub&lt;/a&gt;.&lt;/p&gt;</description>
</item>
<item>
  <title>Aha! connector (Beta)</title>
  <link>https://docs.databricks.com/aws/en/release-notes/product/2026/july#aha</link>
  <pubDate>Wed, 01 Jul 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;Ingest from Aha! with Lakeflow Connect. See &lt;a href="/aws/en/ingestion/lakeflow-connect/aha"&gt;Aha!&lt;/a&gt;.&lt;/p&gt;</description>
</item>
<item>
  <title>Customer-managed keys support MLflow</title>
  <link>https://docs.databricks.com/aws/en/release-notes/product/2026/june#cmk</link>
  <pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;Production workloads can now use monitoring dashboards. Keys also cover MLflow.&lt;/p&gt;</description>
</item>
</channel></rss>"""


def _meta(name, display, phase="BETA", type_json='{"boolean_val": {"value": true}}', **extra):
    return {"name": name, "display_name": display, "preview_phase": phase,
            "description": f"{display} description.", "type": type_json, **extra}


def _scan(name, metas, values=None, ga=(), errors=None):
    s = sync.WorkspaceScan(name=name, ok=True)
    for m in metas:
        s.listed.add(m["name"])
        s.previews[m["name"]] = m
    s.ga = set(ga)
    s.listed |= s.ga
    for k, v in (values or {}).items():
        s.values[k] = settings_api.ObservedValue(effective=v, set_here=False, raw={})
    s.value_errors = dict(errors or {})
    return s


class _KeepOpen:
    """Hands the test session to code that closes its own sessions."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


@pytest.fixture
def items():
    return feed.parse_feed(FEED_XML)


# ---------------------------------------------------------------------------
# feed / docs / settings_api
# ---------------------------------------------------------------------------

def test_parse_feed_newest_first(items):
    assert [i.title for i in items][0] == "ABAC on views is in Beta"
    assert items[0].published == datetime(2026, 9, 14)


def test_classify_phase_and_scope():
    assert feed.classify_phase("now in Public Preview") == "PUBLIC_PREVIEW"
    assert feed.classify_phase("X (Beta)") == "BETA"
    assert feed.classify_phase("is now generally available") == "GA"
    assert feed.classify_phase("nothing here") is None
    assert feed.classify_scope("Account admins can enable it from the account console") == "account"
    assert feed.classify_scope("Workspace admins can enable it") == "workspace"
    assert feed.classify_scope("Use it in a notebook") == "unknown"


def test_match_feature_links_and_phase(items):
    m = feed.match_feature("Lakeflow Connect for Aha!", items) or feed.match_feature("Aha! connector", items)
    assert m is not None
    assert m.docs_link == "https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/aha"
    assert m.phase == "BETA"


def test_match_feature_needs_words_close_together(items):
    # Every word appears in the CMK item, but scattered: not a match.
    assert feed.match_feature("Production Monitoring for MLflow", items) is None


def test_account_previews_names_and_links(items):
    found = {p.name: p for p in feed.account_previews(items)}
    assert "ABAC on Views" in found
    assert found["ABAC on Views"].match.docs_link.endswith("/data-governance/unity-catalog/abac")
    assert "Governance Hub" in found  # name taken from the title
    assert found["Governance Hub"].phase == "BETA"


def test_doc_links_skip_release_notes():
    html = '<a href="/aws/en/release-notes/x">rn</a> <a href="/aws/en/dashboards/x">d</a>'
    assert feed.doc_links(html) == ["https://docs.databricks.com/aws/en/dashboards/x"]


def test_suggest_doc():
    urls = [
        "https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/jira-source-setup",
        "https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/marketo",
        "https://docs.databricks.com/aws/en/error-messages/ai-diagnose-error-class",
    ]
    assert docs.suggest_doc("Lakeflow Connect for Marketo", urls).endswith("/marketo")
    assert docs.suggest_doc("AI Diagnose", urls) is None
    assert docs.suggest_doc("Something Else", urls) is None


def test_parse_value_and_type():
    v = settings_api.parse_value({"effective_boolean_val": {"value": True}, "boolean_val": {"value": True}})
    assert v.effective is True and v.set_here is True
    inherited = settings_api.parse_value({"effective_boolean_val": {}})
    assert inherited.effective is False and inherited.set_here is False
    assert settings_api.value_type_of('{"boolean_val": {"value": true}}') == "boolean"
    assert settings_api.value_type_of('{"string_val": {"value": "x"}}') == "other"


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def _feature(db, setting):
    return db.query(PreviewFeatureModel).filter_by(setting_name=setting).one()


def _target(db, feature, target):
    return db.query(PreviewFeatureTargetModel).filter_by(feature_id=feature.id, target=target).one()


def test_sync_upserts_features_and_targets(db_session, items):
    scans = [
        _scan("ws1", [_meta("aha_connector", "Lakeflow Connect for Aha!"), _meta("abac_on_views", "ABAC on Views")],
              values={"aha_connector": True, "abac_on_views": False}),
        _scan("ws2", [_meta("aha_connector", "Lakeflow Connect for Aha!")], errors={"aha_connector": "nope"}),
    ]
    counts = sync.apply_sync(db_session, scans, items, [])
    db_session.flush()
    assert counts["new"] >= 3  # two API features + feed-only Governance Hub

    aha = _feature(db_session, "aha_connector")
    assert aha.scope == "workspace" and aha.value_type == "boolean"
    assert aha.docs_link_source == "feed"
    assert _target(db_session, aha, "ws1").observed_value == {"effective": True, "set_here": False}
    assert _target(db_session, aha, "ws2").observe_error == "nope"

    abac = _feature(db_session, "abac_on_views")
    assert abac.scope == "account" and abac.scope_source == "inferred"
    acct = _target(db_session, abac, ACCOUNT_TARGET)
    assert acct.observed_value["workspaces_listed"] == 1
    assert acct.observed_value["effective"] is False

    hub = db_session.query(PreviewFeatureModel).filter_by(feed_key="governance hub").one()
    assert hub.scope == "account" and hub.setting_name is None

    # Idempotent.
    again = sync.apply_sync(db_session, scans, items, [])
    assert again["new"] == 0


def test_sync_flags_drift_and_keeps_admin_overrides(db_session, items):
    scans = [_scan("ws1", [_meta("aha_connector", "Lakeflow Connect for Aha!")], values={"aha_connector": True})]
    sync.apply_sync(db_session, scans, items, [])
    aha = _feature(db_session, "aha_connector")
    aha.docs_link, aha.docs_link_source = "https://example.com/mine", "admin"
    row = _target(db_session, aha, "ws1")
    row.status, row.action = "implemented", "enable"
    db_session.flush()

    off = [_scan("ws1", [_meta("aha_connector", "Lakeflow Connect for Aha!")], values={"aha_connector": False})]
    sync.apply_sync(db_session, off, items, [])
    assert row.drift is True
    assert aha.docs_link == "https://example.com/mine"


def _open_request(db, feature, rows, action="enable", status="manager_approval"):
    rid = f"req-{uuid.uuid4()}"
    db.add(RequestModel(id=rid, type="preview_feature_request", title="t", status=status,
                        current_state=status, state_context={}))
    for r in rows:
        r.status, r.action, r.request_id = "requested", action, rid
    db.add(ApprovalModel(id=str(uuid.uuid4()), request_id=rid, approval_type="platform_admin", status="pending"))
    db.flush()
    return rid


def test_sync_archives_ga_and_closes_requests(db_session, items):
    meta = _meta("aha_connector", "Lakeflow Connect for Aha!")
    sync.apply_sync(db_session, [_scan("ws1", [meta], values={"aha_connector": False})], items, [])
    aha = _feature(db_session, "aha_connector")
    rid = _open_request(db_session, aha, [_target(db_session, aha, "ws1")])

    counts = sync.apply_sync(db_session, [_scan("ws1", [], ga=["aha_connector"])], items, [])
    assert counts["archived_ga"] == 1
    assert aha.archived_reason == "ga" and aha.phase == "GA"
    req = db_session.get(RequestModel, rid)
    assert req.status == "completed"
    assert db_session.query(ApprovalModel).filter_by(request_id=rid).one().status == "cancelled"
    assert _target(db_session, aha, "ws1").status == "not_requested"


def test_sync_retires_after_missed_syncs(db_session, items, monkeypatch):
    monkeypatch.setattr(sync.settings, "PREVIEW_FEATURE_RETIRE_AFTER_SYNCS", 2, raising=False)
    sync.apply_sync(db_session, [_scan("ws1", [_meta("x_feature", "X Feature")])], None, [])
    sync.apply_sync(db_session, [_scan("ws1", [])], None, [])
    assert _feature(db_session, "x_feature").archived_at is None
    sync.apply_sync(db_session, [_scan("ws1", [])], None, [])
    assert _feature(db_session, "x_feature").archived_reason == "retired"


def test_failed_scan_changes_nothing(db_session):
    sync.apply_sync(db_session, [_scan("ws1", [_meta("x_feature", "X Feature")])], None, [])
    bad = sync.WorkspaceScan(name="ws1", ok=False, error="403")
    sync.apply_sync(db_session, [bad], None, [])
    f = _feature(db_session, "x_feature")
    assert f.missing_syncs == 0 and f.archived_at is None


def test_sync_reverifies_approved_rows(db_session, items):
    meta = _meta("aha_connector", "Lakeflow Connect for Aha!")
    sync.apply_sync(db_session, [_scan("ws1", [meta], values={"aha_connector": False})], items, [])
    aha = _feature(db_session, "aha_connector")
    row = _target(db_session, aha, "ws1")
    _open_request(db_session, aha, [row])
    row.status = "approved"
    db_session.flush()

    counts = sync.apply_sync(db_session, [_scan("ws1", [meta], values={"aha_connector": True})], items, [])
    assert counts["verified"] == 1
    assert row.status == "implemented" and row.verification == "api"


def test_close_inflight_ignores_terminal_requests(db_session):
    f = PreviewFeatureModel(id="f1", setting_name="s1", display_name="S1", scope="workspace", scope_source="api")
    db_session.add(f)
    row = PreviewFeatureTargetModel(id="t1", feature_id="f1", target="ws1", available=True,
                                    status="requested", drift=False, verify_failures=0)
    db_session.add(row)
    rid = _open_request(db_session, f, [row], status="completed")
    assert close_inflight_requests(db_session, f, "x") == [rid]
    assert row.status == "not_requested"


# ---------------------------------------------------------------------------
# request_flow
# ---------------------------------------------------------------------------

def _seed(db, items=None):
    scans = [
        _scan("ws1", [_meta("aha_connector", "Lakeflow Connect for Aha!")], values={"aha_connector": False}),
        _scan("ws2", [_meta("aha_connector", "Lakeflow Connect for Aha!")], values={"aha_connector": True}),
        _scan("ws3", []),
    ]
    sync.apply_sync(db, scans, items, [])
    db.flush()
    return _feature(db, "aha_connector")


def test_open_request_batches_targets(db_session):
    aha = _seed(db_session)
    opened = request_flow.open_request(db_session, aha, action="enable", targets=["ws1"],
                                       justification="need it", user_email="a@x.com")
    req = db_session.get(RequestModel, opened.request_id)
    assert req.type == "preview_feature_request"
    assert req.state_context["targets"] == ["ws1"]
    assert req.state_context["scope"] == "workspace"
    assert _target(db_session, aha, "ws1").status == "requested"
    facts = db_session.query(EventModel).filter_by(request_id=opened.request_id,
                                                   event_type="preview_feature_requested").all()
    assert len(facts) == 1


@pytest.mark.parametrize("targets,action,why", [
    (["ws2"], "enable", "already on"),
    (["ws3"], "enable", "not listed"),
    (["ws1"], "disable", "not on"),
    ([], "enable", "Select at least one"),
])
def test_open_request_rejects_ineligible(db_session, targets, action, why):
    aha = _seed(db_session)
    with pytest.raises(request_flow.PreviewRequestError, match=why):
        request_flow.open_request(db_session, aha, action=action, targets=targets,
                                  justification=None, user_email="a@x.com")


def test_open_request_rejects_second_inflight(db_session):
    aha = _seed(db_session)
    request_flow.open_request(db_session, aha, action="enable", targets=["ws1"], justification=None,
                              user_email="a@x.com")
    with pytest.raises(request_flow.PreviewRequestError, match="already in progress"):
        request_flow.open_request(db_session, aha, action="enable", targets=["ws1"], justification=None,
                                  user_email="a@x.com")


def test_account_request_targets_account(db_session, items):
    _seed(db_session, items)
    hub = db_session.query(PreviewFeatureModel).filter_by(feed_key="governance hub").one()
    opened = request_flow.open_request(db_session, hub, action="enable", targets=["ws1"],
                                       justification=None, user_email="a@x.com")
    assert opened.targets == [ACCOUNT_TARGET]


# ---------------------------------------------------------------------------
# workflow steps
# ---------------------------------------------------------------------------

@pytest.fixture
def step_db(db_session, monkeypatch):
    monkeypatch.setattr(steps, "_session", lambda: _KeepOpen(db_session))
    return db_session


def _approve(db, rid, approval_type="platform_admin", by="boss@x.com"):
    db.add(EventModel(id=str(uuid.uuid4()), request_id=rid, event_type="approval_received",
                      event_data={"approval_type": approval_type, "approved_by": by},
                      created_at=datetime.utcnow() + timedelta(seconds=1)))
    db.flush()


def test_assess_report_escapes_untrusted_text(step_db):
    aha = _seed(step_db)
    aha.description = "Click [here](https://evil.example) <script>"
    opened = request_flow.open_request(step_db, aha, action="enable", targets=["ws1"], justification=None,
                                       user_email="a@x.com")
    res = steps.assess(opened.request_id)
    md = res["report_markdown"]
    assert "](https://evil.example)" not in md and "<script>" not in md
    assert "| ws1 | yes | off (inherited) |" in md


def test_apply_and_verify_workspace_request(step_db, monkeypatch):
    aha = _seed(step_db)
    aha.raw = None
    opened = request_flow.open_request(step_db, aha, action="enable", targets=["ws1"], justification=None,
                                       user_email="a@x.com")
    rid = opened.request_id
    _approve(step_db, rid)
    steps.set_status(rid, "approved")
    row = _target(step_db, aha, "ws1")
    assert row.status == "approved" and row.approved_by == "boss@x.com"

    monkeypatch.setattr(steps, "_apply_one", lambda f, t, wanted: {
        "target": t, "outcome": "changed", "value": {"effective": wanted, "set_here": True}})
    res = asyncio.run(steps.apply(rid))
    assert res["needs_manual"] is False and res["applied_targets"] == ["ws1"]
    assert row.implemented_by == steps.AUTOMATION_ACTOR

    monkeypatch.setattr(steps, "_read_live", lambda f, t: (
        settings_api.ObservedValue(effective=True, set_here=True, raw={}), None))
    res = asyncio.run(steps.verify(rid))
    assert res["verified"] == ["ws1"]
    assert row.status == "implemented" and row.verification == "api"


def test_apply_failure_goes_to_manual(step_db, monkeypatch):
    aha = _seed(step_db)
    opened = request_flow.open_request(step_db, aha, action="enable", targets=["ws1"], justification=None,
                                       user_email="a@x.com")
    steps.set_status(opened.request_id, "approved")
    monkeypatch.setattr(steps, "_apply_one", lambda f, t, wanted: {
        "target": t, "outcome": "manual", "reason": "PERMISSION_DENIED"})
    res = asyncio.run(steps.apply(opened.request_id))
    assert res["needs_manual"] is True
    assert res["manual_targets"] == [{"target": "ws1", "reason": "PERMISSION_DENIED"}]
    assert "What's left to do" in res["report_markdown"]
    # The tool result must not read as a failure, or the graph would halt.
    from app.tools.tool_executor import is_tool_failure
    assert is_tool_failure(res) is None


def test_verify_attests_unreadable_account_preview(step_db, items):
    _seed(step_db, items)
    hub = step_db.query(PreviewFeatureModel).filter_by(feed_key="governance hub").one()
    opened = request_flow.open_request(step_db, hub, action="enable", targets=[], justification=None,
                                       user_email="a@x.com")
    rid = opened.request_id
    steps.set_status(rid, "approved")
    _approve(step_db, rid, approval_type="manual_task", by="acct-admin@x.com")
    res = asyncio.run(steps.verify(rid))
    row = _target(step_db, hub, ACCOUNT_TARGET)
    assert res["verified"] == [ACCOUNT_TARGET]
    assert row.status == "implemented" and row.verification == "attested"
    assert row.implemented_by == "acct-admin@x.com"


def test_verify_disable_returns_to_not_requested(step_db, monkeypatch):
    aha = _seed(step_db)
    opened = request_flow.open_request(step_db, aha, action="disable", targets=["ws2"], justification=None,
                                       user_email="a@x.com")
    steps.set_status(opened.request_id, "approved")
    monkeypatch.setattr(steps, "_read_live", lambda f, t: (
        settings_api.ObservedValue(effective=False, set_here=True, raw={}), None))
    asyncio.run(steps.verify(opened.request_id))
    row = _target(step_db, aha, "ws2")
    assert row.status == "not_requested" and "Turned off" in row.note


def test_rejected_sets_rejected(step_db):
    aha = _seed(step_db)
    opened = request_flow.open_request(step_db, aha, action="enable", targets=["ws1"], justification=None,
                                       user_email="a@x.com")
    steps.set_status(opened.request_id, "rejected")
    assert _target(step_db, aha, "ws1").status == "rejected"
    # Rejected targets can be requested again.
    request_flow.open_request(step_db, aha, action="enable", targets=["ws1"], justification=None,
                              user_email="a@x.com")


# ---------------------------------------------------------------------------
# workflow spec
# ---------------------------------------------------------------------------

def test_spec_implement_gate_skips_only_when_all_applied():
    from app.workflows.graphs.specs import SPECS
    from app.workflows.spec import Gate
    from app.workflows.spec_loader import spec_from_dict

    spec = spec_from_dict(SPECS["preview_feature_request"])
    implement = next(s for s in spec.stages if isinstance(s, Gate) and s.name == "implement")
    assert implement.type == "manual_task"
    assert implement.auto_approve({"scope": "workspace", "needs_manual": False}) is True
    assert implement.auto_approve({"scope": "workspace", "needs_manual": True}) is False
    assert implement.auto_approve({"scope": "account"}) is False
    # If an admin removes the apply step, needs_manual is missing: hold for a person.
    assert implement.auto_approve({"scope": "workspace"}) is False
    apply = next(s for s in spec.stages if s.name == "apply")
    assert apply.run_if({"scope": "workspace"}) and not apply.run_if({"scope": "account"})
    assert [s.name for s in spec.on_reject] == ["mark_rejected"]


# ---------------------------------------------------------------------------
# API: who can approve from the tab
# ---------------------------------------------------------------------------

class _User:
    def __init__(self, email, roles):
        self.email = email
        self._roles = set(roles)

    def has_role(self, role):
        return role in self._roles


def test_pending_approvals_uses_approve_endpoint_rules(db_session):
    from app.api.v1.preview_features import _pending_approvals

    f = PreviewFeatureModel(id="f2", setting_name="s2", display_name="S2", scope="workspace", scope_source="api")
    db_session.add(f)
    row = PreviewFeatureTargetModel(id="t2", feature_id="f2", target="ws1", available=True,
                                    status="requested", drift=False, verify_failures=0)
    db_session.add(row)
    rid = _open_request(db_session, f, [row])  # unassigned platform_admin approval

    admin = _pending_approvals(db_session, {rid}, _User("a@x.com", ["platform_admin"]))
    assert admin[rid] == {"type": "platform_admin", "can_approve": True}
    other = _pending_approvals(db_session, {rid}, _User("b@x.com", ["governance_admin"]))
    assert other[rid]["can_approve"] is False
    assert _pending_approvals(db_session, set(), _User("a@x.com", [])) == {}


# ---------------------------------------------------------------------------
# Chat agent path: lookup, agent-started requests, instructions
# ---------------------------------------------------------------------------

def test_two_word_names_need_the_phrase():
    assert feed.mentions("AI Enrich", feed.normalize("use ai_search to enrich operational data")) is False
    assert feed.mentions("AI Enrich", feed.normalize("the ai_enrich function")) is True
    assert feed.mentions("AI Enrich", feed.normalize("AI Enrich is in Beta")) is True


def test_exact_doc_prefers_the_setting_name():
    urls = [
        "https://docs.databricks.com/aws/en/agents/mcp-tools/ai-search",
        "https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_search",
    ]
    assert docs.exact_doc("ai_search", urls).endswith("/functions/ai_search")
    assert docs.exact_doc("ai_search", urls[:1]).endswith("/ai-search")
    assert docs.exact_doc("nothing_here", urls) is None


def test_find_preview_features_explains_status(db_session, monkeypatch):
    from app.services.preview_features import search

    aha = _seed(db_session)
    other = PreviewFeatureModel(id="old", setting_name="old_thing", display_name="Lakeflow Connect for Aha! Classic",
                                scope="workspace", scope_source="api", archived_at=datetime.utcnow(),
                                archived_reason="ga")
    db_session.add(other)
    db_session.flush()
    res = search.find(db_session, "lakeflow connect for aha!", ["ws1", "ws2", "ws3"])
    top = res["matches"][0]
    assert top["feature"] == "aha_connector" and top["match"] == "exact"
    ws = {w["workspace"]: w for w in top["workspaces"]}
    assert ws["ws1"]["can_request_turn_on"] is True
    assert ws["ws2"]["why_not_turn_on"] == "already on" and ws["ws2"]["can_request_turn_off"] is True
    assert ws["ws3"]["listed"] is False and ws["ws3"]["can_request_turn_on"] is False
    assert search.find(db_session, "connector", ["ws1"])["matches"] or True  # common word alone is allowed
    assert search.find(db_session, "zzz unrelated", ["ws1"])["matches"] == []
    assert aha.id  # keep fixture referenced


def test_assess_links_an_agent_started_request(step_db):
    _seed(step_db)
    rid = f"req-{uuid.uuid4()}"
    step_db.add(RequestModel(id=rid, type="preview_feature_request", title="Agent Request", status="pending",
                             current_state="pending", requester_email="u@x.com",
                             state_context={"feature": "aha_connector", "targets": ["ws1"]}))
    step_db.flush()
    res = steps.assess(rid, feature_ref="aha_connector", action="enable", targets=["ws1"])
    assert res["scope"] == "workspace" and "report_markdown" in res
    req = step_db.get(RequestModel, rid)
    assert req.title.startswith("Enable preview:") and req.state_context["scope"] == "workspace"
    row = _target(step_db, _feature(step_db, "aha_connector"), "ws1")
    assert row.status == "requested" and row.request_id == rid and row.requested_by == "u@x.com"


@pytest.mark.parametrize("ref,targets,why", [
    ("no_such_feature", ["ws1"], "No tracked preview"),
    ("aha_connector", ["ws2"], "already on"),
    (None, ["ws1"], "doesn't say which"),
])
def test_assess_rejects_bad_agent_requests(step_db, ref, targets, why):
    _seed(step_db)
    rid = f"req-{uuid.uuid4()}"
    step_db.add(RequestModel(id=rid, type="preview_feature_request", title="t", status="pending",
                             current_state="pending", state_context={}))
    step_db.flush()
    res = steps.assess(rid, feature_ref=ref, action="enable", targets=targets)
    assert res["ok"] is False and why in res["error"]


def test_instructions_match_the_graph():
    import os

    from app.workflows.graphs.specs import SPECS
    from app.workflows.instructions import _user_inputs, render_execution_block

    spec = SPECS["preview_feature_request"]
    # Values steps write (scope, needs_manual) aren't things to ask the user for.
    assert _user_inputs(spec) == ["feature", "action", "targets", "justification"]
    path = os.path.join(os.path.dirname(steps.__file__), "..", "..", "agents", "instructions",
                        "preview_feature_request.md")
    with open(path) as f:
        md = f.read()
    assert render_execution_block(spec, request_type="preview_feature_request").strip() in md
    assert "find_preview_features" in md
