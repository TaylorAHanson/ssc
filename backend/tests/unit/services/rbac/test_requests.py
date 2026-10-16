"""Tests for filing rbac_access_change requests and the workflow's plan / apply steps (ADR 0005 D4, D8)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.db import EventModel, RequestModel
from app.db.base import Base
from app.db.rbac import RbacChangeSetModel, RbacRuleModel
from app.models.rbac import AccessChangeIn, AccessItem, ChangeSetIn, ExportStatus, RowChange
from app.services.rbac import approvers, audit, csv_export, inflight, layout, ledger
from app.services.rbac import requests as rq
from app.state_machines.facts import add_fact

UC_DEV = "UC_Securables_RBAC_Dev"
WS_DEV = "WS_Securables_RBAC_Dev"
OWNER = "metastore_admins"
REQUESTER = "ana@example.com"
APPROVER = "approver@example.com"
ADMIN = "admin@example.com"
JUSTIFICATION = "Needed to build the quarterly revenue dashboard for finance"
REASON = "Quarterly access review clean-up"


@pytest.fixture()
def db(monkeypatch):
    """A private in-memory database, also handed to the steps (which close their session)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    real_close = session.close
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(rq, "_session", lambda: session)
    try:
        yield session
    finally:
        real_close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "aliases": "development", "sheet_suffix": "Dev", "self_service": True,
         "write_privileges": True, "service_principals": True, "two_person_rule": True},
    ])
    monkeypatch.setattr(settings, "RBAC_UC_SHEET_PATTERN", "UC_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_WS_SHEET_PATTERN", "WS_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_METASTORE_SHEET", "UC_metastore_RBAC")
    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 200)
    monkeypatch.setattr(settings, "RBAC_MIN_REASON_LENGTH", 10)
    monkeypatch.setattr(settings, "RBAC_MIN_JUSTIFICATION_LENGTH", 20)
    monkeypatch.setattr(settings, "RBAC_HARD_DENIED_PRIVILEGES", ["ALL_PRIVILEGES", "MANAGE"])
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["account users", "users", "admins"])
    monkeypatch.setattr(settings, "RBAC_NAMING_RULES", [])
    monkeypatch.setattr(settings, "RBAC_DEFAULT_OWNER", "")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", False)
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "0 * * * *")


@pytest.fixture(autouse=True)
def _approvers_and_exports(monkeypatch):
    monkeypatch.setattr(approvers, "approver_group", lambda plane: {"uc": "uc-approvers"}.get(plane))
    monkeypatch.setattr(csv_export, "request_export_soon", lambda: None)


def seed(db, sheet=UC_DEV, *, position, principals="g_analysts", privileges="USE_SCHEMA;SELECT",
         kind="schema", catalog="sales_dev", schema="orders", workspace=""):
    spec = layout.sheet_spec(sheet)
    cells = dict(
        environment_value=spec.environment or "", securable_type=kind, catalog=catalog, schema_name=schema,
        object_name="", workspace_name=workspace, owner=OWNER if spec.plane == "uc" else "",
        principal_type="group", principal_name=principals, privilege=privileges,
    )
    rule = RbacRuleModel(
        id=audit.new_id(), sheet=sheet, plane=spec.plane, environment=spec.environment, position=position,
        extra={}, status="active", version=1, row_hash=audit.compute_row_hash(sheet, cells, {}),
        source="import", **cells,
    )
    db.add(rule)
    db.commit()
    return rule


def item(principal="g_new", privileges=("SELECT",), **kw):
    fields = dict(environment="dev", securable_type="schema", catalog="sales_dev", schema_name="orders")
    fields.update(kw)
    return AccessItem(principal_name=principal, privileges=list(privileges), **fields)


def ws_item(**kw):
    return item(principal="g_ws", securable_type="workspace", catalog="", schema_name="", workspace_name="ws-dev",
                privileges=["USER"], **kw)


def plan(db, *items, justification=JUSTIFICATION):
    return ledger.plan_access_change(db, AccessChangeIn(items=list(items), justification=justification),
                                     requester=REQUESTER)


