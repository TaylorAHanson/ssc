"""Tests for the Access Rules ledger: reads, direct edits, history / revert,
access-change plan / apply and find-access (ADR 0005 D1, D4, D7-D10)."""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.core.default_config import DEFAULT_CONFIG
from app.db import RequestModel
from app.db.base import Base
from app.db.rbac import RbacChangeSetModel, RbacRuleEventModel, RbacRuleModel, RbacSheetModel
from app.models.rbac import (
    CANONICAL_FIELDS,
    AccessChangeIn,
    AccessItem,
    AccessProposal,
    ChangeSetIn,
    RowChange,
)
from app.services.rbac import approvers, audit, csv_export, layout, ledger

UC_DEV = "UC_Securables_RBAC_Dev"
UC_PRD = "UC_Securables_RBAC_Prd"
WS_DEV = "WS_Securables_RBAC_Dev"
WS_PRD = "WS_Securables_RBAC_Prd"
META = "UC_metastore_RBAC"
OWNER = "metastore_admins"
ADMIN = "admin@example.com"
REASON = "Quarterly access review clean-up"
JUSTIFICATION = "Needed to build the quarterly revenue dashboard for finance"


@pytest.fixture()
def db():
    """A private in-memory database per test.

    The ledger commits and rolls back its own transactions. Inside the shared
    ``db_session`` fixture a rollback also discards the seeded rows and ends the
    outer test transaction, so these tests need real commit / rollback semantics.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "aliases": "development", "sheet_suffix": "Dev", "self_service": True,
         "write_privileges": True, "service_principals": True, "two_person_rule": False},
        {"key": "prd", "aliases": "prod,production", "sheet_suffix": "Prd", "self_service": True,
         "write_privileges": False, "service_principals": False, "two_person_rule": False},
    ])
    monkeypatch.setattr(settings, "RBAC_UC_SHEET_PATTERN", "UC_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_WS_SHEET_PATTERN", "WS_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_METASTORE_SHEET", META)
    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 200)
    monkeypatch.setattr(settings, "RBAC_MIN_REASON_LENGTH", 10)
    monkeypatch.setattr(settings, "RBAC_MIN_JUSTIFICATION_LENGTH", 20)
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES",
                        copy.deepcopy(DEFAULT_CONFIG["rbac"]["allowed_privileges"]))
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["account users", "users", "admins"])
    monkeypatch.setattr(settings, "RBAC_NAMING_RULES", [])
    monkeypatch.setattr(settings, "RBAC_DEFAULT_OWNER", "")


def _without(kind: str, privilege: str) -> list:
    """The default allow list with ``privilege`` taken off ``kind``'s row."""
    rows = copy.deepcopy(DEFAULT_CONFIG["rbac"]["allowed_privileges"])
    for r in rows:
        if r["securable_type"] == kind:
            r["privileges"] = ", ".join(p.strip() for p in r["privileges"].split(",") if p.strip() != privilege)
    return rows


@pytest.fixture(autouse=True)
def exports(monkeypatch):
    """Approver groups (UC only) and a record of every export the ledger asks for."""
    calls = []
    monkeypatch.setattr(approvers, "approver_group", lambda plane: {"uc": "uc-approvers"}.get(plane))
    monkeypatch.setattr(csv_export, "request_export_soon", lambda: calls.append(1))
    return calls


def seed(db, sheet=UC_DEV, *, position, principals="g_analysts", privileges="USE_SCHEMA;SELECT",
         kind="schema", catalog="sales_dev", schema="orders", obj="", workspace="", owner=OWNER,
         ptype="group", env_value=None, status="active"):
    spec = layout.sheet_spec(sheet)
    cells = dict(
        environment_value=env_value if env_value is not None else (spec.environment or ""),
        securable_type=kind, catalog=catalog, schema_name=schema, object_name=obj, workspace_name=workspace,
        owner=owner, principal_type=ptype, principal_name=principals, privilege=privileges,
    )
    rule = RbacRuleModel(
        id=audit.new_id(), sheet=sheet, plane=spec.plane,
        environment=spec.environment or layout.resolve_environment(cells["environment_value"]),
        position=position, extra={}, status=status, version=1,
        row_hash=audit.compute_row_hash(sheet, cells, {}), source="import", **cells,
    )
    db.add(rule)
    db.commit()
    return rule


def seed_ws(db, *, position, principals="g_ws", privileges="USER", workspace="ws-dev"):
    return seed(db, WS_DEV, position=position, kind="workspace", catalog="", schema="", workspace=workspace,
                owner="", principals=principals, privileges=privileges)


def new_cells(**kw):
    cells = dict(environment_value="dev", securable_type="schema", catalog="sales_dev", schema_name="payments",
                 owner=OWNER, principal_type="group", principal_name="g_new", privilege="USE_SCHEMA;SELECT")
    cells.update(kw)
    return cells


def change(*changes, reason=REASON):
    return ChangeSetIn(changes=list(changes), reason=reason)


def commit(db, *changes, reason=REASON, actor=ADMIN):
    return ledger.commit_change_set(db, change(*changes, reason=reason), actor=actor)


def codes(findings):
    return {f.code for f in findings}


