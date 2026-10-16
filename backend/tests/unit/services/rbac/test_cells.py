"""Tests for packed-cell atoms, prerequisites and the workbook-style row-edit planner (ADR 0005 D1)."""

import pytest

from app.core.config import settings
from app.models.rbac import CANONICAL_FIELDS, AccessItem, RuleRow
from app.services.rbac import cells, layout

UC_DEV = "UC_Securables_RBAC_Dev"
WS_DEV = "WS_Securables_RBAC_Dev"
META = "UC_metastore_RBAC"
OWNER = "metastore_admins"


@pytest.fixture(autouse=True)
def _environments(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "aliases": "development", "sheet_suffix": "Dev"},
        {"key": "prd", "aliases": "prod", "sheet_suffix": "Prd"},
    ])
    monkeypatch.setattr(settings, "RBAC_UC_SHEET_PATTERN", "UC_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_WS_SHEET_PATTERN", "WS_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_METASTORE_SHEET", META)


def uc(rid, kind, catalog, schema, obj, principals, privileges, *, position=0, owner=OWNER,
       ptype="group", env="dev", sheet=UC_DEV):
    return RuleRow(
        id=rid, sheet=sheet, plane="uc", environment=env, position=position, version=3,
        environment_value=env, securable_type=kind, catalog=catalog, schema_name=schema, object_name=obj,
        owner=owner, principal_type=ptype, principal_name=principals, privilege=privileges,
    )


def ws(rid, kind, workspace, obj, principals, privileges, *, position=0, ptype="group"):
    return RuleRow(
        id=rid, sheet=WS_DEV, plane="workspace", environment="dev", position=position,
        environment_value="dev", securable_type=kind, workspace_name=workspace, object_name=obj,
        principal_type=ptype, principal_name=principals, privilege=privileges,
    )


def meta(rid, kind, name, principals, privileges, *, position=0, ptype="group", env=None):
    return RuleRow(
        id=rid, sheet=META, plane="uc_metastore", environment=env, position=position,
        securable_type=kind, catalog=name, owner=OWNER, principal_type=ptype,
        principal_name=principals, privilege=privileges,
    )


def grant(principal, privileges, kind="schema", catalog="main", schema="sales", obj="", **kw):
    return AccessItem(action="grant", environment=kw.pop("environment", "dev"), securable_type=kind,
                      catalog=catalog, schema_name=schema, object_name=obj, principal_name=principal,
                      privileges=privileges, **kw)


def revoke(principal, privileges, **kw):
    return grant(principal, privileges, **kw).model_copy(update={"action": "revoke"})


def _sheet(item):
    plane = cells.item_plane(item)
    spec = layout.sheet_for(plane, layout.resolve_environment(item.environment))
    return spec.name if spec else None


def plan(rows, items, owner=OWNER):
    return cells.plan_edits(
        rows, items,
        sheet_for_item=_sheet,
        owner_for_item=lambda item: owner,
        environment_value_for_item=lambda item: layout.resolve_environment(item.environment) or "",
    )


def summary(diffs):
    return [(d.op, d.rule_id, (d.after or {}).get("principal_name"), (d.after or {}).get("privilege"))
            for d in diffs]


# Base UC sheet: a catalog row, two schema rows with different sets, a view row.
R_CAT = uc("r0", "catalog", "main", "", "", "g_a;g_b;g_c", "USE_CATALOG", position=0)
R_SEL = uc("r1", "schema", "main", "sales", "", "g_a;g_b", "USE_SCHEMA;SELECT", position=1)
R_MOD = uc("r2", "schema", "main", "sales", "", "g_c", "USE_SCHEMA;SELECT;MODIFY", position=2)
R_VIEW = uc("r3", "view", "main", "sales", "fiscal_month", "g_a", "SELECT", position=3)
BASE = [R_CAT, R_SEL, R_MOD, R_VIEW]


