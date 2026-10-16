"""CSV export, archive, path check, compare and scheduling (ADR 0005 D2, D11)."""
import asyncio
import copy
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import _yaml_config, settings
from app.db import EventModel, RequestModel
from app.db.rbac import RbacExportFileModel, RbacExportModel, RbacRuleModel
from app.services.rbac import audit, csv_export
from app.services.rbac.csv_export import ExportBusy

from .synthetic_workbook import (
    METASTORE_SHEET,
    SyntheticSheet,
    build_workbook,
    import_initial,
    managed,
    uc_sheet,
    write_workbook,
    ws_sheet,
)

ALL_SHEETS = [uc_sheet(s) for s in ("Dev", "Tst", "Stg", "Prd")] + \
    [ws_sheet(s) for s in ("Dev", "Tst", "Stg", "Prd")] + [METASTORE_SHEET]


# --- An independent reference writer: what Excel's "Save as CSV" produces -------


def _excel_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def excel_csv(rows, *, eol="\r\n", bom=False) -> bytes:
    out = []
    for row in rows:
        cells = []
        for value in row:
            cell = _excel_text(value)
            if any(ch in cell for ch in ',"\r\n'):
                cell = '"' + cell.replace('"', '""') + '"'
            cells.append(cell)
        out.append(",".join(cells) + eol)
    data = "".join(out).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr("app.services.rbac.policy.evaluate", lambda changes, **kw: [])
    monkeypatch.setattr(csv_export, "_change_pending_since", None)
    monkeypatch.setattr(csv_export, "_next_export_time", None)
    monkeypatch.setattr(settings, "RBAC_CSV_BOM", False)
    monkeypatch.setattr(settings, "RBAC_CSV_LINE_ENDING", "crlf")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ARCHIVE_DAYS", 90)
    set_paths(monkeypatch)


@pytest.fixture
def dev_allowed(monkeypatch):
    monkeypatch.setattr("app.core.config.dev_features_allowed", lambda: True)


@pytest.fixture
def imported(db_session):
    data, sheets = build_workbook()
    import_initial(db_session, data)
    return sheets


def set_paths(monkeypatch, metastore="", **env_paths):
    envs = copy.deepcopy(settings.RBAC_ENVIRONMENTS)
    for row in envs:
        row["export_path"] = env_paths.get(row["key"], "")
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", envs)
    monkeypatch.setattr(settings, "RBAC_METASTORE_EXPORT_PATH", metastore)


class FakeFiles:
    def __init__(self):
        self.uploads = {}
        self.dirs = []
        self.deleted = []

    def create_directory(self, path):
        self.dirs.append(path)

    def upload(self, file_path, contents, overwrite=False):
        assert overwrite is True
        self.uploads[file_path] = contents.read()

    def delete(self, file_path):
        self.deleted.append(file_path)


class FakeWorkspace:
    def __init__(self):
        self.uploads = {}
        self.dirs = []

    def mkdirs(self, path):
        self.dirs.append(path)

    def upload(self, path, content, *, format=None, overwrite=False):
        assert overwrite is True
        self.uploads[path] = (content.read(), format)


class FakeWorkspaceClient:
    instances = []

    def __init__(self):
        self.files = FakeFiles()
        self.workspace = FakeWorkspace()
        FakeWorkspaceClient.instances.append(self)


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeWorkspaceClient.instances = []
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", FakeWorkspaceClient)
    return FakeWorkspaceClient


# --- Rendering ----------------------------------------------------------------


@pytest.mark.parametrize("metastore_environment_column", [True, False])
def test_import_then_export_reproduces_every_sheet(db_session, metastore_environment_column):
    data, sheets = build_workbook(metastore_environment_column=metastore_environment_column)
    import_initial(db_session, data)
    for name, sheet in managed(sheets).items():
        assert csv_export.render_sheet(db_session, name) == excel_csv(sheet.padded()), name


