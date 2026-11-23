"""Bulk SELECT grants from a two-column CSV (ADR 0007): parsing, the environment
from the catalog name, planning through the request planner (packed rows,
prerequisites), covered / duplicate / rejected lines, the cap and the fingerprint."""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.core.default_config import DEFAULT_CONFIG
from app.db.base import Base
from app.db.rbac import RbacRuleModel
from app.services.rbac import audit, csv_export, grant_import, layout, ledger
from app.services.rbac.grant_import import GrantFileUnreadable, environment_for_catalog, parse_securable
from app.services.rbac.ledger import LedgerError

UC_PRD = "UC_Securables_RBAC_Prd"
UC_STG = "UC_Securables_RBAC_Stg"
OWNER = "metastore_admins"
ADMIN = "admin@example.com"
REASON = "Finance read access batch"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


ENVIRONMENTS = [
    {"key": "tst", "aliases": "test,qa", "sheet_suffix": "Tst"},
    {"key": "stg", "aliases": "stage,staging", "sheet_suffix": "Stg"},
    {"key": "prd", "aliases": "prod,production", "sheet_suffix": "Prd", "write_privileges": False},
]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [dict(e) for e in ENVIRONMENTS])
    monkeypatch.setattr(settings, "RBAC_UC_SHEET_PATTERN", "UC_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_WS_SHEET_PATTERN", "WS_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_METASTORE_SHEET", "UC_metastore_RBAC")
    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 200)
    monkeypatch.setattr(settings, "RBAC_MIN_REASON_LENGTH", 10)
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES",
                        copy.deepcopy(DEFAULT_CONFIG["rbac"]["allowed_privileges"]))
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["account users", "users", "admins"])
    monkeypatch.setattr(settings, "RBAC_NAMING_RULES", [])
    monkeypatch.setattr(settings, "RBAC_DEFAULT_OWNER", "")
    monkeypatch.setattr(csv_export, "request_export_soon", lambda: None)


def set_catalogs(monkeypatch, **by_env):
    envs = [dict(e, catalogs=by_env.get(e["key"], "")) for e in ENVIRONMENTS]
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", envs)


def seed(db, sheet=UC_PRD, *, position=1, principals="g_one;g_two", privileges="USE_SCHEMA;SELECT",
         kind="schema", catalog="enterprise_prd", schema="gold_product_cost", obj=""):
    spec = layout.sheet_spec(sheet)
    cells = dict(
        environment_value=spec.environment, securable_type=kind, catalog=catalog, schema_name=schema,
        object_name=obj, workspace_name="", owner=OWNER, principal_type="group",
        principal_name=principals, privilege=privileges,
    )
    rule = RbacRuleModel(
        id=audit.new_id(), sheet=sheet, plane=spec.plane, environment=spec.environment, position=position,
        extra={}, status="active", version=1, row_hash=audit.compute_row_hash(sheet, cells, {}),
        source="import", **cells,
    )
    db.add(rule)
    db.commit()
    return rule


def csv_of(*lines, header=True) -> bytes:
    rows = (["Securable,Group"] if header else []) + [",".join(line) for line in lines]
    return ("\r\n".join(rows) + "\r\n").encode("utf-8")


def plan(db, data):
    return grant_import.plan(db, data, "grants.csv", actor=ADMIN)


def by_line(preview):
    return {line.line: line for line in preview.lines}


# --- Column A ------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("enterprise_prd.gold_product_cost", ("schema", "enterprise_prd", "gold_product_cost", "")),
    ("enterprise_prd.gold_product_cost.*", ("schema", "enterprise_prd", "gold_product_cost", "")),
    ("enterprise_prd.gold_product_cost.pte_die_cost", ("view", "enterprise_prd", "gold_product_cost", "pte_die_cost")),
    (" `enterprise_prd`.`finance` ", ("schema", "enterprise_prd", "finance", "")),
])
def test_column_a_names_a_schema_or_a_view(text, expected):
    assert parse_securable(text) == (expected, "")


@pytest.mark.parametrize("text, problem", [
    ("", "empty"),
    ("enterprise_prd", "only a catalog"),
    ("enterprise_prd.*", "whole catalog"),
    ("a.b.c.d", "more than three parts"),
    ("a..c", "empty"),
    ("a.*.c", "can only end"),
])
def test_other_forms_of_column_a_are_rejected(text, problem):
    parsed, message = parse_securable(text)
    assert parsed is None and problem in message


# --- The environment, from the catalog name ----------------------------------------------


@pytest.mark.parametrize("catalog, env", [
    ("enterprise_gold_prd", "prd"),
    ("enterprise_silver_stg", "stg"),
    ("iplegal_ingest_prd", "prd"),
    ("enterprise_external_prd_sfedhfed", "prd"),
    ("enterprise_external_prd_dpfprprd", "prd"),
    ("ENTERPRISE_PRD", "prd"),
])
def test_the_environment_is_a_whole_segment_of_the_catalog_name(catalog, env):
    assert environment_for_catalog(catalog, {}) == (env, "")