def item(principal="g_new", privileges=("SELECT",), **kw):
    fields = dict(environment="dev", securable_type="schema", catalog="sales_dev", schema_name="orders")
    fields.update(kw)
    return AccessItem(principal_name=principal, privileges=list(privileges), **fields)


def prepare(db, *items, justification=JUSTIFICATION):
    return ledger.prepare_access_change(db, AccessChangeIn(items=list(items), justification=justification),
                                        requester="requester@example.com")


def stored(proposal):
    """The proposal as the workflow stores it in request context and hands it to the record step."""
    return AccessProposal.model_validate(proposal.model_dump(mode="json"))


def open_request(db, request_id, *rule_ids, status="pending_approval"):
    db.add(RequestModel(id=request_id, type="rbac_access_change", status=status,
                        state_context={"prepared_change": {"diffs": [{"rule_id": r} for r in rule_ids]}}))
    db.commit()


# --- Reads ---------------------------------------------------------------------


def test_list_rules_filters_searches_and_pages(db):
    a = seed(db, position=1)
    b = seed(db, position=2, schema="returns", principals="g_finance")
    c = seed(db, position=3, schema="refunds_100%", principals="g_ops")
    seed(db, position=4, schema="archive", status="deleted")
    seed_ws(db, position=1)
    open_request(db, "req-1", a.id)
    open_request(db, "req-old", b.id, status="completed")

    page = ledger.list_rules(db, sheet=UC_DEV)
    assert page.total == 3
    assert [r.id for r in page.rules] == [a.id, b.id, c.id]
    assert page.rules[0].pending_request_ids == ["req-1"]
    assert page.rules[1].pending_request_ids == []

    assert ledger.list_rules(db, plane="workspace").total == 1
    assert ledger.list_rules(db, environment="development").total == 4
    assert [r.id for r in ledger.list_rules(db, q="FINANCE").rules] == [b.id]
    # LIKE wildcards in the search text are literal.
    assert [r.id for r in ledger.list_rules(db, sheet=UC_DEV, q="%").rules] == [c.id]
    assert ledger.list_rules(db, sheet=UC_DEV, include_deleted=True).total == 4

    second = ledger.list_rules(db, sheet=UC_DEV, offset=1, limit=1)
    assert second.total == 3
    assert [r.id for r in second.rules] == [b.id]


def test_get_rule_sheet_columns_and_sheet_infos(db):
    rule = seed(db, position=1)
    seed_ws(db, position=1)
    assert ledger.get_rule(db, rule.id).principal_name == "g_analysts"
    assert ledger.get_rule(db, "missing") is None

    db.add(RbacSheetModel(name=UC_DEV, plane="uc", environment="dev", columns=[
        {"header": "Env", "field": "environment_value"}, {"header": "Notes", "field": None},
    ]))
    db.commit()
    assert [(c.header, c.field) for c in ledger.sheet_columns(db, UC_DEV)] == [
        ("Env", "environment_value"), ("Notes", None),
    ]
    assert [c.header for c in ledger.sheet_columns(db, WS_DEV)] == [h for h, _ in layout.DEFAULT_COLUMNS["workspace"]]
    with pytest.raises(ledger.LedgerError):
        ledger.sheet_columns(db, "Sheet1")

    infos = {i.name: i for i in ledger.sheet_infos(db, {"uc"})}
    assert list(infos) == [UC_DEV, UC_PRD, WS_DEV, WS_PRD, META]
    assert infos[UC_DEV].row_count == 1 and infos[UC_DEV].editable
    assert infos[WS_DEV].row_count == 1 and not infos[WS_DEV].editable
    assert infos[UC_DEV].columns[0].header == "Env"
    assert infos[META].export_target == "metastore" and infos[META].environment is None


def test_planes_in_change(db):
    ws = seed_ws(db, position=1)
    planes = ledger.planes_in_change(db, change(
        RowChange(op="create", sheet=META, cells={"securable_type": "connection", "catalog": "conn_dev"}),
        RowChange(op="delete", rule_id=ws.id, version=1),
    ))
    assert planes == {"uc_metastore", "workspace"}
    with pytest.raises(ledger.LedgerError):
        ledger.planes_in_change(db, change(RowChange(op="create", sheet="Sheet1")))
    with pytest.raises(ledger.LedgerError):
        ledger.planes_in_change(db, change(RowChange(op="update", rule_id="nope", version=1)))


# --- Direct edits ----------------------------------------------------------------


def test_preview_shows_diffs_policy_and_inflight_findings(db):
    untouched = seed(db, position=1)
    row = seed(db, position=2, schema="returns")
    open_request(db, "req-1", row.id)

    preview = ledger.preview_change_set(db, change(
        # Sets a cell to the value it already has: no diff.
        RowChange(op="update", rule_id=untouched.id, version=1, cells={"owner": OWNER}),
        RowChange(op="update", rule_id=row.id, version=1,
                  cells={"principal_name": "g_analysts;g_new", "privilege": "USE_SCHEMA;SELECT;MANAGE"}),
    ), actor=ADMIN)

    [diff] = preview.diffs
    assert diff.op == "update" and diff.rule_id == row.id
    assert diff.before["principal_name"] == "g_analysts" and diff.before["version"] == 1
    assert diff.after["principal_name"] == "g_analysts;g_new" and diff.after["version"] == 2
    assert preview.blocked
    denied = [f for f in preview.findings if f.code == "privilege_not_allowed"]
    assert denied and all(f.severity == "deny" and f.change_index == 1 and f.rule_id == row.id for f in denied)
    [overlap] = [f for f in preview.findings if f.code == "inflight_overlap"]
    assert overlap.severity == "warn" and "req-1" in overlap.message and overlap.change_index == 1
    assert not preview.two_person_required
    # A dry run writes nothing.
    assert db.query(RbacChangeSetModel).count() == 0
    assert ledger.get_rule(db, row.id).version == 1


