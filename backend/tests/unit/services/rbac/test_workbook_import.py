"""Workbook import: preview, initial import, guarded reset (ADR 0005 D3)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401 - registers every table
from app.core.config import settings
from app.db import RequestModel
from app.db.base import Base
from app.db.rbac import (
    RbacChangeSetModel,
    RbacImportModel,
    RbacRuleEventModel,
    RbacRuleModel,
    RbacSheetModel,
)
from app.models.rbac import Finding
from app.services.rbac import audit, csv_export, workbook_import
from app.services.rbac.workbook_import import CONFIRMATION_PHRASE, ImportFailed

from .synthetic_workbook import (
    METASTORE_SHEET,
    MULTILINE_NOTE,
    QUOTED_NOTE,
    SyntheticSheet,
    build_workbook,
    data_rows,
    import_initial,
    uc_sheet,
    write_workbook,
    ws_sheet,
)

ACTOR = "admin@example.com"


@pytest.fixture(autouse=True)
def no_policy(monkeypatch):
    """Import mechanics must not depend on policy content."""
    monkeypatch.setattr("app.services.rbac.policy.evaluate", lambda changes, **kw: [])
    monkeypatch.setattr(csv_export, "_change_pending_since", None)


@pytest.fixture
def own_db():
    """A session that really commits and rolls back (db_session's outer transaction can't survive a rollback)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _active(db, sheet=None):
    q = db.query(RbacRuleModel).filter(RbacRuleModel.status == "active")
    if sheet:
        q = q.filter(RbacRuleModel.sheet == sheet)
    return q.order_by(RbacRuleModel.sheet, RbacRuleModel.position).all()


def _open_request(db, status="pending"):
    req = RequestModel(id=audit.new_id(), type="rbac_access_change", title="Access change", status=status,
                       requester_email="user@example.com", state_context={})
    db.add(req)
    db.commit()
    return req


# --- Preview ------------------------------------------------------------------


def test_preview_reports_sheets_skipped_tabs_and_findings(db_session):
    data, sheets = build_workbook()
    preview = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)

    assert [s.sheet for s in preview.sheets] == [k for k in sheets if k != "Sheet1"]
    assert preview.skipped_sheets == ["Sheet1"]
    assert preview.missing_sheets == []
    assert preview.mode == "initial"
    assert preview.confirmation_phrase is None
    assert preview.existing_rules == 0
    assert sum(s.rows for s in preview.sheets) == data_rows(sheets)

    by_name = {s.sheet: s for s in preview.sheets}
    assert by_name[uc_sheet("Dev")].unknown_headers == ["Notes"]
    assert by_name[ws_sheet("Tst")].unknown_headers == ["Ticket"]
    assert by_name[uc_sheet("Stg")].duplicates == 1
    # The empty header past the last named column is kept, with no field.
    assert by_name[ws_sheet("Prd")].columns[-1].header == ""
    assert by_name[ws_sheet("Prd")].columns[-1].field is None
    # The hidden Environment column on the metastore sheet is read like any other.
    assert by_name[METASTORE_SHEET].columns[0].field == "environment_value"

    codes = [(f.code, f.sheet) for f in preview.findings]
    assert ("unknown_header", uc_sheet("Dev")) in codes
    assert ("duplicate_row", uc_sheet("Stg")) in codes
    dup = next(f for f in preview.findings if f.code == "duplicate_row")
    assert dup.row_number == 2 and dup.severity == "warn"
    assert preview.findings_total == len(preview.findings)

    stored = db_session.get(RbacImportModel, preview.import_id)
    assert stored.status == "previewed"
    assert stored.file_sha256 == preview.file_sha256
    assert stored.skipped_sheets == ["Sheet1"]
    assert stored.sheets[uc_sheet("Dev")]["columns"][9] == {"header": "Notes", "field": None}
    # A preview writes no rules.
    assert _active(db_session) == []