# --- split / join / atoms -----------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (" a; b;;c ;", ["a", "b", "c"]),
    ("", []),
    (None, []),
    ("USE_SCHEMA;SELECT", ["USE_SCHEMA", "SELECT"]),
    (";;", []),
])
def test_split_cell(value, expected):
    assert cells.split_cell(value) == expected


def test_join_cell_uses_no_spaces_and_drops_empties():
    assert cells.join_cell([" a ", "", "b"]) == "a;b"


def test_atoms_is_the_cartesian_product():
    row = uc("x", "schema", "main", "sales", "", "g_a; g_b", "USE_SCHEMA;SELECT")
    assert [(a.principal, a.privilege) for a in cells.atoms(row)] == [
        ("g_a", "USE_SCHEMA"), ("g_a", "SELECT"), ("g_b", "USE_SCHEMA"), ("g_b", "SELECT"),
    ]


@pytest.mark.parametrize("value, expected", [
    ("group", "group"), ("Group", "group"), ("service principal", "service principal"),
    ("service_principal", "service principal"), ("ServicePrincipal", "service principal"),
    ("sp", "service principal"), ("user", "user"), ("role", "role"), ("", ""),
])
def test_principal_type_key(value, expected):
    assert cells.principal_type_key(value) == expected


# --- plan_edits: table-driven -------------------------------------------------