def agent_request(db, request_id, *items, justification=JUSTIFICATION, group_ref=None):
    """A request as execute_workflow files it: parameters + requested_by_email, generic title."""
    ctx = {"items": [i.model_dump(mode="json") for i in items], "justification": justification,
           "requested_by_email": REQUESTER}
    if group_ref:
        ctx["group_ref"] = group_ref
    db.add(RequestModel(id=request_id, type=rq.WORKFLOW_ID, title=f"Agent Request: {rq.WORKFLOW_ID}",
                        status="pending", current_state="pending", state_context=ctx, requester_email=REQUESTER))
    db.commit()
    return ctx


def run_plan(db, request_id):
    ctx = db.get(RequestModel, request_id).state_context
    return rq.plan_step(request_id, items=ctx.get("items"), justification=ctx.get("justification"),
                        group_ref=ctx.get("group_ref"))


def approve(db, request_id, by=APPROVER):
    add_fact(db, request_id, "approval_received", {"approval_type": "platform_admin", "approved_by": by}, actor=by)


def stored_plan(db, request_id):
    db.expire_all()
    return db.get(RequestModel, request_id).state_context[rq.PLAN_CONTEXT_KEY]


def facts(db, request_id, kind):
    return db.query(EventModel).filter(EventModel.request_id == request_id, EventModel.event_type == kind).all()


# --- Filing an access change --------------------------------------------------------


def test_submit_access_change_files_one_request_with_the_plan(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_analysts", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = plan(db, item()).proposals
    assert [p.securable_type for p in proposal.added_prerequisites] == ["catalog"]

    request_id = rq.submit_access_change(db, proposal, requester_email=REQUESTER, justification=JUSTIFICATION,
                                         group_ref="grp-1")

    request = db.get(RequestModel, request_id)
    assert (request.type, request.status, request.requester_email) == (rq.WORKFLOW_ID, "pending", REQUESTER)
    assert request.title == "Access change: grant USE_SCHEMA;SELECT on schema sales_dev.orders to g_new"
    ctx = request.state_context
    # Prerequisites are re-derived at plan time, so only the requested item is stored.
    assert [i["securable_type"] for i in ctx["items"]] == ["schema"]
    assert (ctx["justification"], ctx["group_ref"], ctx["requested_by_email"]) == (JUSTIFICATION, "grp-1", REQUESTER)
    assert ctx[rq.PLAN_CONTEXT_KEY]["kind"] == rq.KIND_ITEMS
    assert len(facts(db, request_id, "request_submitted")) == 1
    # The grid marks the rows straight away.
    assert inflight.touched_rule_ids(db)[target.id] == [request_id]


@pytest.mark.parametrize("case, message", [
    ("no_justification", "justification"),
    ("blocked", "can't be requested"),
    ("nothing", "Nothing to request"),
])
def test_submit_access_change_refuses(db, case, message):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2, principals="g_new")
    if case == "blocked":
        proposal = plan(db, item(privileges=["MANAGE"])).proposals[0]
    elif case == "nothing":
        proposal = plan(db, item()).proposals[0]
    else:
        proposal = plan(db, item(principal="g_other")).proposals[0]
    with pytest.raises(rq.AccessRequestError, match=message):
        rq.submit_access_change(db, proposal, requester_email=REQUESTER,
                                justification="" if case == "no_justification" else JUSTIFICATION)
    assert db.query(RequestModel).count() == 0


# --- Plan step -------------------------------------------------------------------------