def _tiny(db):
    rows = [
        ["Environment", "Securable Type", "Catalog", "SchemaName", "ObjectName", "Owner", "PrincipalType",
         "PrincipalName", "AccessPrivilege"],
        ["prd", "schema", "cat_prd", "sales", None, "grp_owner", "group", "grp_a;grp_b", "USE_SCHEMA;SELECT"],
        ["prd", "view", "cat_prd", "sales", 'say "hi", ok', "grp_owner", "group", "grp_c", "SELECT"],
    ]
    import_initial(db, write_workbook({uc_sheet("Prd"): SyntheticSheet(rows=rows)}))


TINY = (
    b"Environment,Securable Type,Catalog,SchemaName,ObjectName,Owner,PrincipalType,PrincipalName,AccessPrivilege\r\n"
    b"prd,schema,cat_prd,sales,,grp_owner,group,grp_a;grp_b,USE_SCHEMA;SELECT\r\n"
    b'prd,view,cat_prd,sales,"say ""hi"", ok",grp_owner,group,grp_c,SELECT\r\n'
)


def test_writer_bytes_are_pinned(db_session):
    _tiny(db_session)
    assert csv_export.render_sheet(db_session, uc_sheet("Prd")) == TINY


def test_writer_bom_and_lf_settings(db_session, monkeypatch):
    _tiny(db_session)
    monkeypatch.setattr(settings, "RBAC_CSV_BOM", True)
    assert csv_export.render_sheet(db_session, uc_sheet("Prd")) == b"\xef\xbb\xbf" + TINY
    monkeypatch.setattr(settings, "RBAC_CSV_BOM", False)
    monkeypatch.setattr(settings, "RBAC_CSV_LINE_ENDING", "lf")
    assert csv_export.render_sheet(db_session, uc_sheet("Prd")) == TINY.replace(b"\r\n", b"\n")