PLAN_CASES = [
    pytest.param(
        BASE, [grant("g_new", ["USE_SCHEMA", "SELECT"])],
        [("update", "r1", "g_a;g_b;g_new", "USE_SCHEMA;SELECT")], ["change"],
        id="grant-appends-to-exact-set-row",
    ),
    pytest.param(
        BASE, [grant("g_d", ["USE_SCHEMA", "MODIFY"])],
        [("create", None, "g_d", "USE_SCHEMA;MODIFY")], ["change"],
        id="grant-new-row-when-no-exact-set",
    ),
    pytest.param(
        BASE, [grant("g_a", ["USE_SCHEMA", "SELECT"])],
        [], ["noop"],
        id="grant-already-held-is-noop",
    ),
    pytest.param(
        BASE, [grant("g_c", ["SELECT"])],
        [], ["noop"],
        id="grant-held-through-a-wider-row-is-noop",
    ),
    pytest.param(
        BASE, [grant("g_a", ["USE_SCHEMA", "SELECT", "MODIFY"])],
        [("update", "r2", "g_c;g_a", "USE_SCHEMA;SELECT;MODIFY")], ["change"],
        id="partially-held-grant-appends-with-full-set",
    ),
    pytest.param(
        BASE, [revoke("g_b", ["USE_SCHEMA", "SELECT"])],
        [("update", "r1", "g_a", "USE_SCHEMA;SELECT")], ["change"],
        id="whole-revoke-removes-principal",
    ),
    pytest.param(
        BASE, [revoke("g_b", ["SELECT"])],
        [("update", "r1", "g_a", "USE_SCHEMA;SELECT")], ["change"],
        id="revoke-leaving-only-prerequisites-drops-principal",
    ),
    pytest.param(
        BASE, [revoke("g_c", ["MODIFY"])],
        [("delete", "r2", None, None), ("update", "r1", "g_a;g_b;g_c", "USE_SCHEMA;SELECT")], ["change"],
        id="partial-revoke-regrants-remainder",
    ),
    pytest.param(
        BASE, [revoke("g_a", ["SELECT"], kind="view", obj="fiscal_month")],
        [("delete", "r3", None, None)], ["change"],
        id="row-emptied-is-deleted",
    ),
    pytest.param(
        BASE, [revoke("g_z", ["SELECT"])],
        [], ["noop"],
        id="revoke-not-held-is-noop",
    ),
    pytest.param(
        BASE, [revoke("g_a", ["MODIFY"])],
        [], ["noop"],
        id="revoke-of-privilege-not-in-any-of-its-rows-is-noop",
    ),
    pytest.param(
        BASE, [grant("g_x", ["USE_SCHEMA", "SELECT"]), grant("g_y", ["USE_SCHEMA", "SELECT"])],
        [("update", "r1", "g_a;g_b;g_x;g_y", "USE_SCHEMA;SELECT")], ["change", "change"],
        id="two-grants-on-one-row-merge",
    ),
    pytest.param(
        BASE, [grant("g_x", ["USE_SCHEMA", "MODIFY"]), grant("g_y", ["MODIFY", "USE_SCHEMA"])],
        [("create", None, "g_x;g_y", "USE_SCHEMA;MODIFY")], ["change", "change"],
        id="later-grant-appends-to-created-row",
    ),
    pytest.param(
        BASE, [grant("g_x", ["USE_SCHEMA", "SELECT"]), revoke("g_a", ["USE_SCHEMA", "SELECT"])],
        [("update", "r1", "g_b;g_x", "USE_SCHEMA;SELECT")], ["change", "change"],
        id="grant-and-revoke-on-one-row-merge",
    ),
    pytest.param(
        BASE, [grant("g_x", ["SELECT"]), grant("g_x", ["SELECT"])],
        [("create", None, "g_x", "SELECT")], ["change", "noop"],
        id="repeat-grant-in-same-batch-is-noop",
    ),
    pytest.param(
        BASE, [revoke("g_a", ["SELECT"], kind="view", obj="fiscal_month"),
               grant("g_b", ["SELECT"], kind="view", obj="fiscal_month")],
        [("update", "r3", "g_b", "SELECT")], ["change", "change"],
        id="emptied-row-takes-a-later-grant",
    ),
    pytest.param(
        BASE, [grant("g_x", ["USE_SCHEMA", "MODIFY"]), revoke("g_x", ["MODIFY"])],
        [], ["change", "change"],
        id="created-then-emptied-row-has-no-diff",
    ),
    pytest.param(
        BASE, [grant("g_x", ["USE_SCHEMA", "SELECT"]), revoke("g_x", ["USE_SCHEMA", "SELECT"])],
        [], ["change", "change"],
        id="row-edited-back-to-original-has-no-diff",
    ),
    pytest.param(
        [uc("r9", "schema", "main", "sales", "", "g_a", "USE_SCHEMA;SELECT", owner="other_owner")],
        [grant("g_b", ["USE_SCHEMA", "SELECT"])],
        [("create", None, "g_b", "USE_SCHEMA;SELECT")], ["change"],
        id="owner-mismatch-makes-a-new-row",
    ),
    pytest.param(
        [uc("r9", "schema", "main", "sales", "", "sp_a", "USE_SCHEMA;SELECT", ptype="service principal")],
        [grant("g_b", ["USE_SCHEMA", "SELECT"])],
        [("create", None, "g_b", "USE_SCHEMA;SELECT")], ["change"],
        id="principal-type-mismatch-makes-a-new-row",
    ),
    pytest.param(
        [uc("r9", "schema", "main", "sales", "", "g_a", "USE_SCHEMA;SELECT", env="prd",
            sheet="UC_Securables_RBAC_Prd")],
        [grant("g_b", ["USE_SCHEMA", "SELECT"])],
        [("create", None, "g_b", "USE_SCHEMA;SELECT")], ["change"],
        id="rows-on-other-sheets-are-ignored",
    ),
]


@pytest.mark.parametrize("rows, items, expected, statuses", PLAN_CASES)
def test_plan_edits(rows, items, expected, statuses):
    diffs, outcomes = plan(rows, items)
    assert summary(diffs) == expected
    assert [o.status for o in outcomes] == statuses
    assert [o.index for o in outcomes] == list(range(len(items)))
    assert all(o.reason for o in outcomes)