def test_plan_routes_to_the_type_group_and_stores_the_plan(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    target = seed(db, position=2)
    agent_request(db, "req-1", item())

    result = run_plan(db, "req-1")

    assert result["approvers"] == ["uc-approvers"] and result["approver_group"] == "uc-approvers"
    assert result["nothing_to_change"] is False and result["change_count"] == 1
    assert result["report_title"] == "UC securables access change"
    assert "Approvers: members of `uc-approvers`." in result["report_markdown"]
    plan_ctx = stored_plan(db, "req-1")
    assert plan_ctx["plane"] == "uc" and [d["rule_id"] for d in plan_ctx["diffs"]] == [target.id]
    assert inflight.touched_rule_ids(db) == {target.id: ["req-1"]}
    assert db.get(RequestModel, "req-1").title.startswith("Access change: grant USE_SCHEMA;SELECT")


def test_plan_without_a_group_falls_back_to_platform_admins(db):
    agent_request(db, "req-ws", ws_item())
    result = run_plan(db, "req-ws")
    assert result["approvers"] == [] and result["approver_group"] is None
    assert "No approver group set — Platform Admins approve." in result["report_markdown"]


def test_plan_refuses_more_than_one_type(db):
    agent_request(db, "req-mix", item(), ws_item())
    with pytest.raises(PermanentError, match="Split by type"):
        run_plan(db, "req-mix")
    assert rq.PLAN_CONTEXT_KEY not in db.get(RequestModel, "req-mix").state_context


def test_plan_refuses_a_blocked_change_with_the_findings(db):
    agent_request(db, "req-short", item(), justification="because")
    with pytest.raises(PermanentError, match="blocked") as exc:
        run_plan(db, "req-short")
    assert "20" in str(exc.value)


def test_plan_refuses_invalid_items(db):
    db.add(RequestModel(id="req-bad", type=rq.WORKFLOW_ID, title="t", status="pending",
                        state_context={"items": [{"securable_type": "schema"}]}, requester_email=REQUESTER))
    db.commit()
    with pytest.raises(PermanentError, match="aren't valid"):
        run_plan(db, "req-bad")
    with pytest.raises(PermanentError, match="no access changes"):
        rq.plan_step("req-bad", items=[])


def test_replanning_ignores_the_requests_own_rows_but_warns_about_others(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new;g_two", privileges="USE_CATALOG")
    target = seed(db, position=2)
    agent_request(db, "req-a", item())
    run_plan(db, "req-a")
    again = run_plan(db, "req-a")
    assert "**Warnings**" not in again["report_markdown"]

    agent_request(db, "req-b", item(principal="g_two"))
    other = run_plan(db, "req-b")
    assert "Open access request req-a will also change this row." in other["report_markdown"]
    assert set(inflight.touched_rule_ids(db)[target.id]) == {"req-a", "req-b"}


def test_plan_lists_linked_requests_from_the_same_ask(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    agent_request(db, "req-ws", ws_item(), group_ref="grp-9")
    agent_request(db, "req-uc", item(), group_ref="grp-9")
    result = run_plan(db, "req-uc")
    assert "Workspace securables: request `req-ws`" in result["report_markdown"]


def test_plan_with_nothing_to_change_auto_approves(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2, principals="g_new")
    agent_request(db, "req-noop", item())
    result = run_plan(db, "req-noop")
    assert result["nothing_to_change"] is True and result["change_count"] == 0


# --- Apply step ------------------------------------------------------------------------


def test_apply_writes_the_approved_plan_once_and_names_the_next_export(db, monkeypatch):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    target = seed(db, position=2)
    agent_request(db, "req-1", item())
    run_plan(db, "req-1")
    approve(db, "req-1")
    at = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(csv_export, "status", lambda db, limit=20: ExportStatus(runs=[], targets=[], next_run_at=at))

    result = rq.apply_step("req-1")

    assert result["applied_rows"] == 1 and not result["already_applied"]
    assert "The next scheduled CSV export is at 2026-10-16 15:00 UTC." in result["message"]
    rule = db.get(RbacRuleModel, target.id)
    assert (rule.principal_name, rule.updated_by) == ("g_analysts;g_new", APPROVER)
    cs = db.get(RbacChangeSetModel, result["change_set_id"])
    assert (cs.source, cs.source_ref) == ("request", "req-1")

    again = rq.apply_step("req-1")
    assert again["already_applied"] and again["change_set_id"] == result["change_set_id"]
    assert db.query(RbacChangeSetModel).count() == 1


def test_apply_reports_items_already_in_place(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new;g_two", privileges="USE_CATALOG")
    target = seed(db, position=2)
    agent_request(db, "req-2", item(), item(principal="g_two"))
    run_plan(db, "req-2")
    approve(db, "req-2")
    ledger.commit_change_set(db, ChangeSetIn(changes=[RowChange(
        op="update", rule_id=target.id, version=1, cells={"principal_name": "g_analysts;g_new"})], reason=REASON),
        actor=ADMIN)

    result = rq.apply_step("req-2")
    assert [(s["index"], s["reason"]) for s in result["skipped"]] == [(0, "already in place")]
    assert ("Skipped: grant USE_SCHEMA;SELECT on schema sales_dev.orders to g_new (already in place)."
            in result["message"])


def test_apply_error_mapping(db, monkeypatch):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2)
    agent_request(db, "req-3", item())
    run_plan(db, "req-3")
    approve(db, "req-3")

    def raising(exc):
        def _apply(*args, **kwargs):
            raise exc
        return _apply

    monkeypatch.setattr(ledger, "apply_access_change", raising(ledger.VersionConflict("r-1", None)))
    with pytest.raises(RetryableError):
        rq.apply_step("req-3")
    monkeypatch.setattr(ledger, "apply_access_change", raising(ledger.LedgerError("no longer applies")))
    with pytest.raises(PermanentError, match="no longer applies"):
        rq.apply_step("req-3")


def test_apply_rechecks_policy(db, monkeypatch):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2)
    agent_request(db, "req-4", item())
    run_plan(db, "req-4")
    approve(db, "req-4")
    monkeypatch.setattr(settings, "RBAC_HARD_DENIED_PRIVILEGES", ["SELECT"])
    with pytest.raises(PermanentError, match="now blocks"):
        rq.apply_step("req-4")


def test_apply_refuses_a_plan_changed_after_planning(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2)
    agent_request(db, "req-edited", item())
    run_plan(db, "req-edited")
    approve(db, "req-edited")
    request = db.get(RequestModel, "req-edited")
    ctx = dict(request.state_context)
    ctx[rq.PLAN_CONTEXT_KEY] = {**ctx[rq.PLAN_CONTEXT_KEY], "items": [item(principal="g_other").model_dump(mode="json")]}
    request.state_context = ctx
    db.commit()

    with pytest.raises(PermanentError, match="changed after it was planned"):
        rq.apply_step("req-edited")
    assert db.query(RbacChangeSetModel).count() == 0


def test_two_person_environment_needs_an_approver_other_than_the_requester(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2)
    agent_request(db, "req-self", item())
    run_plan(db, "req-self")
    approve(db, "req-self", by=REQUESTER.upper())

    with pytest.raises(PermanentError, match="two-person rule"):
        rq.apply_step("req-self")
    assert db.query(RbacChangeSetModel).count() == 0


def test_the_requester_is_the_request_row_not_a_context_value(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2)
    agent_request(db, "req-forged", item())
    request = db.get(RequestModel, "req-forged")
    request.state_context = {**request.state_context, "requested_by_email": APPROVER}
    db.commit()
    run_plan(db, "req-forged")
    approve(db, "req-forged")

    assert rq._requester(db.get(RequestModel, "req-forged")) == REQUESTER
    assert rq.apply_step("req-forged")["applied_rows"] == 1


def test_apply_without_a_stored_plan_fails(db):
    agent_request(db, "req-5", item())
    with pytest.raises(PermanentError, match="no stored plan"):
        rq.apply_step("req-5")
    with pytest.raises(PermanentError, match="doesn't exist"):
        rq.apply_step("req-missing")


# --- Two-person rule: direct edits filed as requests -----------------------------------


def direct_edit(db, target, *, privileges="USE_SCHEMA;SELECT;MODIFY"):
    return ChangeSetIn(changes=[RowChange(op="update", rule_id=target.id, version=target.version,
                                          cells={"privilege": privileges})], reason=REASON)


def test_direct_edit_is_filed_planned_and_applied_after_a_second_approval(db):
    target = seed(db, position=1)
    change = direct_edit(db, target)

    request_id = rq.submit_row_changes(db, change, plane="uc", requester_email=ADMIN)

    request = db.get(RequestModel, request_id)
    assert request.requester_email == ADMIN and request.state_context["plane"] == "uc"
    assert request.title == "Access Rules edit: 1 UC securables row (second approval)"
    [filed] = facts(db, request_id, rq.DIRECT_EDIT_FACT)
    assert filed.event_data["editor"] == ADMIN and filed.event_data["plane"] == "uc"
    assert inflight.touched_rule_ids(db) == {target.id: [request_id]}

    result = rq.plan_step(request_id)
    assert result["approvers"] == ["uc-approvers"] and result["change_count"] == 1
    assert "Edited by: `admin@example.com`" in result["report_markdown"]
    plan_ctx = stored_plan(db, request_id)
    assert plan_ctx["kind"] == rq.KIND_CHANGES and plan_ctx["diffs"][0]["rule_id"] == target.id
    assert "Warnings" not in result["report_markdown"]

    approve(db, request_id, by=ADMIN)
    with pytest.raises(PermanentError, match="someone other than the editor"):
        rq.apply_step(request_id)

    approve(db, request_id)
    applied = rq.apply_step(request_id)
    db.expire_all()
    rule = db.get(RbacRuleModel, target.id)
    assert (rule.privilege, rule.updated_by) == ("USE_SCHEMA;SELECT;MODIFY", ADMIN)
    cs = db.get(RbacChangeSetModel, applied["change_set_id"])
    assert (cs.source, cs.source_ref, cs.reason) == ("request", request_id, REASON)
    assert rq.apply_step(request_id)["already_applied"]
    assert db.query(RbacChangeSetModel).count() == 1


def test_direct_edit_conflict_fails_with_a_re_edit_message(db):
    target = seed(db, position=1)
    request_id = rq.submit_row_changes(db, direct_edit(db, target), plane="uc", requester_email=ADMIN)
    rq.plan_step(request_id)
    approve(db, request_id)
    ledger.commit_change_set(db, ChangeSetIn(changes=[RowChange(
        op="update", rule_id=target.id, version=1, cells={"principal_name": "g_analysts;g_x"})], reason=REASON),
        actor="other@example.com")

    with pytest.raises(PermanentError, match="Re-edit the rows"):
        rq.apply_step(request_id)


def test_changes_submitted_any_other_way_are_refused(db):
    target = seed(db, position=1)
    change = direct_edit(db, target)
    db.add(RequestModel(id="req-forged", type=rq.WORKFLOW_ID, title="Agent Request: x", status="pending",
                        state_context={"changes": change.model_dump(mode="json"), "plane": "uc",
                                       "requested_by_email": REQUESTER}, requester_email=REQUESTER))
    db.commit()
    with pytest.raises(RetryableError, match="no filing record"):
        rq.plan_step("req-forged")

    add_fact(db, "req-forged", rq.DIRECT_EDIT_FACT, {"plane": "uc", "digest": "nope"}, actor=REQUESTER)
    with pytest.raises(PermanentError, match="no longer matches"):
        rq.plan_step("req-forged")


def test_submit_row_changes_refuses_more_than_one_type(db):
    uc = seed(db, position=1)
    ws = seed(db, WS_DEV, position=1, kind="workspace", catalog="", schema="", workspace="ws-dev",
              principals="g_ws", privileges="USER")
    change = ChangeSetIn(changes=[
        RowChange(op="update", rule_id=uc.id, version=1, cells={"privilege": "USE_SCHEMA"}),
        RowChange(op="delete", rule_id=ws.id, version=1),
    ], reason=REASON)
    with pytest.raises(rq.AccessRequestError, match="Split by type"):
        rq.submit_row_changes(db, change, plane="uc", requester_email=ADMIN)
    assert db.query(RequestModel).count() == 0


# --- Two-person rule: reverts filed as requests ---------------------------------------

REVERT_REASON = "MODIFY was granted by mistake"


def committed(db, target):
    """An earlier edit (granting MODIFY) to revert; commit_change_set itself ignores the two-person rule."""
    return ledger.commit_change_set(db, direct_edit(db, target), actor="editor@example.com").change_set_id


def test_revert_is_filed_planned_and_applied_after_a_second_approval(db):
    target = seed(db, position=1)
    cs_id = committed(db, target)

    request_id = rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)

    request = db.get(RequestModel, request_id)
    assert request.requester_email == ADMIN
    assert (request.state_context["revert_of"], request.state_context["plane"]) == (cs_id, "uc")
    assert request.title == f"Access Rules revert: 1 UC securables row from change set {cs_id} (second approval)"
    [filed] = facts(db, request_id, rq.DIRECT_EDIT_FACT)
    assert {k: filed.event_data[k] for k in ("kind", "plane", "editor", "digest", "rows")} == {
        "kind": "revert", "plane": "uc", "editor": ADMIN, "digest": cs_id, "rows": 1}
    assert inflight.touched_rule_ids(db) == {target.id: [request_id]}
    db.expire_all()
    assert db.get(RbacRuleModel, target.id).privilege == "USE_SCHEMA;SELECT;MODIFY"
    assert db.get(RbacChangeSetModel, cs_id).reverted_by is None

    result = rq.plan_step(request_id)
    assert result["approvers"] == ["uc-approvers"] and result["nothing_to_change"] is False
    md = result["report_markdown"]
    assert f"Reverts change set `{cs_id}`, made by `editor@example.com`" in md
    assert f"Original reason: {REASON}" in md and f"Reason for reverting: {REVERT_REASON}" in md
    assert "Requested by: `admin@example.com`" in md and "Warnings" not in md
    plan_ctx = stored_plan(db, request_id)
    assert (plan_ctx["kind"], plan_ctx["revert_of"]) == (rq.KIND_REVERT, cs_id)
    assert plan_ctx["diffs"][0]["rule_id"] == target.id

    approve(db, request_id, by=ADMIN)
    with pytest.raises(PermanentError, match="someone other than the requester"):
        rq.apply_step(request_id)

    approve(db, request_id)
    applied = rq.apply_step(request_id)
    db.expire_all()
    assert db.get(RbacRuleModel, target.id).privilege == "USE_SCHEMA;SELECT"
    assert db.get(RbacChangeSetModel, cs_id).reverted_by == applied["change_set_id"]
    revert = db.get(RbacChangeSetModel, applied["change_set_id"])
    assert (revert.source, revert.source_ref, revert.actor) == ("revert", cs_id, ADMIN)
    assert revert.reason == f"{REVERT_REASON} (request {request_id})"
    assert applied["message"].startswith(f"Reverted change set {cs_id}: updated 1 ledger row(s)")

    again = rq.apply_step(request_id)
    assert again["already_applied"] and again["change_set_id"] == applied["change_set_id"]
    assert db.query(RbacChangeSetModel).count() == 2


def test_a_revert_across_types_goes_to_platform_admins(db):
    uc = seed(db, position=1)
    ws = seed(db, WS_DEV, position=1, kind="workspace", catalog="", schema="", workspace="ws-dev",
              principals="g_ws", privileges="USER")
    cs_id = ledger.commit_change_set(db, ChangeSetIn(changes=[
        RowChange(op="update", rule_id=uc.id, version=1, cells={"privilege": "USE_SCHEMA"}),
        RowChange(op="delete", rule_id=ws.id, version=1),
    ], reason=REASON), actor="editor@example.com").change_set_id

    with pytest.raises(rq.AccessRequestError, match="don't match"):
        rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)
    request_id = rq.submit_revert(db, cs_id, plane=None, requester_email=ADMIN, reason=REVERT_REASON)

    result = rq.plan_step(request_id)
    assert result["approvers"] == [] and result["approver_group"] is None
    assert "Approvers: Platform Admins, because this revert changes" in result["report_markdown"]
    assert stored_plan(db, request_id)["planes"] == ["uc", "workspace"]