def test_bom_and_lf_round_trip(db_session, imported, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_CSV_BOM", True)
    monkeypatch.setattr(settings, "RBAC_CSV_LINE_ENDING", "lf")
    sheet = imported[uc_sheet("Dev")]
    assert csv_export.render_sheet(db_session, uc_sheet("Dev")) == excel_csv(sheet.padded(), eol="\n", bom=True)


def test_render_unknown_and_never_imported_sheets(db_session):
    with pytest.raises(ValueError):
        csv_export.render_sheet(db_session, "Sheet1")
    assert csv_export.render_sheet(db_session, ws_sheet("Dev")) == (
        b"Environment,PrincipalType,PrincipalName,WorkspaceName,SecurableType,SecurableName,SecurablePermission\r\n")


def test_deleted_rules_are_not_exported(db_session, imported):
    rule = db_session.query(RbacRuleModel).filter_by(sheet=uc_sheet("Prd"), position=0).one()
    rule.status = "deleted"
    db_session.commit()
    rows = imported[uc_sheet("Prd")].padded()
    assert csv_export.render_sheet(db_session, uc_sheet("Prd")) == excel_csv([rows[0], *rows[2:]])


# --- Export runs --------------------------------------------------------------


def test_export_writes_exactly_the_csvs_to_local_folders(db_session, imported, tmp_path, monkeypatch,
                                                        dev_allowed):
    set_paths(monkeypatch, metastore=str(tmp_path / "ms"), dev=str(tmp_path / "dev") + "/")
    run = csv_export.run_export(db_session, trigger="manual", actor="admin@example.com")

    assert run.status == "completed"
    assert run.id and run.actor == "admin@example.com"
    assert sorted(p.name for p in (tmp_path / "dev").iterdir()) == [f"{uc_sheet('Dev')}.csv",
                                                                     f"{ws_sheet('Dev')}.csv"]
    assert [p.name for p in (tmp_path / "ms").iterdir()] == [f"{METASTORE_SHEET}.csv"]
    for sheet, folder in ((uc_sheet("Dev"), "dev"), (ws_sheet("Dev"), "dev"), (METASTORE_SHEET, "ms")):
        written = (tmp_path / folder / f"{sheet}.csv").read_bytes()
        assert written == excel_csv(imported[sheet].padded())

    by_sheet = {f.sheet: f for f in run.files}
    assert set(by_sheet) == set(ALL_SHEETS)
    dev = by_sheet[uc_sheet("Dev")]
    assert dev.status == "written" and dev.downloadable
    assert dev.path == f"{tmp_path}/dev/{uc_sheet('Dev')}.csv"
    assert dev.row_count == 4
    data = (tmp_path / "dev" / f"{uc_sheet('Dev')}.csv").read_bytes()
    assert dev.sha256 == hashlib.sha256(data).hexdigest() and dev.size_bytes == len(data)
    skipped = [f for f in run.files if f.status == "skipped"]
    assert {f.sheet for f in skipped} == {uc_sheet("Tst"), ws_sheet("Tst"), uc_sheet("Stg"), ws_sheet("Stg"),
                                          uc_sheet("Prd"), ws_sheet("Prd")}
    assert {f.error for f in skipped} == {"No export path set"}
    assert len(run.warnings) == 3
    assert any('"tst"' in w and uc_sheet("Tst") in w and ws_sheet("Tst") in w for w in run.warnings)

    manifest = run.change_summary["manifest"]
    assert manifest["export_id"] == run.id and manifest["watermark"] == run.watermark
    assert {f["sheet"]: f["sha256"] for f in manifest["files"]}[uc_sheet("Dev")] == dev.sha256
    assert run.change_summary["created"] == sum(len(s.rows) - 1 for s in managed(imported).values())
    assert run.watermark == audit.watermark(db_session)


def test_local_folders_are_refused_outside_local_development(db_session, imported, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.dev_features_allowed", lambda: False)
    set_paths(monkeypatch, dev=str(tmp_path / "dev"))
    run = csv_export.run_export(db_session, trigger="manual")
    assert run.status == "failed"
    failed = [f for f in run.files if f.status == "failed"]
    assert {f.sheet for f in failed} == {uc_sheet("Dev"), ws_sheet("Dev")}
    assert {f.error for f in failed} == {csv_export.LOCAL_PATH_REFUSED}
    assert not (tmp_path / "dev").exists()


def test_volumes_are_written_through_the_sdk(db_session, imported, monkeypatch, fake_sdk):
    set_paths(monkeypatch, dev="/Volumes/main/rbac/exports/dev")
    run = csv_export.run_export(db_session, trigger="manual")
    assert run.status == "completed"
    client = fake_sdk.instances[0]
    assert len(fake_sdk.instances) == 1
    assert client.files.dirs == ["/Volumes/main/rbac/exports/dev"]
    assert set(client.files.uploads) == {
        f"/Volumes/main/rbac/exports/dev/{uc_sheet('Dev')}.csv",
        f"/Volumes/main/rbac/exports/dev/{ws_sheet('Dev')}.csv",
    }
    assert client.files.uploads[f"/Volumes/main/rbac/exports/dev/{ws_sheet('Dev')}.csv"] == \
        excel_csv(imported[ws_sheet("Dev")].padded())


def test_workspace_folders_are_written_through_the_sdk(db_session, imported, monkeypatch, fake_sdk):
    from databricks.sdk.service.workspace import ImportFormat

    set_paths(monkeypatch, metastore="/Workspace/Shared/rbac")
    run = csv_export.run_export(db_session, trigger="manual")
    assert run.status == "completed"
    ws = fake_sdk.instances[0].workspace
    assert ws.dirs == ["/Workspace/Shared/rbac"]
    content, fmt = ws.uploads[f"/Workspace/Shared/rbac/{METASTORE_SHEET}.csv"]
    assert fmt == ImportFormat.AUTO
    assert content == excel_csv(imported[METASTORE_SHEET].padded())


def test_one_failed_file_makes_a_partial_run(db_session, imported, monkeypatch, fake_sdk):
    set_paths(monkeypatch, dev="/Volumes/main/rbac/exports/dev")
    real_upload = FakeFiles.upload

    def flaky(self, file_path, contents, overwrite=False):
        if ws_sheet("Dev") in file_path:
            raise PermissionError("PERMISSION_DENIED: no WRITE VOLUME")
        real_upload(self, file_path, contents, overwrite=overwrite)

    monkeypatch.setattr(FakeFiles, "upload", flaky)
    run = csv_export.run_export(db_session, trigger="manual")
    assert run.status == "partial"
    failed = next(f for f in run.files if f.sheet == ws_sheet("Dev"))
    assert failed.status == "failed" and "service principal needs write access" in failed.error
    assert not failed.downloadable
    # A partial run doesn't count as exported: the schedule retries it.
    retry = csv_export.run_export(db_session, trigger="schedule")
    assert retry.status == "partial" and retry.id


def test_no_change_skips_unless_forced(db_session, imported, tmp_path, monkeypatch, dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path))
    first = csv_export.run_export(db_session, trigger="manual")
    assert first.status == "completed"
    runs_before = db_session.query(RbacExportModel).count()

    scheduled = csv_export.run_export(db_session, trigger="schedule")
    assert scheduled.status == "skipped" and scheduled.id == ""
    assert scheduled.warnings == ["No changes since the last export."]
    assert db_session.query(RbacExportModel).count() == runs_before

    manual = csv_export.run_export(db_session, trigger="manual")
    assert manual.status == "skipped" and manual.id
    assert db_session.query(RbacExportModel).count() == runs_before + 1

    forced = csv_export.run_export(db_session, trigger="manual", force=True)
    assert forced.status == "completed" and forced.id not in (first.id, manual.id)
    assert forced.change_summary["created"] == 0
    assert csv_export.status(db_session).pending_changes is False


def test_empty_ledger_and_unset_paths_skip_scheduled_runs(db_session, tmp_path, monkeypatch, dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path))
    run = csv_export.run_export(db_session, trigger="schedule")
    assert run.status == "skipped" and "ledger is empty" in run.warnings[0]
    assert db_session.query(RbacExportModel).count() == 0

    data, _ = build_workbook(only=[uc_sheet("Dev")])
    import_initial(db_session, data)
    set_paths(monkeypatch)
    run = csv_export.run_export(db_session, trigger="on_change")
    assert run.status == "skipped" and run.warnings == ["No export path is set for any sheet."]
    manual = csv_export.run_export(db_session, trigger="manual")
    assert manual.status == "skipped" and manual.id
    assert {f.status for f in manual.files} == {"skipped"}