@pytest.mark.parametrize("make", [
    lambda r, d: RowChange(op="update", rule_id=r.id, version=1, cells={"colour": "red"}),
    lambda r, d: RowChange(op="update", rule_id=r.id, version=1, extra={"notes": "x"}),
    lambda r, d: RowChange(op="update", rule_id=r.id, cells={"owner": "x"}),
    lambda r, d: RowChange(op="create", cells=new_cells()),
    lambda r, d: RowChange(op="create", sheet="Sheet1", cells=new_cells()),
    lambda r, d: RowChange(op="delete", rule_id=d.id, version=1),
    lambda r, d: RowChange(op="update", rule_id=d.id, version=1, cells={"owner": "x"}),
    lambda r, d: RowChange(op="delete", rule_id="missing", version=1),
], ids=["unknown-cell", "extra-not-a-column-number", "no-version", "create-without-sheet",
        "unmanaged-sheet", "delete-deleted", "edit-deleted", "unknown-rule"])
def test_preview_rejects_invalid_changes(db, make):
    row = seed(db, position=1)
    deleted = seed(db, position=2, schema="archive", status="deleted")
    with pytest.raises(ledger.LedgerError) as exc:
        ledger.preview_change_set(db, change(make(row, deleted)), actor=ADMIN)
    assert not isinstance(exc.value, ledger.VersionConflict)


def test_the_same_row_twice_in_one_set_is_refused(db):
    row = seed(db, position=1)
    with pytest.raises(ledger.LedgerError, match="more than once"):
        ledger.preview_change_set(db, change(
            RowChange(op="update", rule_id=row.id, version=1, cells={"owner": "x"}),
            RowChange(op="delete", rule_id=row.id, version=1),
        ), actor=ADMIN)


def test_commit_creates_updates_and_deletes_in_one_change_set(db, exports):
    keep = seed(db, position=1)
    drop = seed(db, position=2, schema="returns")

    result = commit(
        db,
        RowChange(op="create", sheet=UC_DEV, cells=new_cells(), extra={"9": "note"}),
        RowChange(op="update", rule_id=keep.id, version=1, cells={"principal_name": "g_analysts;g_new"}),
        RowChange(op="delete", rule_id=drop.id, version=1),
    )

    assert result.change_set_id
    assert [d.op for d in result.diffs] == ["create", "update", "delete"]
    created = ledger.get_rule(db, result.diffs[0].rule_id)
    assert (created.position, created.version, created.source, created.environment) == (3, 1, "admin_edit", "dev")
    assert created.created_by == ADMIN and created.extra == {"9": "note"}

    updated = ledger.get_rule(db, keep.id)
    assert updated.version == 2 and updated.principal_name == "g_analysts;g_new" and updated.updated_by == ADMIN
    model = db.get(RbacRuleModel, keep.id)
    assert model.row_hash == audit.compute_row_hash(UC_DEV, {f: getattr(model, f) for f in CANONICAL_FIELDS}, {})

    deleted = ledger.get_rule(db, drop.id)
    assert deleted.status == "deleted" and deleted.version == 2
    assert result.diffs[2].after is None
    assert ledger.list_rules(db, sheet=UC_DEV).total == 2

    cs = db.get(RbacChangeSetModel, result.change_set_id)
    assert (cs.source, cs.actor, cs.reason) == ("admin_edit", ADMIN, REASON)
    assert {k: cs.summary[k] for k in ("created", "updated", "deleted", "sheets", "planes")} == {
        "created": 1, "updated": 1, "deleted": 1, "sheets": [UC_DEV], "planes": ["uc"],
    }
    events = (db.query(RbacRuleEventModel).filter_by(change_set_id=cs.id)
              .order_by(RbacRuleEventModel.id).all())
    assert [e.action for e in events] == ["create", "update", "delete"]
    assert events[1].before["version"] == 1 and events[1].after["version"] == 2
    assert events[2].after["status"] == "deleted"
    assert all(e.actor == ADMIN and e.reason == REASON for e in events)
    assert exports == [1]


def test_stale_version_is_a_conflict_and_writes_nothing(db, exports):
    row = seed(db, position=1)
    other = seed(db, position=2, schema="returns")
    with pytest.raises(ledger.VersionConflict) as exc:
        commit(db,
               RowChange(op="delete", rule_id=other.id, version=1),
               RowChange(op="update", rule_id=row.id, version=7, cells={"owner": "someone_else"}))
    assert exc.value.rule_id == row.id
    assert exc.value.current.version == 1 and exc.value.current.owner == OWNER
    assert db.query(RbacChangeSetModel).count() == 0
    assert db.query(RbacRuleEventModel).count() == 0
    assert ledger.get_rule(db, other.id).status == "active"
    assert exports == []