def test_noop_reasons_say_why():
    _, outcomes = plan(BASE, [grant("g_a", ["SELECT"]), revoke("g_z", ["SELECT"])])
    assert outcomes[0].reason == "g_a already holds SELECT on schema main.sales"
    assert outcomes[1].reason == "g_z doesn't hold SELECT on schema main.sales"


def test_partial_revoke_keeps_the_rest_and_says_so():
    _, outcomes = plan(BASE, [revoke("g_c", ["MODIFY"])])
    assert "keeps USE_SCHEMA;SELECT" in outcomes[0].reason


def test_partial_revoke_regrant_is_noop_when_another_row_covers_it():
    rows = BASE + [uc("r4", "schema", "main", "sales", "", "g_c", "USE_SCHEMA;SELECT", position=4)]
    diffs, _ = plan(rows, [revoke("g_c", ["MODIFY"])])
    assert summary(diffs) == [("delete", "r2", None, None)]


def test_regrant_uses_the_source_rows_owner():
    rows = [
        uc("r1", "schema", "main", "sales", "", "g_a", "USE_SCHEMA;SELECT;MODIFY", owner="legacy_owner"),
        uc("r2", "schema", "main", "sales", "", "g_b", "USE_SCHEMA;SELECT", owner="legacy_owner", position=1),
    ]
    diffs, _ = plan(rows, [revoke("g_a", ["MODIFY"])], owner="new_default_owner")
    assert summary(diffs) == [("delete", "r1", None, None), ("update", "r2", "g_b;g_a", "USE_SCHEMA;SELECT")]


def test_revoke_from_several_rows():
    rows = BASE + [uc("r4", "schema", "main", "sales", "", "g_b;g_d", "USE_SCHEMA;SELECT;APPLY_TAG", position=4)]
    diffs, outcomes = plan(rows, [revoke("g_b", ["SELECT"])])
    assert summary(diffs) == [
        ("update", "r1", "g_a", "USE_SCHEMA;SELECT"),
        ("update", "r4", "g_d", "USE_SCHEMA;SELECT;APPLY_TAG"),
        ("create", None, "g_b", "USE_SCHEMA;APPLY_TAG"),
    ]
    assert "2 rows" in outcomes[0].reason


def test_case_insensitive_matching_preserves_existing_spelling():
    rows = [uc("r1", "Schema", "Main", "Sales", "", "G_A;g_b", "use_schema;select")]
    diffs, outcomes = plan(rows, [
        grant("g_a", ["USE_SCHEMA", "SELECT"]),
        grant("g_New", ["Select", " use_schema "]),
        revoke("G_B", ["SELECT"]),
    ])
    assert [o.status for o in outcomes] == ["noop", "change", "change"]
    assert summary(diffs) == [("update", "r1", "G_A;g_New", "use_schema;select")]


def test_append_and_remove_keep_the_rest_of_the_cell_as_written():
    rows = [uc("r1", "schema", "main", "sales", "", "g_a; g_b ;g_c;", "USE_SCHEMA;SELECT")]
    diffs, _ = plan(rows, [grant("g_d", ["USE_SCHEMA", "SELECT"])])
    assert diffs[0].after["principal_name"] == "g_a; g_b ;g_c;g_d"
    diffs, _ = plan(rows, [revoke("g_b", ["SELECT"])])
    assert diffs[0].after["principal_name"] == "g_a;g_c"


def test_diff_snapshots():
    rows = [uc("r1", "schema", "main", "sales", "", "g_a", "USE_SCHEMA;SELECT", position=7)]
    rows[0].extra = {"9": "note"}
    diffs, _ = plan(rows, [grant("g_b", ["USE_SCHEMA", "SELECT"]), grant("g_c", ["MODIFY"])])
    update, create = diffs
    assert update.before == {**{f: getattr(rows[0], f) for f in CANONICAL_FIELDS},
                             "extra": {"9": "note"}, "position": 7, "version": 3}
    assert update.after["principal_name"] == "g_a;g_b"
    assert (update.after["position"], update.after["version"]) == (7, 3)
    assert (update.sheet, update.plane, update.environment) == (UC_DEV, "uc", "dev")
    assert rows[0].principal_name == "g_a"  # inputs are never mutated
    assert create.before is None and create.rule_id is None
    assert create.after == {
        "environment_value": "dev", "securable_type": "schema", "catalog": "main", "schema_name": "sales",
        "object_name": "", "workspace_name": "", "owner": OWNER, "principal_type": "group",
        "principal_name": "g_c", "privilege": "MODIFY", "extra": {},
    }
    assert (create.sheet, create.plane, create.environment) == (UC_DEV, "uc", "dev")