def test_revert_conflicts_fail_with_a_revert_again_message(db):
    target = seed(db, position=1)
    cs_id = committed(db, target)
    later = ChangeSetIn(changes=[RowChange(op="update", rule_id=target.id, version=2,
                                           cells={"principal_name": "g_analysts;g_x"})], reason=REASON)

    approved_id = rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)
    rq.plan_step(approved_id)
    approve(db, approved_id)
    ledger.commit_change_set(db, later, actor="other@example.com")
    with pytest.raises(PermanentError, match="changed after the revert was approved.*revert it again"):
        rq.apply_step(approved_id)

    db.get(RequestModel, approved_id).status = "failed"
    db.commit()
    target2 = seed(db, position=2, schema="payments")
    cs2 = committed(db, target2)
    filed_id = rq.submit_revert(db, cs2, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)
    ledger.commit_change_set(db, ChangeSetIn(changes=[RowChange(
        op="update", rule_id=target2.id, version=2, cells={"owner": "new_owner"})], reason=REASON),
        actor="other@example.com")
    with pytest.raises(PermanentError, match="changed after the revert was filed.*revert it again"):
        rq.plan_step(filed_id)


def test_revert_refusals(db):
    target = seed(db, position=1)
    cs_id = committed(db, target)

    with pytest.raises(ledger.ChangeBlocked):
        rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason="oops")
    request_id = rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)
    with pytest.raises(rq.AccessRequestError, match=f"Request {request_id} to revert this change set is already open"):
        rq.submit_revert(db, cs_id, plane="uc", requester_email=ADMIN, reason=REVERT_REASON)

    rq.plan_step(request_id)
    approve(db, request_id)
    ledger.revert_change_set(db, cs_id, actor="other@example.com", reason="Reverted by hand instead")
    with pytest.raises(PermanentError, match="already reverted outside this request"):
        rq.apply_step(request_id)