def test_a_row_changing_between_preview_and_write_rolls_back_the_whole_set(db, monkeypatch):
    first = seed(db, position=1)
    second = seed(db, position=2, schema="returns")
    real_preview = ledger._preview

    def preview_then_race(session, ops, *, reason):
        result = real_preview(session, ops, reason=reason)
        session.query(RbacRuleModel).filter_by(id=second.id).update({"version": 5}, synchronize_session=False)
        return result

    monkeypatch.setattr(ledger, "_preview", preview_then_race)
    with pytest.raises(ledger.VersionConflict) as exc:
        commit(db,
               RowChange(op="update", rule_id=first.id, version=1, cells={"principal_name": "g_other"}),
               RowChange(op="delete", rule_id=second.id, version=1))
    assert exc.value.rule_id == second.id and exc.value.current.version == 5
    assert ledger.get_rule(db, first.id).principal_name == "g_analysts"
    assert ledger.get_rule(db, first.id).version == 1
    assert db.query(RbacChangeSetModel).count() == 0


def test_change_sets_over_the_limit_are_refused(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 2)
    creates = [RowChange(op="create", sheet=UC_DEV, cells=new_cells(schema_name=f"s{i}")) for i in range(3)]
    with pytest.raises(ledger.LedgerError, match="at most 2"):
        ledger.preview_change_set(db, change(*creates), actor=ADMIN)
    with pytest.raises(ledger.LedgerError, match="at most 2"):
        commit(db, *creates)
    assert db.query(RbacRuleModel).count() == 0


def test_a_short_reason_blocks_the_commit(db, exports):
    row = seed(db, position=1)
    with pytest.raises(ledger.ChangeBlocked) as exc:
        commit(db, RowChange(op="delete", rule_id=row.id, version=1), reason="tidy")
    assert "reason_too_short" in codes(exc.value.preview.findings)
    assert exc.value.preview.blocked
    assert ledger.get_rule(db, row.id).status == "active"
    assert db.query(RbacChangeSetModel).count() == 0
    assert exports == []


def test_duplicate_rows_are_flagged_but_allowed(db):
    seed(db, position=1)
    preview = ledger.preview_change_set(db, change(
        RowChange(op="create", sheet=UC_DEV, cells=new_cells(schema_name="orders", principal_name="g_analysts")),
        RowChange(op="create", sheet=UC_DEV, cells=new_cells()),
        RowChange(op="create", sheet=UC_DEV, cells=new_cells()),
        RowChange(op="create", sheet=UC_DEV, cells=new_cells(schema_name="unique")),
    ), actor=ADMIN)
    dupes = [f for f in preview.findings if f.code == "duplicate_row"]
    assert sorted(f.change_index for f in dupes) == [0, 1, 2]
    assert all(f.severity == "warn" for f in dupes)
    assert not preview.blocked


def test_two_person_rule_flags_edits_in_that_environment(db, monkeypatch):
    envs = [dict(e) for e in settings.RBAC_ENVIRONMENTS]
    envs[1]["two_person_rule"] = True
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", envs)
    dev = seed(db, position=1)
    prd = seed(db, UC_PRD, position=1, catalog="sales_prd")

    def flagged(*changes):
        return ledger.preview_change_set(db, change(*changes), actor=ADMIN).two_person_required

    assert not flagged(RowChange(op="update", rule_id=dev.id, version=1, cells={"principal_name": "g_x"}))
    assert flagged(RowChange(op="delete", rule_id=prd.id, version=1))
    assert flagged(RowChange(op="create", sheet=UC_PRD, cells=new_cells(environment_value="prd")))


# --- History and revert ------------------------------------------------------------


def test_history_and_change_set_detail(db):
    row = seed(db, position=1)
    first = commit(db, RowChange(op="update", rule_id=row.id, version=1, cells={"principal_name": "g_x"}))
    second = commit(db, RowChange(op="delete", rule_id=row.id, version=2))

    page = ledger.history(db)
    assert page.total == 2
    assert [c.id for c in page.change_sets] == [second.change_set_id, first.change_set_id]
    assert [c.id for c in ledger.history(db, offset=1, limit=1).change_sets] == [first.change_set_id]

    detail = ledger.change_set_detail(db, second.change_set_id)
    assert detail.reason == REASON and detail.actor == ADMIN and detail.summary["deleted"] == 1
    [diff] = detail.diffs
    assert (diff.op, diff.rule_id, diff.sheet, diff.plane) == ("delete", row.id, UC_DEV, "uc")
    assert diff.before["version"] == 2 and diff.after is None
    assert ledger.change_set_detail(db, "missing") is None