def test_preview_lists_missing_sheets(db_session):
    keep = [uc_sheet("Dev"), ws_sheet("Dev"), "Sheet1"]
    data, _ = build_workbook(only=keep)
    preview = workbook_import.preview(db_session, data, "partial.xlsx", actor=ACTOR)
    assert [s.sheet for s in preview.sheets] == [ws_sheet("Dev"), uc_sheet("Dev")]
    assert set(preview.missing_sheets) == {
        uc_sheet("Tst"), uc_sheet("Stg"), uc_sheet("Prd"),
        ws_sheet("Tst"), ws_sheet("Stg"), ws_sheet("Prd"), METASTORE_SHEET,
    }


def test_unreadable_file_is_refused(db_session):
    with pytest.raises(ImportFailed, match="isn't a readable .xlsx workbook"):
        workbook_import.preview(db_session, b"Environment,Catalog\r\ndev,cat\r\n", "rules.csv", actor=ACTOR)
    assert db_session.query(RbacImportModel).count() == 0


def test_workbook_without_managed_sheets_is_refused(db_session):
    data = write_workbook({"Sheet1": SyntheticSheet(rows=[["a", "b"]])})
    with pytest.raises(ImportFailed, match="None of the workbook's tabs"):
        workbook_import.preview(db_session, data, "scratch.xlsx", actor=ACTOR)


def test_policy_findings_are_mapped_to_sheet_and_row(db_session, monkeypatch):
    def fake_evaluate(changes, *, path, **kw):
        assert path == "import"
        out = []
        for i, (before, after) in enumerate(changes):
            assert before is None
            if after.sheet == uc_sheet("Prd") and after.object_name == "fiscal_month":
                # Imports are warn-only, whatever the policy says.
                out.append(Finding(severity="deny", code="naming_rule", message="Bad name.", change_index=i,
                                   field="object_name"))
        return out

    monkeypatch.setattr("app.services.rbac.policy.evaluate", fake_evaluate)
    data, _ = build_workbook()
    preview = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)
    finding = next(f for f in preview.findings if f.code == "naming_rule")
    assert finding.sheet == uc_sheet("Prd")
    assert finding.row_number == 3
    assert finding.severity == "warn"
    assert next(s for s in preview.sheets if s.sheet == uc_sheet("Prd")).warnings == 1


def test_findings_are_capped(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.rbac.policy.evaluate",
        lambda changes, **kw: [Finding(severity="warn", code="naming_rule", message="x", change_index=0)] * 600,
    )
    data, _ = build_workbook()
    preview = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)
    assert len(preview.findings) == workbook_import.MAX_PREVIEW_FINDINGS
    assert preview.findings_total >= 600
    stored = db_session.get(RbacImportModel, preview.import_id)
    assert len(stored.findings) == workbook_import.MAX_STORED_FINDINGS


def test_policy_crash_does_not_block_the_import(db_session, monkeypatch):
    def broken(changes, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.rbac.policy.evaluate", broken)
    data, _ = build_workbook()
    preview = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)
    assert "policy_unavailable" in {f.code for f in preview.findings}


# --- Initial import -----------------------------------------------------------