def test_exports_are_single_flight(db_session, imported):
    assert csv_export._local_lock.acquire(blocking=False)
    try:
        with pytest.raises(ExportBusy, match="already running"):
            csv_export.run_export(db_session, trigger="manual")
    finally:
        csv_export._local_lock.release()


def test_unknown_trigger_is_refused(db_session):
    with pytest.raises(ValueError):
        csv_export.run_export(db_session, trigger="hourly")


def test_archived_files_download_by_ids_and_are_pruned(db_session, imported, tmp_path, monkeypatch,
                                                      dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path))
    old = RbacExportFileModel(id="old-file", export_id="old-run", sheet=uc_sheet("Dev"), status="written",
                              content="Environment\r\n",
                              created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120))
    db_session.add(old)
    db_session.commit()

    run = csv_export.run_export(db_session, trigger="manual")
    dev = next(f for f in run.files if f.sheet == uc_sheet("Dev"))
    name, content = csv_export.get_file(db_session, run.id, dev.id)
    assert name == f"{uc_sheet('Dev')}.csv"
    assert content == (tmp_path / name).read_bytes()
    assert csv_export.get_file(db_session, "other-run", dev.id) is None
    skipped = next(f for f in run.files if f.status == "skipped")
    assert csv_export.get_file(db_session, run.id, skipped.id) is None

    db_session.refresh(old)
    assert old.content is None
    assert csv_export.get_file(db_session, "old-run", "old-file") is None