def test_revert_restores_the_previous_state(db, exports):
    keep = seed(db, position=1)
    drop = seed(db, position=2, schema="returns")
    original = commit(
        db,
        RowChange(op="create", sheet=UC_DEV, cells=new_cells()),
        RowChange(op="update", rule_id=keep.id, version=1, cells={"principal_name": "g_analysts;g_new"}),
        RowChange(op="delete", rule_id=drop.id, version=1),
    )
    created_id = original.diffs[0].rule_id

    preview = ledger.revert_preview(db, original.change_set_id, actor=ADMIN)
    assert [(d.op, d.rule_id) for d in preview.diffs] == [
        ("delete", created_id), ("update", keep.id), ("restore", drop.id),
    ]
    assert not preview.blocked

    reverted = ledger.revert_change_set(db, original.change_set_id, actor="second@example.com",
                                        reason="Undo the clean-up, it was premature")
    assert ledger.get_rule(db, created_id).status == "deleted"
    restored_keep = ledger.get_rule(db, keep.id)
    assert restored_keep.principal_name == "g_analysts" and restored_keep.version == 3
    restored_drop = ledger.get_rule(db, drop.id)
    assert restored_drop.status == "active" and restored_drop.version == 3

    cs = db.get(RbacChangeSetModel, reverted.change_set_id)
    assert (cs.source, cs.source_ref, cs.actor) == ("revert", original.change_set_id, "second@example.com")
    assert (cs.summary["deleted"], cs.summary["updated"], cs.summary["restored"]) == (1, 1, 1)
    assert db.get(RbacChangeSetModel, original.change_set_id).reverted_by == reverted.change_set_id
    assert exports == [1, 1]

    with pytest.raises(ledger.LedgerError, match="already reverted"):
        ledger.revert_change_set(db, original.change_set_id, actor=ADMIN, reason="Once more, with feeling")


def test_revert_needs_a_reason(db):
    row = seed(db, position=1)
    original = commit(db, RowChange(op="delete", rule_id=row.id, version=1))
    with pytest.raises(ledger.ChangeBlocked):
        ledger.revert_change_set(db, original.change_set_id, actor=ADMIN, reason="oops")
    assert ledger.get_rule(db, row.id).status == "deleted"


def test_revert_after_a_later_change_is_a_conflict(db):
    row = seed(db, position=1)
    first = commit(db, RowChange(op="update", rule_id=row.id, version=1, cells={"principal_name": "g_x"}))
    commit(db, RowChange(op="update", rule_id=row.id, version=2, cells={"owner": "new_owner"}))

    with pytest.raises(ledger.VersionConflict) as exc:
        ledger.revert_preview(db, first.change_set_id, actor=ADMIN)
    assert exc.value.rule_id == row.id and exc.value.current.version == 3
    with pytest.raises(ledger.VersionConflict):
        ledger.revert_change_set(db, first.change_set_id, actor=ADMIN, reason="Put the old principal back")
    assert db.get(RbacChangeSetModel, first.change_set_id).reverted_by is None
    assert ledger.get_rule(db, row.id).owner == "new_owner"


def test_revert_limit_applies_except_to_imports(db, monkeypatch):
    imported = [seed(db, position=i, schema=f"imported_{i}") for i in range(1, 4)]
    import_cs = audit.new_change_set(db, source="import", source_ref="imp-1", actor=ADMIN, reason="Initial import")
    for rule in imported:
        audit.record_event(db, import_cs, rule_id=rule.id, action="create", before=None, after=audit.snapshot(rule))
    db.commit()
    edited = [seed(db, position=i, schema=f"edited_{i}") for i in range(4, 7)]
    edit = commit(db, *[RowChange(op="update", rule_id=r.id, version=1, cells={"owner": "x"}) for r in edited])

    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 2)
    with pytest.raises(ledger.LedgerError, match="at most 2"):
        ledger.revert_preview(db, edit.change_set_id, actor=ADMIN)

    result = ledger.revert_change_set(db, import_cs.id, actor=ADMIN, reason="Reset the test import")
    assert len(result.diffs) == 3
    assert all(ledger.get_rule(db, r.id).status == "deleted" for r in imported)


def test_reverting_a_reset_restores_rows_that_predate_the_policy(db):
    legacy = seed(db, META, position=1, kind="connection", catalog="conn_prd", schema="", env_value="prd",
                  ptype="service principal", principals="sp_loader_prd", privileges="MANAGE;USE_CONNECTION")
    before = audit.snapshot(legacy)
    reset = audit.new_change_set(db, source="import", source_ref="imp-2", actor=ADMIN, reason="Reset")
    legacy.status, legacy.version = "deleted", 2
    db.flush()
    audit.record_event(db, reset, rule_id=legacy.id, action="delete", before=before, after=audit.snapshot(legacy))
    db.commit()

    preview = ledger.revert_preview(db, reset.id, actor=ADMIN)
    assert [d.op for d in preview.diffs] == ["restore"] and not preview.blocked
    ledger.revert_change_set(db, reset.id, actor=ADMIN, reason="Undo the test reset")
    restored = ledger.get_rule(db, legacy.id)
    assert restored.status == "active" and restored.privilege == "MANAGE;USE_CONNECTION"


# --- Access changes: prepare ---------------------------------------------------------