def test_a_partial_segment_doesnt_count():
    env, message = environment_for_catalog("enterprise_external_dpfprprd", {})
    assert env is None and "names an environment" in message and "Catalogs" in message


def test_a_name_with_two_environments_is_ambiguous():
    env, message = environment_for_catalog("qa_tools_prd", {})
    assert env is None and "more than one environment (tst, prd)" in message


def test_the_catalogs_setting_decides_first(monkeypatch):
    set_catalogs(monkeypatch, stg="qa_tools_prd, odd_catalog")
    assert environment_for_catalog("QA_TOOLS_PRD", {}) == ("stg", "")
    assert environment_for_catalog("odd_catalog", {}) == ("stg", "")


def test_a_catalog_listed_under_two_environments_is_rejected(monkeypatch):
    set_catalogs(monkeypatch, stg="shared", prd="shared")
    env, message = environment_for_catalog("shared", {})
    assert env is None and "more than one environment" in message


def test_a_catalog_whose_rules_are_on_another_sheet_is_rejected():
    env, message = environment_for_catalog("x_prd", {"x_prd": {"stg"}})
    assert env is None and "existing rules are on the stg sheet" in message
    assert environment_for_catalog("x_prd", {"x_prd": {"stg", "prd"}}) == ("prd", "")


# --- Planning ---------------------------------------------------------------------------------


def test_a_schema_line_appends_the_group_to_the_existing_packed_row(db):
    row = seed(db)
    seed(db, kind="catalog", schema="", privileges="USE_CATALOG", principals="g_new", position=2)
    preview, change = plan(db, csv_of(("enterprise_prd.gold_product_cost.*", "g_new")))

    [line] = preview.lines
    assert (line.status, line.environment, line.securable_type) == ("change", "prd", "schema")
    assert "existing USE_SCHEMA;SELECT row" in line.message
    [diff] = preview.diffs
    assert (diff.op, diff.rule_id, diff.after["principal_name"]) == ("update", row.id, "g_one;g_two;g_new")
    assert [c.model_dump(exclude_none=True) for c in change.changes] == [
        {"op": "update", "rule_id": row.id, "version": 1, "cells": {"principal_name": "g_one;g_two;g_new"}}]
    assert preview.prerequisites == [] and not preview.blocked and preview.fingerprint


def test_a_view_line_adds_a_select_row_and_its_prerequisites(db):
    preview, _ = plan(db, csv_of(("enterprise_prd.gold_product_cost.pte_die_cost", "g_yield")))

    assert preview.prerequisites == [
        "USE_CATALOG on enterprise_prd for g_yield (prd)",
        "USE_SCHEMA on enterprise_prd.gold_product_cost for g_yield (prd)",
    ]
    made = [(d.op, d.after["securable_type"], d.after["object_name"], d.after["privilege"], d.sheet)
            for d in preview.diffs]
    assert made == [
        ("create", "catalog", "", "USE_CATALOG", UC_PRD),
        ("create", "schema", "", "USE_SCHEMA", UC_PRD),
        ("create", "view", "pte_die_cost", "SELECT", UC_PRD),
    ]


def test_lines_span_several_sheets_and_the_header_is_optional(db):
    data = csv_of(("enterprise_prd.finance", "g_fin"), ("enterprise_stg.finance", "g_fin"), header=False)
    preview, _ = plan(db, data)
    assert preview.header_skipped is False
    assert {d.sheet for d in preview.diffs} == {UC_PRD, UC_STG}

    with_header, _ = plan(db, csv_of(("enterprise_prd.finance", "g_fin")))
    assert with_header.header_skipped is True and [l.line for l in with_header.lines] == [2]


def test_a_grant_already_held_is_skipped(db):
    seed(db)
    seed(db, kind="catalog", schema="", privileges="USE_CATALOG", principals="g_two", position=2)
    preview, change = plan(db, csv_of(("enterprise_prd.gold_product_cost", "G_TWO")))
    [line] = preview.lines
    assert line.status == "already_held" and "already holds" in line.message
    assert change.changes == [] and not preview.blocked


def test_views_covered_by_a_schema_or_catalog_read_are_flagged_and_not_written(db):
    seed(db, principals="g_schema")
    seed(db, kind="catalog", schema="", privileges="USE_CATALOG;SELECT", principals="g_catalog", position=2)
    preview, _ = plan(db, csv_of(
        ("enterprise_prd.gold_product_cost.pte_ft_cost", "g_schema"),
        ("enterprise_prd.gold_product_cost.pte_ft_cost", "g_catalog"),
        ("enterprise_stg.gold_product_cost.*", "g_file"),
        ("enterprise_stg.gold_product_cost.pte_ft_cost", "g_file"),
    ))
    lines = by_line(preview)
    assert [lines[n].status for n in (2, 3, 4, 5)] == ["covered", "covered", "change", "covered"]
    assert "SELECT on enterprise_prd.gold_product_cost, already in the rules" in lines[2].message
    assert "catalog enterprise_prd" in lines[3].message
    assert "granted on line 4" in lines[5].message
    assert not any(d.after and d.after["object_name"] == "pte_ft_cost" for d in preview.diffs)
    assert not preview.blocked