def test_delete_snapshot_has_no_after():
    diffs, _ = plan(BASE, [revoke("g_a", ["SELECT"], kind="view", obj="fiscal_month")])
    assert diffs[0].after is None
    assert diffs[0].before["object_name"] == "fiscal_month"


def test_diffs_are_ordered_by_first_touch():
    diffs, _ = plan(BASE, [
        grant("g_x", ["SELECT"], kind="view", obj="fiscal_month"),
        grant("g_x", ["USE_SCHEMA", "SELECT"]),
        grant("g_y", ["SELECT"], kind="view", obj="fiscal_month"),
    ])
    assert [d.rule_id for d in diffs] == ["r3", "r1"]
    assert diffs[0].after["principal_name"] == "g_a;g_x;g_y"


def test_duplicate_exact_rows_pick_the_first_by_position():
    rows = [
        uc("late", "schema", "main", "sales", "", "g_a", "USE_SCHEMA;SELECT", position=9),
        uc("early", "schema", "main", "sales", "", "g_b", "USE_SCHEMA;SELECT", position=2),
    ]
    diffs, _ = plan(rows, [grant("g_c", ["USE_SCHEMA", "SELECT"])])
    assert summary(diffs) == [("update", "early", "g_b;g_c", "USE_SCHEMA;SELECT")]


def test_new_service_principal_row_uses_workbook_spelling():
    diffs, _ = plan([], [grant("sp_x", ["USE_SCHEMA", "SELECT"], principal_type="service_principal")])
    assert diffs[0].after["principal_type"] == "service principal"


def test_missing_sheet_raises():
    with pytest.raises(ValueError):
        cells.plan_edits([], [grant("g_a", ["SELECT"])], sheet_for_item=lambda item: None,
                         owner_for_item=lambda item: OWNER, environment_value_for_item=lambda item: "dev")


def test_blank_principal_or_privileges_is_noop():
    _, outcomes = plan([], [grant(" ", ["SELECT"]), grant("g_a", [" ; "])])
    assert [(o.status, o.reason) for o in outcomes] == [
        ("noop", "no principal given"), ("noop", "no privileges given"),
    ]


# --- workspace and metastore sheets --------------------------------------------

WS_ROWS = [
    ws("w1", "workspace", "ws-dev", "", "g_a", "USER", position=1),
    ws("w2", "workspace entitlement", "ws-dev", "", "g_a", "workspace_access;databricks_sql_access", position=2),
]


def ws_grant(principal, privileges, kind="workspace", obj="", action="grant"):
    return AccessItem(action=action, environment="dev", securable_type=kind, workspace_name="ws-dev",
                      object_name=obj, principal_name=principal, privileges=privileges)


def test_ws_grant_appends_and_keeps_lowercase_entitlements():
    diffs, _ = plan(WS_ROWS, [ws_grant("g_b", ["Workspace_Access", "DATABRICKS_SQL_ACCESS"],
                                       kind="workspace entitlement")])
    assert summary(diffs) == [("update", "w2", "g_a;g_b", "workspace_access;databricks_sql_access")]