def test_bom_survives_the_archive(db_session, imported, tmp_path, monkeypatch, dev_allowed):
    monkeypatch.setattr(settings, "RBAC_CSV_BOM", True)
    set_paths(monkeypatch, dev=str(tmp_path))
    run = csv_export.run_export(db_session, trigger="manual")
    dev = next(f for f in run.files if f.sheet == uc_sheet("Dev"))
    _, content = csv_export.get_file(db_session, run.id, dev.id)
    assert content.startswith(b"\xef\xbb\xbf")
    assert content == (tmp_path / f"{uc_sheet('Dev')}.csv").read_bytes()


def _request_change(db, sheet):
    req = RequestModel(id=audit.new_id(), type="rbac_access_change", title="Grant", status="completed",
                       requester_email="user@example.com", state_context={})
    db.add(req)
    db.flush()
    cs = audit.new_change_set(db, source="request", source_ref=req.id, actor="user@example.com",
                              reason="Approved request")
    rule = db.query(RbacRuleModel).filter_by(sheet=sheet, position=0).one()
    before = audit.snapshot(rule)
    rule.principal_name += ";grp_new"
    rule.version += 1
    audit.record_event(db, cs, rule_id=rule.id, action="update", before=before, after=audit.snapshot(rule))
    audit.finalize_change_set(cs, created=0, updated=1, deleted=0, sheets=[sheet], planes=[rule.plane])
    db.commit()
    return req, cs


def _facts(db, request_id):
    return db.query(EventModel).filter_by(request_id=request_id, event_type="rbac_exported").all()


def test_change_summary_and_rbac_exported_fact(db_session, imported, tmp_path, monkeypatch, dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path))
    first = csv_export.run_export(db_session, trigger="manual")
    req, cs = _request_change(db_session, uc_sheet("Dev"))
    other, _ = _request_change(db_session, uc_sheet("Prd"))  # its sheet has no export path

    run = csv_export.run_export(db_session, trigger="schedule")
    assert run.status == "completed"
    summary = run.change_summary
    assert summary["since_watermark"] == first.watermark
    assert summary["updated"] == 2 and summary["created"] == 0
    assert cs.id in summary["change_sets"]
    assert summary["request_ids"] == [req.id, other.id]

    facts = _facts(db_session, req.id)
    assert len(facts) == 1
    assert facts[0].event_data["export_id"] == run.id
    assert facts[0].event_data["files"] == [uc_sheet("Dev")]
    assert facts[0].event_data["watermark"] == run.watermark
    assert facts[0].event_data["actor"] == "system"
    # Nothing of the other request's was written, so it gets no fact yet.
    assert _facts(db_session, other.id) == []

    csv_export.run_export(db_session, trigger="manual", force=True)
    assert len(_facts(db_session, req.id)) == 1


def test_status_lists_runs_targets_and_schedule(db_session, imported, tmp_path, monkeypatch, dev_allowed):
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "0 * * * *")
    set_paths(monkeypatch, dev=str(tmp_path))
    before = csv_export.status(db_session)
    assert before.runs == [] and before.pending_changes is True
    assert before.next_run_at is not None and before.next_run_at > datetime.now(timezone.utc)
    assert [t.key for t in before.targets] == ["dev", "tst", "stg", "prd", "metastore"]
    assert before.targets[0].configured and not before.targets[1].configured
    assert before.targets[-1].sheets == [METASTORE_SHEET]

    run = csv_export.run_export(db_session, trigger="manual")
    after = csv_export.status(db_session)
    assert after.pending_changes is False
    assert after.runs[0].id == run.id
    assert [f.sheet for f in after.runs[0].files][:2] == [uc_sheet("Dev"), ws_sheet("Dev")]
    downloadable = {f.sheet for f in after.runs[0].files if f.downloadable}
    assert downloadable == {uc_sheet("Dev"), ws_sheet("Dev")}

    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "")
    assert csv_export.status(db_session).next_run_at is None


# --- Path check ---------------------------------------------------------------