def test_identical_lines_are_written_once(db):
    preview, _ = plan(db, csv_of(("enterprise_prd.finance", "g_fin"), ("Enterprise_Prd.Finance.*", "G_FIN")))
    lines = by_line(preview)
    assert (lines[2].status, lines[3].status) == ("change", "duplicate")
    assert "Same as line 2" in lines[3].message
    assert [d.after["securable_type"] for d in preview.diffs] == ["catalog", "schema"]


def test_rejected_lines_block_the_save(db):
    preview, _ = plan(db, csv_of(
        ("enterprise_prd.finance", "g_fin"),
        ("enterprise_prd", "g_fin"),
        ("enterprise_prd.finance", ""),
        ("enterprise_prd.finance", "g_a;g_b"),
        ("enterprise_prd.finance", "g_fin", "note"),
        ("nowhere.finance", "g_fin"),
    ))
    statuses = [line.status for line in preview.lines]
    assert statuses == ["change", "rejected", "rejected", "rejected", "rejected", "rejected"]
    assert preview.blocked and preview.blocked_reasons[0].startswith("5 lines are rejected")


def test_going_over_the_cap_blocks_the_save(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_MAX_CHANGES_PER_COMMIT", 2)
    preview, _ = plan(db, csv_of(("enterprise_prd.finance.a", "g_x")))
    assert preview.blocked and "at most 2" in preview.blocked_reasons[0]


def test_policy_deny_findings_block_the_save(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["users"])
    preview, _ = plan(db, csv_of(("enterprise_prd.finance", "users")))
    assert preview.blocked and any(f.severity == "deny" for f in preview.findings)


def test_unreadable_and_empty_files_are_refused(db):
    with pytest.raises(GrantFileUnreadable):
        plan(db, b"Securable,Group\r\n")
    with pytest.raises(GrantFileUnreadable):
        plan(db, b"\x00\x01\x02 binary")


# --- Saving -------------------------------------------------------------------------------


def test_save_commits_one_change_set_through_the_direct_edit_path(db):
    row = seed(db)
    data = csv_of(("enterprise_prd.gold_product_cost", "g_new"), ("enterprise_prd.finance.v", "g_new"))
    preview, _ = plan(db, data)

    change = grant_import.planned_change(db, data, "grants.csv", expected_fingerprint=preview.fingerprint,
                                         reason=REASON, actor=ADMIN)
    result = ledger.commit_change_set(db, change, actor=ADMIN)

    assert result.change_set_id and len(result.diffs) == len(preview.diffs)
    db.expire_all()
    assert db.get(RbacRuleModel, row.id).principal_name == "g_one;g_two;g_new"
    again, change_again = plan(db, data)
    assert {line.status for line in again.lines} == {"already_held"} and change_again.changes == []


def test_save_refuses_when_the_rules_changed_since_the_preview(db):
    row = seed(db)
    data = csv_of(("enterprise_prd.gold_product_cost", "g_new"))
    preview, _ = plan(db, data)

    row.principal_name = "g_one"
    row.version = 2
    db.commit()
    with pytest.raises(LedgerError, match="changed since the preview"):
        grant_import.planned_change(db, data, "grants.csv", expected_fingerprint=preview.fingerprint,
                                    reason=REASON, actor=ADMIN)


def test_save_refuses_blocked_or_empty_plans(db):
    seed(db)
    blocked = csv_of(("enterprise_prd", "g_new"), ("enterprise_prd.finance", "g_new"))
    preview, _ = plan(db, blocked)
    with pytest.raises(LedgerError, match="rejected"):
        grant_import.planned_change(db, blocked, "g.csv", expected_fingerprint=preview.fingerprint,
                                    reason=REASON, actor=ADMIN)

    seed(db, kind="catalog", schema="", privileges="USE_CATALOG", principals="g_one", position=2)
    held = csv_of(("enterprise_prd.gold_product_cost", "g_one"))
    preview, _ = plan(db, held)
    with pytest.raises(LedgerError, match="Nothing to save"):
        grant_import.planned_change(db, held, "g.csv", expected_fingerprint=preview.fingerprint,
                                    reason=REASON, actor=ADMIN)
    with pytest.raises(LedgerError, match="Preview the file"):
        grant_import.planned_change(db, held, "g.csv", expected_fingerprint="", reason=REASON, actor=ADMIN)