def test_ws_rows_ignore_owner():
    rows = [ws("w1", "workspace", "ws-dev", "", "g_a", "USER")]
    diffs, _ = plan(rows, [ws_grant("g_b", ["USER"]), ws_grant("g_c", ["ADMIN"])], owner=OWNER)
    assert summary(diffs) == [("update", "w1", "g_a;g_b", "USER"), ("create", None, "g_c", "ADMIN")]
    assert diffs[1].after["owner"] == ""
    assert diffs[1].after["workspace_name"] == "ws-dev"
    assert (diffs[1].sheet, diffs[1].plane, diffs[1].environment) == (WS_DEV, "workspace", "dev")


def test_ws_partial_revoke_regrants_remainder():
    diffs, _ = plan(WS_ROWS, [ws_grant("g_a", ["databricks_sql_access"], kind="workspace entitlement",
                                       action="revoke")])
    assert summary(diffs) == [("delete", "w2", None, None), ("create", None, "g_a", "workspace_access")]


def test_ws_folder_securable_is_matched_by_name():
    rows = [ws("w1", "folder", "ws-dev", "/Shared/a", "g_a", "CAN_READ")]
    diffs, _ = plan(rows, [ws_grant("g_b", ["CAN_READ"], kind="folder", obj="/Shared/b")])
    assert summary(diffs) == [("create", None, "g_b", "CAN_READ")]


META_ROWS = [meta("m1", "connection", "conn_sales_prd", "sp_a;sp_b", "MANAGE;USE_CONNECTION",
                  ptype="service principal", env="prd")]


def meta_grant(principal, privileges, environment=None, **kw):
    return AccessItem(action="grant", environment=environment, securable_type="connection",
                      catalog="conn_sales_prd", principal_name=principal, privileges=privileges, **kw)


def test_metastore_grant_creates_row_with_item_environment():
    diffs, _ = plan(META_ROWS, [meta_grant("g_a", ["USE_CONNECTION"], environment="prod")])
    assert summary(diffs) == [("create", None, "g_a", "USE_CONNECTION")]
    assert (diffs[0].sheet, diffs[0].plane, diffs[0].environment) == (META, "uc_metastore", "prd")
    assert diffs[0].after["catalog"] == "conn_sales_prd"
    assert diffs[0].after["schema_name"] == "" and diffs[0].after["object_name"] == ""


def test_metastore_grant_without_environment():
    diffs, _ = plan(META_ROWS, [meta_grant("g_a", ["USE_CONNECTION"])])
    assert diffs[0].environment is None


def test_metastore_appends_to_matching_service_principal_row():
    diffs, _ = plan(META_ROWS, [meta_grant("sp_c", ["use_connection", "manage"],
                                           principal_type="service principal")])
    assert summary(diffs) == [("update", "m1", "sp_a;sp_b;sp_c", "MANAGE;USE_CONNECTION")]


# --- prerequisites ------------------------------------------------------------

def _shape(items):
    return [(i.securable_type, i.catalog, i.schema_name, i.object_name, i.principal_name, i.privileges)
            for i in items]