def test_path_check_writes_and_removes_a_probe(tmp_path, monkeypatch, dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path / "out"))
    result = csv_export.test_path("dev")
    assert result.ok and result.path == str(tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_path_check_failures(tmp_path, monkeypatch):
    set_paths(monkeypatch, dev=str(tmp_path))
    assert csv_export.test_path("tst").ok is False
    assert "No export path" in csv_export.test_path("metastore").message
    assert csv_export.test_path("nowhere").ok is False
    monkeypatch.setattr("app.core.config.dev_features_allowed", lambda: False)
    refused = csv_export.test_path("dev")
    assert refused.ok is False and refused.message == csv_export.LOCAL_PATH_REFUSED


def test_path_check_reports_permission_errors(monkeypatch, fake_sdk):
    class PermissionDenied(Exception):
        pass

    def denied(self, file_path, contents, overwrite=False):
        raise PermissionDenied("User does not have WRITE VOLUME on Volume 'main.rbac.exports'.")

    monkeypatch.setattr(FakeFiles, "upload", denied)
    set_paths(monkeypatch, metastore="/Volumes/main/rbac/exports/metastore")
    result = csv_export.test_path("metastore")
    assert result.ok is False
    assert "service principal needs write access" in result.message

    monkeypatch.setattr(FakeFiles, "upload", lambda self, p, c, overwrite=False: None)
    ok = csv_export.test_path("metastore")
    assert ok.ok and "service principal can write" in ok.message
    deleted = fake_sdk.instances[-1].files.deleted
    assert len(deleted) == 1 and deleted[0].startswith("/Volumes/main/rbac/exports/metastore/.rbac_probe_")


# --- Compare ------------------------------------------------------------------


def _ours(db):
    return csv_export.render_sheet(db, uc_sheet("Dev"))


def _kinds(result):
    return [d.kind for d in result.differences]


def test_compare_identical(db_session, imported):
    result = csv_export.compare(db_session, uc_sheet("Dev"), _ours(db_session))
    assert result.identical and result.differences == []
    assert result.ours_sha256 == result.theirs_sha256


def test_compare_bom(db_session, imported):
    result = csv_export.compare(db_session, uc_sheet("Dev"), b"\xef\xbb\xbf" + _ours(db_session))
    assert not result.identical
    assert _kinds(result) == ["bom"]
    assert "Turn on" in result.differences[0].detail


def test_compare_line_endings(db_session, imported):
    theirs = _ours(db_session).replace(b"\r\n", b"\n")
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["line_endings"]
    assert "LF" in result.differences[0].detail and "to lf" in result.differences[0].detail


def test_compare_missing_trailing_newline(db_session, imported):
    theirs = _ours(db_session)[:-2]
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["trailing_newline"]


def test_compare_header_change(db_session, imported):
    theirs = _ours(db_session).replace(b"SchemaName", b"Schema Name", 1)
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["header"]
    assert "Schema Name" in result.differences[0].detail


def test_compare_missing_row(db_session, imported):
    rows = imported[uc_sheet("Dev")].padded()
    theirs = excel_csv([rows[0], rows[1], *rows[3:]])
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["rows"]
    assert result.only_in_theirs == []
    assert len(result.only_in_ours) == 1 and result.only_in_ours[0][1] == "schema"
    assert result.differences[0].detail.startswith("1 row is only in this export, and 0 rows are")


def test_compare_reordered_rows(db_session, imported):
    rows = imported[uc_sheet("Dev")].padded()
    theirs = excel_csv([rows[0], rows[2], rows[1], *rows[3:]])
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["rows"]
    assert "different order" in result.differences[0].detail
    assert result.only_in_ours == [] and result.only_in_theirs == []


def test_compare_quoting(db_session, imported):
    theirs = _ours(db_session).replace(b",USE_SCHEMA;SELECT,", b',"USE_SCHEMA;SELECT",', 1)
    assert theirs != _ours(db_session)
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    assert _kinds(result) == ["quoting"]
    assert "Line 2" in result.differences[0].detail


def test_compare_encoding(db_session):
    rows = [["Environment", "Securable Type", "Catalog", "SchemaName", "ObjectName", "Owner", "PrincipalType",
             "PrincipalName", "AccessPrivilege"],
            ["dev", "schema", "cat_dev", "café", None, "o", "group", "g", "USE_SCHEMA"]]
    import_initial(db_session, write_workbook({uc_sheet("Dev"): SyntheticSheet(rows=rows)}))
    theirs = _ours(db_session).decode("utf-8").encode("cp1252")
    result = csv_export.compare(db_session, uc_sheet("Dev"), theirs)
    # Read as Windows-1252, the cells themselves match.
    assert _kinds(result) == ["encoding"]


def test_compare_unknown_sheet(db_session):
    with pytest.raises(ValueError):
        csv_export.compare(db_session, "Sheet1", b"")


# --- Scheduling ---------------------------------------------------------------


def test_request_export_soon_only_with_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", False)
    csv_export.request_export_soon()
    assert csv_export._change_pending_since is None
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)
    csv_export.request_export_soon()
    first = csv_export._change_pending_since
    assert first is not None
    csv_export.request_export_soon()
    assert csv_export._change_pending_since == first