def test_reverts_submitted_any_other_way_are_refused(db):
    target = seed(db, position=1)
    cs_id = committed(db, target)
    db.add(RequestModel(id="req-forged", type=rq.WORKFLOW_ID, title="Agent Request: x", status="pending",
                        state_context={"revert_of": cs_id, "plane": "uc", "justification": REVERT_REASON,
                                       "requested_by_email": REQUESTER}, requester_email=REQUESTER))
    db.commit()
    with pytest.raises(RetryableError, match="revert has no filing record"):
        rq.plan_step("req-forged")

    # A direct edit's filing record doesn't stand in for a revert's.
    add_fact(db, "req-forged", rq.DIRECT_EDIT_FACT, {"plane": "uc", "digest": cs_id}, actor=REQUESTER)
    with pytest.raises(PermanentError, match="no longer matches what was filed"):
        rq.plan_step("req-forged")
    db.expire_all()
    assert db.get(RbacChangeSetModel, cs_id).reverted_by is None


# --- Chat preview ----------------------------------------------------------------------


def test_preview_splits_a_mixed_ask_and_says_how_to_file_it(db):
    preview = rq.preview_access_change(db, AccessChangeIn(items=[ws_item(), item()], justification=JUSTIFICATION),
                                       requester=REQUESTER)
    assert preview["group_ref"]
    ws, uc = preview["proposals"]
    assert (ws["plane"], ws["approver_group"], ws["approver_fallback"]) == ("workspace", None, True)
    assert uc["approver_group"] == "uc-approvers"
    assert "No approver group set — Platform Admins approve." in ws["report_markdown"]
    assert "UC securables: filed alongside this one" in ws["report_markdown"]
    # Prerequisites are re-added when the request is planned, so they aren't submitted.
    assert [i["securable_type"] for i in uc["submit_items"]] == ["schema"]
    assert {p["submit"] for p in preview["proposals"]} == {True}
    assert "diffs" not in uc
    assert preview["group_ref"] in preview["next_step"] and "2 in total" in preview["next_step"]