def test_prepare_grant_appends_to_the_matching_packed_row(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_analysts;g_new", privileges="USE_CATALOG")
    target = seed(db, position=2, principals="g_analysts;g_finance")
    seed(db, position=3, principals="g_ops", privileges="USE_SCHEMA;SELECT;MODIFY")

    preview = prepare(db, item(environment="development", securable_type="Schema", catalog=" sales_dev ",
                            privileges=["select"]))

    assert preview.group_ref is None
    [proposal] = preview.proposals
    assert proposal.plane == "uc"
    assert proposal.approver_group == "uc-approvers" and not proposal.approver_fallback
    assert not proposal.blocked and proposal.added_prerequisites == []
    [normalized] = proposal.items
    assert (normalized.environment, normalized.securable_type, normalized.catalog) == ("dev", "schema", "sales_dev")
    assert normalized.privileges == ["USE_SCHEMA", "SELECT"]
    [diff] = proposal.diffs
    assert diff.op == "update" and diff.rule_id == target.id
    assert diff.after["principal_name"] == "g_analysts;g_finance;g_new"
    assert [(o.index, o.status) for o in proposal.outcomes] == [(0, "change")]
    assert proposal.report_markdown == ""


def test_prepare_new_rows_carry_prerequisites_owner_and_environment(db, monkeypatch):
    seed(db, position=1, principals="g_analysts", owner="sales_owners")
    seed(db, position=2, catalog="hr_dev", principals="g_hr")
    seed(db, position=3, catalog="hr_dev", schema="people", principals="g_hr")

    [proposal] = prepare(db, item(securable_type="view", object_name="fiscal_month")).proposals
    assert [(a.securable_type, a.privileges) for a in proposal.added_prerequisites] == [
        ("catalog", ["USE_CATALOG"]), ("schema", ["USE_SCHEMA"]),
    ]
    assert [i.securable_type for i in proposal.items] == ["catalog", "schema", "view"]
    assert [(o.index, o.status) for o in proposal.outcomes] == [(0, "change"), (1, "change"), (2, "change")]
    assert [d.op for d in proposal.diffs] == ["create", "create", "create"]
    assert {d.after["securable_type"]: d.after["privilege"] for d in proposal.diffs} == {
        "catalog": "USE_CATALOG", "schema": "USE_SCHEMA", "view": "SELECT",
    }
    # The owner already used on that catalog wins over the sheet's most common owner.
    assert {d.after["owner"] for d in proposal.diffs} == {"sales_owners"}
    assert all(d.environment == "dev" and d.after["environment_value"] == "dev" for d in proposal.diffs)

    [fresh] = prepare(db, item(catalog="fresh_dev", schema_name="s")).proposals
    assert {d.after["owner"] for d in fresh.diffs} == {OWNER}

    monkeypatch.setattr(settings, "RBAC_DEFAULT_OWNER", "platform_owners")
    [configured] = prepare(db, item(catalog="fresh_dev", schema_name="s")).proposals
    assert {d.after["owner"] for d in configured.diffs} == {"platform_owners"}


def test_prepare_mixed_types_returns_one_linked_proposal_per_type(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    preview = prepare(
        db,
        item(principal="sp_etl_dev", securable_type="workspace", catalog="", schema_name="",
             workspace_name="ws-dev", principal_type="sp", privileges=["user"]),
        item(),
    )
    assert preview.group_ref
    assert [p.plane for p in preview.proposals] == ["workspace", "uc"]
    ws, uc = preview.proposals
    assert ws.approver_group is None and ws.approver_fallback
    assert uc.approver_group == "uc-approvers" and not uc.approver_fallback
    [ws_diff] = ws.diffs
    assert (ws_diff.op, ws_diff.sheet, ws_diff.environment) == ("create", WS_DEV, "dev")
    assert ws_diff.after["principal_type"] == "service principal" and ws_diff.after["privilege"] == "USER"
    assert ws_diff.after["owner"] == ""
    assert uc.diffs and all(d.plane == "uc" for d in uc.diffs)
    assert [i.plane for i in ws.items] == ["workspace"]


def test_prepare_metastore_grant_without_an_environment(db):
    [proposal] = prepare(db, item(securable_type="connection", catalog="conn_dev", schema_name="", environment=None,
                               privileges=["use connection"])).proposals
    assert proposal.plane == "uc_metastore" and not proposal.blocked
    assert proposal.approver_fallback
    [diff] = proposal.diffs
    assert (diff.op, diff.sheet, diff.environment) == ("create", META, None)
    assert (diff.after["catalog"], diff.after["privilege"], diff.after["owner"]) == ("conn_dev", "USE_CONNECTION", "")


def test_prepare_blocks_a_privilege_outside_the_allow_list(db):
    [proposal] = prepare(db, item(privileges=["MANAGE"]), item(principal="g_other")).proposals
    assert proposal.blocked
    denied = [f for f in proposal.findings if f.code == "privilege_not_allowed"]
    assert denied and all(f.severity == "deny" and f.principal == "g_new" for f in denied)
    schema_outcomes = {o.item.principal_name: o for o in proposal.outcomes if o.item.securable_type == "schema"}
    assert schema_outcomes["g_new"].status == "blocked" and "MANAGE" in schema_outcomes["g_new"].reason
    assert schema_outcomes["g_other"].status == "change"
    assert all(proposal.items[o.index] == o.item for o in proposal.outcomes)


def test_prepare_spells_configured_workspaces_and_blocks_others(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [{"key": "dev", "sheet_suffix": "Dev", "workspaces": "ws-dev"}])
    ws = dict(securable_type="workspace", catalog="", schema_name="", privileges=["USER"])
    [proposal] = prepare(db, item(workspace_name="WS-DEV", object_name="Ws-Dev", **ws),
                      item(principal="g_other", workspace_name="ws-typo", object_name="ws-typo", **ws)).proposals
    outcomes = {o.item.principal_name: o for o in proposal.outcomes}
    assert outcomes["g_new"].status == "change"
    assert (outcomes["g_new"].item.workspace_name, outcomes["g_new"].item.object_name) == ("ws-dev", "ws-dev")
    assert outcomes["g_other"].status == "blocked" and "ws-typo" in outcomes["g_other"].reason
    assert {f.code for f in proposal.findings if f.severity == "deny"} == {"value_not_in_options"}


def test_prepare_spells_a_configured_catalog(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [{"key": "dev", "sheet_suffix": "Dev", "catalogs": "Sales_Dev"}])
    [proposal] = prepare(db, item(catalog="sales_dev")).proposals
    assert not proposal.blocked
    assert {i.catalog for i in proposal.items} == {"Sales_Dev"}


def test_prepare_reports_items_it_cannot_resolve(db):
    preview = prepare(
        db,
        item(environment="moon"),
        item(securable_type="spaceship", privileges=["FLY"]),
        justification="short",
    )
    [proposal] = preview.proposals
    assert proposal.plane == "uc" and proposal.blocked and proposal.diffs == []
    assert codes(proposal.findings) >= {"unknown_environment", "unknown_securable_type", "justification_too_short"}
    assert [(o.index, o.status) for o in proposal.outcomes] == [(0, "blocked"), (1, "blocked")]
    assert {f.change_index for f in proposal.findings if f.code.startswith("unknown_")} == {0, 1}
    assert "dev, prd" in proposal.outcomes[0].reason


def test_prepare_takes_the_type_of_rule_from_the_securable_type_not_the_caller(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    [proposal] = prepare(db, item(plane="uc_metastore"), justification=JUSTIFICATION).proposals
    assert proposal.plane == "uc" and proposal.blocked
    assert "plane_mismatch" in codes(proposal.findings)
    assert all(d.sheet == UC_DEV for d in proposal.diffs)


def test_prepare_short_justification_blocks_every_change(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    [proposal] = prepare(db, item(), justification="because").proposals
    assert proposal.blocked
    [finding] = [f for f in proposal.findings if f.code == "justification_too_short"]
    assert finding.severity == "deny"
    assert [o.status for o in proposal.outcomes] == ["blocked"]


# --- Access changes: record ------------------------------------------------------------


def test_record_writes_the_approved_change_once(db, exports):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = prepare(db, item()).proposals

    result = ledger.record_access_change(db, request_id="req-9", approved=stored(proposal), actor="approver@x.com")
    assert result.change_set_id and not result.already_recorded and result.skipped == []
    assert [d.rule_id for d in result.recorded] == [target.id]
    assert ledger.get_rule(db, target.id).principal_name == "g_analysts;g_new"
    cs = db.get(RbacChangeSetModel, result.change_set_id)
    assert (cs.source, cs.source_ref, cs.reason) == ("request", "req-9", "Approved access request req-9")
    assert ledger.get_rule(db, target.id).updated_by == "approver@x.com"

    again = ledger.record_access_change(db, request_id="req-9", approved=stored(proposal), actor="approver@x.com")
    assert again.already_recorded and again.change_set_id == result.change_set_id
    assert [d.rule_id for d in again.recorded] == [target.id]
    assert db.query(RbacChangeSetModel).count() == 1
    assert ledger.get_rule(db, target.id).version == 2
    assert exports == [1]


def test_record_skips_what_an_admin_already_added(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new;g_two", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = prepare(db, item(), item(principal="g_two")).proposals

    commit(db, RowChange(op="update", rule_id=target.id, version=1, cells={"principal_name": "g_analysts;g_new"}))
    result = ledger.record_access_change(db, request_id="req-1", approved=stored(proposal), actor=ADMIN)

    assert [(o.index, o.item.principal_name, o.status, o.reason) for o in result.skipped] == [
        (0, "g_new", "noop", "already in place"),
    ]
    assert ledger.get_rule(db, target.id).principal_name == "g_analysts;g_new;g_two"


def test_record_never_goes_beyond_the_approved_change(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new;g_sneaky", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = prepare(db, item()).proposals
    widened = stored(proposal).model_copy(update={"items": [
        *proposal.items, item(principal="g_sneaky", privileges=["USE_SCHEMA", "SELECT"], plane="uc"),
    ]})

    result = ledger.record_access_change(db, request_id="req-2", approved=widened, actor=ADMIN)
    assert [(o.index, o.item.principal_name, o.reason) for o in result.skipped] == [
        (1, "g_sneaky", "outside the approved change"),
    ]
    assert ledger.get_rule(db, target.id).principal_name == "g_analysts;g_new"
    assert ledger.find_access(db, principal_name="g_sneaky", securable_type="schema").matches == []


def test_record_recreates_a_row_removed_since_approval_with_the_same_atoms(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = prepare(db, item()).proposals
    commit(db, RowChange(op="delete", rule_id=target.id, version=1))

    result = ledger.record_access_change(db, request_id="req-3", approved=stored(proposal), actor=ADMIN)
    [diff] = result.recorded
    assert diff.op == "create" and result.skipped == []
    assert (diff.after["principal_name"], diff.after["privilege"]) == ("g_new", "USE_SCHEMA;SELECT")


def test_record_rechecks_policy(db, monkeypatch):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    target = seed(db, position=2)
    [proposal] = prepare(db, item()).proposals
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES", _without("schema", "SELECT"))

    with pytest.raises(ledger.ChangeBlocked) as exc:
        ledger.record_access_change(db, request_id="req-4", approved=stored(proposal), actor=ADMIN)
    assert "privilege_not_allowed" in codes(exc.value.preview.findings)
    assert ledger.get_rule(db, target.id).principal_name == "g_analysts"
    assert db.query(RbacChangeSetModel).count() == 0


# --- Find access ---------------------------------------------------------------------------


def test_find_access_on_a_securable_includes_parent_rows(db):
    cat = seed(db, position=1, kind="catalog", schema="", principals="g_readers;g_new",
               privileges="USE_CATALOG;SELECT")
    sch = seed(db, position=2, principals="g_analysts;g_new")
    view = seed(db, position=3, kind="view", obj="fiscal_month", principals="g_new", privileges="SELECT")
    seed(db, position=4, schema="returns", principals="g_other")
    seed(db, UC_PRD, position=1, kind="view", obj="fiscal_month", principals="g_new", privileges="SELECT")

    result = ledger.find_access(db, securable_type="VIEW", catalog="SALES_DEV", schema_name="orders",
                                object_name="fiscal_month", environment="development")
    assert [(m.rule_id, m.principal_name, m.via) for m in result.matches] == [
        (view.id, "g_new", "direct"),
        (cat.id, "g_readers", "parent"),
        (cat.id, "g_new", "parent"),
        (sch.id, "g_analysts", "parent"),
        (sch.id, "g_new", "parent"),
    ]
    assert result.matches[1].privileges == ["USE_CATALOG", "SELECT"]
    assert not result.truncated

    schema_only = ledger.find_access(db, catalog="sales_dev", schema_name="orders", principal_name="g_analysts",
                                     environment="dev")
    assert [(m.rule_id, m.via) for m in schema_only.matches] == [(sch.id, "direct")]


def test_find_access_for_a_principal(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new", privileges="USE_CATALOG")
    seed(db, position=2, principals="g_analysts;G_NEW")
    seed(db, position=3, principals="g_newer")
    seed(db, UC_PRD, position=1, catalog="sales_prd", principals="g_new")

    dev = ledger.find_access(db, principal_name="g_new", environment="dev")
    assert [m.principal_name for m in dev.matches] == ["g_new", "G_NEW"]
    assert all(m.environment == "dev" for m in dev.matches)

    capped = ledger.find_access(db, principal_name="g_new", limit=2)
    assert len(capped.matches) == 2 and capped.truncated

    with pytest.raises(ledger.LedgerError):
        ledger.find_access(db)
    with pytest.raises(ledger.LedgerError):
        ledger.find_access(db, principal_name="g_new", environment="moon")


def test_find_access_for_a_set_of_principals_keeps_exact_names_only(db):
    seed(db, position=1, kind="catalog", schema="", principals="g_new;g_readers", privileges="USE_CATALOG")
    seed(db, position=2, principals="G_ANALYSTS;g_newer")
    seed(db, position=3, schema="returns", principals="g_other")
    seed(db, UC_PRD, position=1, catalog="sales_prd", principals="g_new")

    mine = ledger.find_access(db, principals_in=["g_new", "g_analysts"])
    assert sorted((m.environment, m.principal_name) for m in mine.matches) == [
        ("dev", "G_ANALYSTS"), ("dev", "g_new"), ("prd", "g_new")]

    dev = ledger.find_access(db, principals_in=["g_new"], environment="dev")
    assert [m.principal_name for m in dev.matches] == ["g_new"]

    on_schema = ledger.find_access(db, catalog="sales_dev", schema_name="orders", principals_in=["g_new"])
    assert [(m.principal_name, m.via) for m in on_schema.matches] == [("g_new", "parent")]

    # An empty set of principals holds nothing, rather than meaning "everyone".
    assert ledger.find_access(db, principals_in=[]).matches == []


def test_find_access_suggests_a_schema_asked_for_as_a_catalog(db):
    seed(db, UC_PRD, position=1, catalog="enterprise_prd", schema="platinum_insights")
    seed(db, UC_PRD, position=2, kind="table", catalog="enterprise_prd", schema="platinum_insights", obj="sales")
    seed(db, position=1, catalog="enterprise_dev", schema="Platinum_Insights")

    result = ledger.find_access(db, catalog="platinum_insights", environment="prd")
    assert result.matches == []
    assert [(s.securable_type, s.catalog, s.schema_name, s.environments) for s in result.suggestions] == [
        ("schema", "enterprise_prd", "platinum_insights", ["prd"]),
        ("schema", "enterprise_dev", "Platinum_Insights", ["dev"]),
    ]

    dotted = ledger.find_access(db, catalog="enterprise_prd.platinum_insights.sales", environment="prd")
    assert ("table", "enterprise_prd", "platinum_insights", "sales") in [
        (s.securable_type, s.catalog, s.schema_name, s.object_name) for s in dotted.suggestions]

    partial = ledger.find_access(db, catalog="platinum")
    assert {s.schema_name for s in partial.suggestions} == {"platinum_insights", "Platinum_Insights"}

    assert ledger.find_access(db, catalog="nowhere").suggestions == []
    found = ledger.find_access(db, catalog="enterprise_prd", schema_name="platinum_insights")
    assert found.matches and found.suggestions == []