@pytest.fixture
def task_runs(monkeypatch):
    calls = []

    def fake_run(trigger):
        calls.append(trigger)
        return csv_export.ExportRun(id="x", trigger=trigger, status="completed")

    monkeypatch.setattr(csv_export, "_run_in_own_session", fake_run)
    monkeypatch.setitem(_yaml_config.setdefault("features", {}), "rbac", True)
    return calls


def test_task_runs_when_the_cron_is_due(monkeypatch, task_runs):
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "0 * * * *")
    asyncio.run(csv_export.rbac_export_task())
    assert task_runs == []
    assert csv_export._next_export_time is not None

    csv_export._next_export_time = datetime.now(timezone.utc) - timedelta(seconds=1)
    asyncio.run(csv_export.rbac_export_task())
    assert task_runs == ["schedule"]
    assert csv_export._next_export_time > datetime.now(timezone.utc)


def test_task_runs_on_change_after_the_delay(monkeypatch, task_runs):
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)
    csv_export._change_pending_since = datetime.now(timezone.utc) - timedelta(seconds=10)
    asyncio.run(csv_export.rbac_export_task())
    assert task_runs == []

    csv_export._change_pending_since = datetime.now(timezone.utc) - timedelta(seconds=61)
    asyncio.run(csv_export.rbac_export_task())
    assert task_runs == ["on_change"]
    assert csv_export._change_pending_since is None


def test_task_keeps_a_pending_change_when_busy(monkeypatch, task_runs):
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)

    def busy(trigger):
        raise ExportBusy("An export is already running.")

    monkeypatch.setattr(csv_export, "_run_in_own_session", busy)
    noted = datetime.now(timezone.utc) - timedelta(seconds=61)
    csv_export._change_pending_since = noted
    asyncio.run(csv_export.rbac_export_task())
    assert csv_export._change_pending_since == noted


def test_task_does_nothing_with_the_feature_off(monkeypatch, task_runs):
    monkeypatch.setitem(_yaml_config["features"], "rbac", False)
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "* * * * *")
    csv_export._next_export_time = datetime.now(timezone.utc) - timedelta(seconds=1)
    asyncio.run(csv_export.rbac_export_task())
    assert task_runs == []


def test_task_exports_with_its_own_session(db_session, imported, tmp_path, monkeypatch, dev_allowed):
    set_paths(monkeypatch, dev=str(tmp_path))
    monkeypatch.setitem(_yaml_config.setdefault("features", {}), "rbac", True)
    monkeypatch.setattr(settings, "RBAC_EXPORT_CRON", "")
    monkeypatch.setattr(settings, "RBAC_EXPORT_ON_CHANGE", True)
    monkeypatch.setattr("app.db.session.get_lakebase_session", lambda: _Unclosable(db_session))
    csv_export._change_pending_since = datetime.now(timezone.utc) - timedelta(seconds=61)
    asyncio.run(csv_export.rbac_export_task())
    run = db_session.query(RbacExportModel).one()
    assert run.trigger == "on_change" and run.status == "completed"
    assert (tmp_path / f"{uc_sheet('Dev')}.csv").exists()


class _Unclosable:
    """The test session, handed to the task without letting it close the session."""

    def __init__(self, session):
        self._session = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)