PREREQ_CASES = [
    pytest.param(
        [], [grant("g_n", ["SELECT"])],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("schema", "main", "sales", "", "g_n", ["USE_SCHEMA", "SELECT"])],
        1, id="schema-grant-gets-use-schema-in-set-and-use-catalog-item",
    ),
    pytest.param(
        [R_CAT], [grant("g_a", ["SELECT"])],
        [("schema", "main", "sales", "", "g_a", ["USE_SCHEMA", "SELECT"])],
        0, id="use-catalog-already-held",
    ),
    pytest.param(
        [], [grant("g_n", ["use_schema", "SELECT"])],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("schema", "main", "sales", "", "g_n", ["use_schema", "SELECT"])],
        1, id="use-schema-present-case-insensitively",
    ),
    pytest.param(
        [], [grant("g_n", ["SELECT"], kind="view", obj="fiscal_month")],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("schema", "main", "sales", "", "g_n", ["USE_SCHEMA"]),
         ("view", "main", "sales", "fiscal_month", "g_n", ["SELECT"])],
        2, id="view-grant-adds-schema-and-catalog-items",
    ),
    pytest.param(
        BASE, [grant("g_b", ["SELECT"], kind="view", obj="fiscal_month")],
        [("view", "main", "sales", "fiscal_month", "g_b", ["SELECT"])],
        0, id="view-grant-prerequisites-already-held",
    ),
    pytest.param(
        [R_CAT, uc("rx", "catalog", "main", "", "", "g_d", "USE_CATALOG;USE_SCHEMA")],
        [grant("g_d", ["SELECT"], kind="table", obj="orders")],
        [("table", "main", "sales", "orders", "g_d", ["SELECT"])],
        0, id="catalog-level-use-schema-covers-the-schema",
    ),
    pytest.param(
        [], [grant("g_n", ["SELECT"], kind="view", obj="v1"), grant("g_n", ["SELECT"], kind="volume", obj="vol")],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("schema", "main", "sales", "", "g_n", ["USE_SCHEMA"]),
         ("view", "main", "sales", "v1", "g_n", ["SELECT"]),
         ("volume", "main", "sales", "vol", "g_n", ["SELECT"])],
        2, id="shared-prerequisites-are-added-once",
    ),
    pytest.param(
        [], [grant("g_n", ["SELECT"], kind="view", obj="v1"), grant("g_n", ["USE_SCHEMA", "MODIFY"])],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("view", "main", "sales", "v1", "g_n", ["SELECT"]),
         ("schema", "main", "sales", "", "g_n", ["USE_SCHEMA", "MODIFY"])],
        1, id="a-schema-grant-in-the-batch-covers-use-schema",
    ),
    pytest.param(
        [], [grant("g_n", ["SELECT"], kind="catalog", schema="")],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG", "SELECT"])],
        0, id="catalog-grant-gets-use-catalog-in-set",
    ),
    pytest.param(
        [uc("p1", "catalog", "main", "", "", "g_n", "USE_CATALOG", env="prd", sheet="UC_Securables_RBAC_Prd")],
        [grant("g_n", ["USE_SCHEMA", "SELECT"])],
        [("catalog", "main", "", "", "g_n", ["USE_CATALOG"]),
         ("schema", "main", "sales", "", "g_n", ["USE_SCHEMA", "SELECT"])],
        1, id="other-environments-dont-count",
    ),
    pytest.param(
        [], [revoke("g_n", ["SELECT"], kind="view", obj="v1")],
        [("view", "main", "sales", "v1", "g_n", ["SELECT"])],
        0, id="revokes-are-untouched",
    ),
    pytest.param(
        [], [ws_grant("g_n", ["USER"]), meta_grant("g_n", ["USE_CONNECTION"])],
        [("workspace", "", "", "", "g_n", ["USER"]),
         ("connection", "conn_sales_prd", "", "", "g_n", ["USE_CONNECTION"])],
        0, id="non-uc-planes-are-untouched",
    ),
]


@pytest.mark.parametrize("rows, items, expected, added", PREREQ_CASES)
def test_add_prerequisites(rows, items, expected, added):
    out, new = cells.add_prerequisites(items, rows)
    assert _shape(out) == expected
    assert len(new) == added
    assert all(i in out for i in new)
    assert all(i.plane == "uc" and i.action == "grant" and i.environment == "dev" for i in new)


def test_prerequisites_then_plan():
    items, added = cells.add_prerequisites([grant("g_n", ["SELECT"], kind="view", obj="fiscal_month")], BASE)
    diffs, outcomes = plan(BASE, items)
    assert summary(diffs) == [
        ("update", "r0", "g_a;g_b;g_c;g_n", "USE_CATALOG"),
        ("create", None, "g_n", "USE_SCHEMA"),
        ("update", "r3", "g_a;g_n", "SELECT"),
    ]
    assert [o.status for o in outcomes] == ["change", "change", "change"]