def test_preview_without_a_justification_asks_for_one(db):
    preview = rq.preview_access_change(db, AccessChangeIn(items=[item()]), requester=REQUESTER)
    [uc] = preview["proposals"]
    assert uc["needs_justification"] and not uc["blocked_by_policy"]
    assert "never write it for them" in preview["next_step"]


def test_preview_blocked_by_policy_says_not_to_submit(db):
    preview = rq.preview_access_change(
        db, AccessChangeIn(items=[item(privileges=["MANAGE"])], justification=JUSTIFICATION), requester=REQUESTER)
    assert preview["proposals"][0]["blocked_by_policy"]
    assert preview["next_step"].startswith("Don't submit.")


# --- Export note -----------------------------------------------------------------------


def test_export_note_variants(db, monkeypatch):
    at = datetime(2026, 10, 16, 15, 0)
    monkeypatch.setattr(csv_export, "status", lambda db, limit=20: ExportStatus(runs=[], targets=[], next_run_at=at))
    assert rq.export_note(db) == "The next scheduled CSV export is at 2026-10-16 15:00 UTC."
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)
    assert rq.export_note(db).startswith("The CSV export runs shortly; the next scheduled run is at")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", False)
    monkeypatch.setattr(csv_export, "status", lambda db, limit=20: ExportStatus(runs=[], targets=[]))
    assert rq.export_note(db).startswith("No CSV export is scheduled")

    def broken(db, limit=20):
        raise RuntimeError("boom")

    monkeypatch.setattr(csv_export, "status", broken)
    assert rq.export_note(db) == ""