def test_initial_import_inserts_rows_layouts_and_one_change_set(db_session):
    data, sheets = build_workbook()
    preview, result = import_initial(db_session, data)

    total = data_rows(sheets)
    assert result.created == total and result.deleted == 0
    rules = _active(db_session)
    assert len(rules) == total
    assert all(r.source == "import" and r.source_ref == preview.import_id and r.version == 1 for r in rules)
    assert all(r.created_by == ACTOR for r in rules)
    dev = _active(db_session, uc_sheet("Dev"))
    assert [r.position for r in dev] == [0, 1, 2, 3]
    assert dev[0].principal_name.count(";") == 11
    assert dev[0].privilege == "USE_SCHEMA;SELECT"
    assert dev[0].environment == "dev" and dev[0].plane == "uc"
    assert dev[0].row_hash == audit.compute_row_hash(uc_sheet("Dev"), {
        f: getattr(dev[0], f) for f in ("environment_value", "securable_type", "catalog", "schema_name",
                                        "object_name", "workspace_name", "owner", "principal_type",
                                        "principal_name", "privilege")}, dev[0].extra)

    ws = _active(db_session, ws_sheet("Dev"))
    assert [r.principal_type for r in ws] == ["group", "group", "group", "service principal"]
    assert ws[2].privilege == "workspace_access;databricks_sql_access"
    assert ws[0].workspace_name == "ws_dev" and ws[0].object_name == "ws_dev"

    change_sets = db_session.query(RbacChangeSetModel).all()
    assert len(change_sets) == 1
    cs = change_sets[0]
    assert cs.id == result.change_set_id
    assert cs.source == "import" and cs.source_ref == preview.import_id
    assert cs.reason == "Workbook import: rbac.xlsx"
    assert cs.summary["created"] == total and cs.summary["deleted"] == 0
    events = db_session.query(RbacRuleEventModel).all()
    assert len(events) == total
    assert {e.action for e in events} == {"create"}
    assert {e.change_set_id for e in events} == {cs.id}

    layout_row = db_session.get(RbacSheetModel, uc_sheet("Dev"))
    assert [c["header"] for c in layout_row.columns][-1] == "Notes"
    assert layout_row.source_import_id == preview.import_id

    imp = db_session.get(RbacImportModel, preview.import_id)
    assert imp.status == "completed" and imp.mode == "initial" and imp.change_set_id == cs.id
    assert imp.completed_at is not None


def test_unknown_columns_pass_through_extra(db_session):
    data, _ = build_workbook()
    import_initial(db_session, data)
    dev = _active(db_session, uc_sheet("Dev"))
    assert dev[0].extra == {"9": QUOTED_NOTE}
    assert dev[1].extra == {}
    assert dev[2].extra == {"9": MULTILINE_NOTE}
    tst = _active(db_session, ws_sheet("Tst"))
    # Numbers are stored as Excel shows them (no ".0").
    assert [r.extra.get("3") for r in tst] == ["1042", "7", None, "1043"]
    assert tst[0].workspace_name == "ws_tst"
    prd = _active(db_session, ws_sheet("Prd"))
    assert prd[1].extra == {"7": "checked by hand"}


def test_metastore_environment_is_resolved_from_the_cell(db_session):
    data, _ = build_workbook(metastore_environment_column=True)
    import_initial(db_session, data)
    rows = _active(db_session, METASTORE_SHEET)
    assert [r.environment_value for r in rows] == ["stg", "prd", "Dev", "production", "sandbox"]
    assert [r.environment for r in rows] == ["stg", "prd", "dev", "prd", None]
    assert rows[0].privilege == "MANAGE;USE_CONNECTION"
    assert rows[0].plane == "uc_metastore"


def test_metastore_without_environment_column(db_session):
    data, _ = build_workbook(metastore_environment_column=False)
    import_initial(db_session, data)
    rows = _active(db_session, METASTORE_SHEET)
    assert {r.environment for r in rows} == {None}
    assert rows[0].catalog == "conn_crm_stg"
    assert [c["header"] for c in db_session.get(RbacSheetModel, METASTORE_SHEET).columns][0] == "Securable Type"


def test_blank_rows_are_skipped(db_session):
    rows = [["Environment", "Securable Type", "Catalog", "SchemaName", "ObjectName", "Owner", "PrincipalType",
             "PrincipalName", "AccessPrivilege"],
            ["dev", "schema", "cat_dev", "a", None, "o", "group", "g1", "USE_SCHEMA"],
            [None] * 9,
            ["dev", "schema", "cat_dev", "b", None, "o", "group", "g2", "USE_SCHEMA"]]
    data = write_workbook({uc_sheet("Dev"): SyntheticSheet(rows=rows)})
    preview, result = import_initial(db_session, data)
    assert result.created == 2
    assert [r.schema_name for r in _active(db_session, uc_sheet("Dev"))] == ["a", "b"]


def test_cell_text_matches_excel_general_format():
    text = workbook_import._cell_text
    assert text(None) == ""
    assert text("  padded  ") == "  padded  "
    assert text(True) == "TRUE" and text(False) == "FALSE"
    assert text(42) == "42"
    assert text(7.0) == "7"
    assert text(2.5) == "2.5"
    assert text(0.1 + 0.2) == "0.3"
    assert text(datetime(2026, 1, 31)) == "2026-01-31"
    assert text(datetime(2026, 1, 31, 8, 30)) == "2026-01-31 08:30:00"


def test_list_imports_newest_first(db_session):
    data, _ = build_workbook(only=[uc_sheet("Dev")])
    first = workbook_import.preview(db_session, data, "one.xlsx", actor=ACTOR)
    first_created = db_session.get(RbacImportModel, first.import_id).created_at
    db_session.get(RbacImportModel, first.import_id).created_at = first_created - timedelta(minutes=5)
    db_session.commit()
    second = workbook_import.preview(db_session, data, "two.xlsx", actor=ACTOR)
    listed = workbook_import.list_imports(db_session)
    assert [i.id for i in listed][:2] == [second.import_id, first.import_id]
    assert listed[0].status == "previewed" and listed[0].filename == "two.xlsx"


# --- Run guards ---------------------------------------------------------------


def test_run_requires_the_previewed_file(db_session):
    data, _ = build_workbook()
    other, _ = build_workbook(only=[uc_sheet("Dev")])
    preview = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)
    with pytest.raises(ImportFailed, match="isn't the one that was previewed"):
        workbook_import.run(db_session, other, "rbac.xlsx", preview_id=preview.import_id, mode="initial",
                            confirmation="", actor=ACTOR)
    with pytest.raises(ImportFailed, match="wasn't found"):
        workbook_import.run(db_session, data, "rbac.xlsx", preview_id="nope", mode="initial",
                            confirmation="", actor=ACTOR)
    assert _active(db_session) == []
    assert db_session.get(RbacImportModel, preview.import_id).status == "previewed"


def test_run_refuses_a_mode_the_ledger_no_longer_matches(db_session):
    data, _ = build_workbook()
    stale = workbook_import.preview(db_session, data, "rbac.xlsx", actor=ACTOR)
    with pytest.raises(ImportFailed, match="initial import"):
        workbook_import.run(db_session, data, "rbac.xlsx", preview_id=stale.import_id, mode="reset",
                            confirmation=CONFIRMATION_PHRASE, actor=ACTOR)
    import_initial(db_session, data)
    # The ledger filled up after this preview was taken.
    with pytest.raises(ImportFailed, match="would be a reset"):
        workbook_import.run(db_session, data, "rbac.xlsx", preview_id=stale.import_id, mode="initial",
                            confirmation="", actor=ACTOR)


def test_a_preview_can_be_used_once(db_session):
    data, _ = build_workbook()
    preview, _ = import_initial(db_session, data)
    with pytest.raises(ImportFailed, match="already used"):
        workbook_import.run(db_session, data, "rbac.xlsx", preview_id=preview.import_id, mode="reset",
                            confirmation=CONFIRMATION_PHRASE, actor=ACTOR)


def _reset_preview(db, monkeypatch, allow=True):
    data, _ = build_workbook()
    import_initial(db, data)
    monkeypatch.setattr(settings, "RBAC_ALLOW_RESET", allow)
    again, _ = build_workbook(only=[uc_sheet("Dev"), ws_sheet("Dev")])
    preview = workbook_import.preview(db, again, "v2.xlsx", actor=ACTOR)
    return again, preview


def test_reset_preview_asks_for_the_phrase(db_session, monkeypatch):
    _, preview = _reset_preview(db_session, monkeypatch)
    assert preview.mode == "reset"
    assert preview.confirmation_phrase == CONFIRMATION_PHRASE
    assert preview.reset_allowed is True
    assert preview.existing_rules > 0


def test_reset_is_refused_when_turned_off(db_session, monkeypatch):
    data, preview = _reset_preview(db_session, monkeypatch, allow=False)
    assert preview.reset_allowed is False
    with pytest.raises(ImportFailed, match="Reset is turned off"):
        workbook_import.run(db_session, data, "v2.xlsx", preview_id=preview.import_id, mode="reset",
                            confirmation=CONFIRMATION_PHRASE, actor=ACTOR)


def test_reset_requires_the_exact_phrase(db_session, monkeypatch):
    data, preview = _reset_preview(db_session, monkeypatch)
    with pytest.raises(ImportFailed, match="exactly"):
        workbook_import.run(db_session, data, "v2.xlsx", preview_id=preview.import_id, mode="reset",
                            confirmation=CONFIRMATION_PHRASE.lower(), actor=ACTOR)


def test_reset_is_blocked_while_a_request_is_open(db_session, monkeypatch):
    data, preview = _reset_preview(db_session, monkeypatch)
    _open_request(db_session, status="awaiting_approval")
    _open_request(db_session, status="completed")
    before = len(_active(db_session))
    with pytest.raises(ImportFailed, match="1 access request is still in progress"):
        workbook_import.run(db_session, data, "v2.xlsx", preview_id=preview.import_id, mode="reset",
                            confirmation=CONFIRMATION_PHRASE, actor=ACTOR)
    assert len(_active(db_session)) == before


def test_reset_soft_deletes_and_reinserts_in_one_change_set(db_session, monkeypatch):
    data, preview = _reset_preview(db_session, monkeypatch)
    old_ids = {r.id for r in _active(db_session)}
    result = workbook_import.run(db_session, data, "v2.xlsx", preview_id=preview.import_id, mode="reset",
                                 confirmation=CONFIRMATION_PHRASE, actor="other@example.com")

    assert result.deleted == len(old_ids)
    assert result.created == 8
    active = _active(db_session)
    assert len(active) == 8 and not ({r.id for r in active} & old_ids)
    assert {r.sheet for r in active} == {uc_sheet("Dev"), ws_sheet("Dev")}
    deleted = db_session.query(RbacRuleModel).filter(RbacRuleModel.id.in_(old_ids)).all()
    assert {r.status for r in deleted} == {"deleted"}
    assert {r.version for r in deleted} == {2}
    assert {r.updated_by for r in deleted} == {"other@example.com"}

    events = db_session.query(RbacRuleEventModel).filter(RbacRuleEventModel.change_set_id == result.change_set_id).all()
    assert sum(e.action == "delete" for e in events) == len(old_ids)
    assert sum(e.action == "create" for e in events) == 8
    delete_event = next(e for e in events if e.action == "delete")
    assert delete_event.before["status"] == "active" and delete_event.after["status"] == "deleted"
    cs = db_session.get(RbacChangeSetModel, result.change_set_id)
    assert cs.reason == "Workbook reset: v2.xlsx"
    assert cs.summary["created"] == 8 and cs.summary["deleted"] == len(old_ids)
    assert db_session.get(RbacImportModel, preview.import_id).mode == "reset"


def test_import_requests_an_export_when_export_on_change_is_on(db_session, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)
    data, _ = build_workbook(only=[uc_sheet("Dev")])
    import_initial(db_session, data)
    assert csv_export._change_pending_since is not None


def test_a_failed_write_rolls_back_and_marks_the_import_failed(own_db, monkeypatch):
    data, _ = build_workbook()
    preview = workbook_import.preview(own_db, data, "rbac.xlsx", actor=ACTOR)

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(audit, "finalize_change_set", broken)
    with pytest.raises(ImportFailed, match="nothing was changed"):
        workbook_import.run(own_db, data, "rbac.xlsx", preview_id=preview.import_id, mode="initial",
                            confirmation="", actor=ACTOR)
    assert own_db.query(RbacRuleModel).count() == 0
    assert own_db.query(RbacChangeSetModel).count() == 0
    assert own_db.query(RbacRuleEventModel).count() == 0
    imp = own_db.get(RbacImportModel, preview.import_id)
    assert imp.status == "failed" and "disk on fire" in imp.error
