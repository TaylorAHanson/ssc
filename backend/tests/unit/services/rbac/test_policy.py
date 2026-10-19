"""Tests for the Access Rules policy module (ADR 0005 D7)."""

import copy
import logging

import pytest

from app.core.config import settings
from app.core.default_config import DEFAULT_CONFIG
from app.models.rbac import RuleRow
from app.services.rbac import layout, policy

UC_DEV = "UC_Securables_RBAC_Dev"
OWNER = "metastore_admins"
DEFAULT_ALLOWED = DEFAULT_CONFIG["rbac"]["allowed_privileges"]
# What the removed never-granted list held by default; the default allow list leaves them out.
OLD_NEVER_GRANTED = {
    "ALL_PRIVILEGES", "MANAGE", "CREATE_CATALOG", "CREATE_EXTERNAL_LOCATION", "CREATE_STORAGE_CREDENTIAL",
    "CREATE_CONNECTION", "CREATE_SHARE", "CREATE_RECIPIENT", "CREATE_PROVIDER", "SET_SHARE_PERMISSION",
}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "sheet_suffix": "Dev", "self_service": True, "write_privileges": True,
         "service_principals": True},
        {"key": "tst", "sheet_suffix": "Tst", "self_service": False, "write_privileges": True,
         "service_principals": False},
        {"key": "prd", "sheet_suffix": "Prd", "self_service": True, "write_privileges": False,
         "service_principals": False},
    ])
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES", copy.deepcopy(DEFAULT_ALLOWED))
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["account users", "users", "admins"])
    monkeypatch.setattr(settings, "RBAC_NAMING_RULES", [])
    monkeypatch.setattr(settings, "RBAC_MIN_JUSTIFICATION_LENGTH", 20)
    monkeypatch.setattr(settings, "RBAC_MIN_REASON_LENGTH", 10)


def row(principals="g_a", privileges="USE_SCHEMA;SELECT", *, kind="schema", catalog="main", schema="sales",
        obj="", ptype="group", env="dev", plane="uc", owner=OWNER, rid="r1", workspace=""):
    sheet = {"uc": f"UC_Securables_RBAC_{(env or 'x').title()}",
             "workspace": f"WS_Securables_RBAC_{(env or 'x').title()}",
             "uc_metastore": "UC_metastore_RBAC"}[plane]
    return RuleRow(id=rid, sheet=sheet, plane=plane, environment=env, environment_value=env or "",
                   securable_type=kind, catalog=catalog, schema_name=schema, object_name=obj,
                   workspace_name=workspace, owner=owner, principal_type=ptype,
                   principal_name=principals, privilege=privileges)


def created(**kw):
    return [(None, row(rid=None, **kw))]


def codes(findings):
    return sorted((f.code, f.severity) for f in findings)


def only(findings, code):
    return [f for f in findings if f.code == code]


# --- severity per rule and path -----------------------------------------------

SEVERITY_CASES = [
    # (id, changes, code, {path: severity})
    ("not-allowed", created(privileges="USE_SCHEMA;MANAGE"), "privilege_not_allowed",
     {"request": "deny", "admin_edit": "deny"}),
    ("all-privileges-with-a-space", created(kind="catalog", schema="", privileges="ALL PRIVILEGES"),
     "privilege_not_allowed", {"request": "deny", "admin_edit": "deny"}),
    ("not-valid-on-the-type", created(kind="volume", obj="raw", privileges="SELECT"), "privilege_not_allowed",
     {"request": "deny", "admin_edit": "deny"}),
    ("deprecated", created(privileges="USAGE"), "deprecated_privilege",
     {"request": "deny", "admin_edit": "deny"}),
    ("user", created(ptype="user", principals="someone@example.com"), "principal_type_not_allowed",
     {"request": "deny", "admin_edit": "deny"}),
    ("unknown-principal-type", created(ptype="role"), "principal_type_not_allowed",
     {"request": "deny", "admin_edit": "deny"}),
    ("system-group-added", created(principals="users"), "system_group",
     {"request": "deny", "admin_edit": "deny"}),
    ("system-group-removed", [(row(principals="users;g_a"), row(principals="g_a"))], "system_group",
     {"request": "deny", "admin_edit": "deny"}),
    ("missing-cells", created(privileges=""), "missing_cells",
     {"request": "deny", "admin_edit": "deny"}),
    ("unknown-securable-type", created(kind="widget"), "unknown_securable_type",
     {"request": "deny", "admin_edit": "warn"}),
    ("type-on-another-types-sheet", created(kind="connection", schema="", privileges="USE_CONNECTION"),
     "securable_type_wrong_sheet", {"request": "deny", "admin_edit": "deny"}),
    ("sp-where-not-allowed", created(ptype="service principal", principals="sp_x", env="prd"),
     "principal_type_not_allowed", {"request": "deny", "admin_edit": "warn"}),
    ("self-service-off", created(env="tst"), "self_service_disabled",
     {"request": "deny", "admin_edit": "warn"}),
    ("write-privileges-off", created(env="prd", privileges="USE_SCHEMA;MODIFY"), "write_privilege_disabled",
     {"request": "deny", "admin_edit": "warn"}),
    ("broad-grant", created(kind="catalog", schema="", privileges="USE_CATALOG;SELECT"), "broad_grant",
     {"request": "warn", "admin_edit": "warn"}),
]


@pytest.mark.parametrize("changes, code, severities",
                         [pytest.param(c, code, s, id=i) for i, c, code, s in SEVERITY_CASES])
@pytest.mark.parametrize("path", ["request", "admin_edit"])
def test_rule_severity_by_path(changes, code, severities, path):
    findings = only(policy.evaluate(changes, path=path), code)
    assert findings, f"expected {code}"
    assert {f.severity for f in findings} == {severities[path]}
    assert all(f.change_index == 0 and f.message for f in findings)


def test_clean_change_has_no_findings():
    assert policy.evaluate(created(), path="request", justification="Quarterly reporting for finance team") == []


# --- what a change adds -------------------------------------------------------

def test_adding_a_principal_to_a_manage_row_flags_only_the_new_atom():
    before = row(principals="sp_a", privileges="MANAGE;USE_CONNECTION", kind="connection", catalog="conn",
                 schema="", plane="uc_metastore", env="dev", ptype="service principal")
    after = before.model_copy(update={"principal_name": "sp_a;sp_b"})
    findings = only(policy.evaluate([(before, after)], path="admin_edit"), "privilege_not_allowed")
    assert [(f.principal, f.privilege) for f in findings] == [("sp_b", "MANAGE")]


def test_unrelated_edit_on_a_manage_row_is_not_judged():
    before = row(privileges="USE_SCHEMA;MANAGE")
    after = before.model_copy(update={"owner": "new_owner"})
    assert policy.evaluate([(before, after)], path="request") == []


def test_moving_a_row_to_another_securable_judges_every_atom():
    before = row(privileges="USE_SCHEMA;MANAGE")
    after = before.model_copy(update={"schema_name": "finance"})
    findings = only(policy.evaluate([(before, after)], path="admin_edit"), "privilege_not_allowed")
    assert [(f.principal, f.privilege) for f in findings] == [("g_a", "MANAGE")]


def test_respelling_a_privilege_is_not_an_addition():
    before = row(kind="catalog", schema="", privileges="ALL PRIVILEGES")
    after = before.model_copy(update={"privilege": "all_privileges"})
    assert policy.evaluate([(before, after)], path="request") == []


@pytest.mark.parametrize("before, after", [
    pytest.param(row(privileges="USE_SCHEMA;MANAGE"), row(privileges="USE_SCHEMA"), id="drop-privilege"),
    pytest.param(row(principals="g_a;g_b", privileges="MANAGE"), row(principals="g_a", privileges="MANAGE"),
                 id="drop-principal"),
    pytest.param(row(privileges="USAGE", ptype="user"), None, id="delete-row"),
    pytest.param(row(env="prd", ptype="service principal", principals="sp_a;sp_b"),
                 row(env="prd", ptype="service principal", principals="sp_a"), id="drop-sp-in-prd"),
])
def test_removals_are_allowed(before, after):
    assert policy.evaluate([(before, after)], path="request") == []


def test_system_group_untouched_by_an_edit_is_fine():
    before = row(principals="users;g_a")
    after = before.model_copy(update={"principal_name": "users;g_a;g_b"})
    assert policy.evaluate([(before, after)], path="request") == []


def test_system_group_row_delete_is_denied_case_insensitively():
    findings = policy.evaluate([(row(principals="Account Users"), None)], path="admin_edit")
    assert [(f.code, f.severity, f.principal) for f in findings] == [("system_group", "deny", "Account Users")]


def test_adding_a_privilege_to_a_system_group_row_is_denied():
    before = row(principals="users", privileges="USE_SCHEMA")
    after = before.model_copy(update={"privilege": "USE_SCHEMA;SELECT"})
    assert codes(policy.evaluate([(before, after)], path="request")) == [("system_group", "deny")]


# --- individual rules ---------------------------------------------------------

def test_deprecated_privilege_hints_and_isnt_also_unknown():
    findings = policy.evaluate(created(privileges="USE_SCHEMA;USAGE"), path="request")
    assert codes(findings) == [("deprecated_privilege", "deny")]
    assert "USE_CATALOG" in findings[0].message and "USE_SCHEMA" in findings[0].message
    finding = policy.evaluate(created(kind="catalog", schema="", privileges="READ_METADATA"), path="request")
    assert "BROWSE" in only(finding, "deprecated_privilege")[0].message


def test_service_principal_allowed_where_environment_allows():
    assert policy.evaluate(created(ptype="service principal", principals="sp_x"), path="request") == []
    assert policy.evaluate(created(ptype="service_principal", principals="sp_x"), path="request") == []


def test_service_principal_needs_an_environment():
    changes = created(ptype="sp", principals="sp_x", kind="connection", catalog="conn", schema="",
                      privileges="USE_CONNECTION", plane="uc_metastore", env=None)
    assert codes(policy.evaluate(changes, path="request")) == [("principal_type_not_allowed", "deny")]


@pytest.mark.parametrize("privilege, flagged", [
    ("MODIFY", True), ("CREATE_TABLE", True), ("APPLY_TAG", True), ("SELECT", False), ("USE_SCHEMA", False),
])
def test_write_privileges_in_a_read_only_environment(privilege, flagged):
    findings = only(policy.evaluate(created(env="prd", privileges=privilege), path="request"),
                    "write_privilege_disabled")
    assert bool(findings) is flagged


@pytest.mark.parametrize("path", ["request", "admin_edit"])
def test_nothing_is_grantable_on_an_unknown_securable_type(path):
    findings = policy.evaluate(created(kind="widget", privileges="ANYTHING"), path=path)
    assert ("privilege_not_allowed", "deny") in codes(findings)
    assert "unknown_securable_type" in {f.code for f in findings}


def test_view_accepts_modify_as_the_workbook_does():
    assert policy.evaluate(created(kind="view", obj="v", privileges="MODIFY"), path="request") == []


@pytest.mark.parametrize("kind, privileges", [
    ("workspace", "USER"),
    ("workspace entitlement", "workspace_access;databricks_sql_access"),
    ("workspace entitlement", "Workspace_Access"),
    ("folder", "CAN_READ"),
    ("service principal", "roles/servicePrincipal.user"),
])
def test_workspace_privileges(kind, privileges):
    changes = created(kind=kind, privileges=privileges, plane="workspace", catalog="", schema="", owner="",
                      workspace="ws-dev")
    assert policy.evaluate(changes, path="request") == []


def test_missing_cells_on_update_only_when_the_change_empties_it():
    broken = row(privileges="")
    edited = broken.model_copy(update={"owner": "other"})
    assert policy.evaluate([(broken, edited)], path="admin_edit") == []
    emptied = row().model_copy(update={"principal_name": " ; "})
    findings = policy.evaluate([(row(), emptied)], path="admin_edit")
    assert [(f.code, f.field) for f in findings] == [("missing_cells", "principal_name")]


@pytest.mark.parametrize("value, flagged", [(None, False), ("too short", True), ("x" * 20, False)])
def test_justification_length(value, flagged):
    findings = policy.evaluate(created(), path="request", justification=value)
    assert [(f.code, f.severity, f.change_index) for f in findings] == (
        [("justification_too_short", "deny", None)] if flagged else []
    )


@pytest.mark.parametrize("path, severity", [("admin_edit", "deny"), ("request", "warn"), ("import", "warn")])
def test_reason_length(path, severity):
    findings = policy.evaluate(created(), path=path, reason=" short ")
    assert [(f.code, f.severity) for f in findings] == [("reason_too_short", severity)]
    assert policy.evaluate(created(), path=path, reason="Cleaning up after the migration") == []


def test_findings_carry_index_sheet_and_rule_id():
    changes = [
        (None, row(rid=None)),
        (row(rid="r7"), row(rid="r7", privileges="USE_SCHEMA;SELECT;MANAGE")),
        (row(rid="r8", principals="admins"), None),
    ]
    findings = policy.evaluate(changes, path="admin_edit")
    assert [(f.code, f.change_index, f.rule_id, f.sheet, f.principal, f.privilege) for f in findings] == [
        ("privilege_not_allowed", 1, "r7", UC_DEV, "g_a", "MANAGE"),
        ("system_group", 2, "r8", UC_DEV, "admins", None),
    ]


# --- naming rules -------------------------------------------------------------

def _rules(monkeypatch, *rules):
    monkeypatch.setattr(settings, "RBAC_NAMING_RULES", list(rules))


GROUP_RULE = {"field": "principal_name", "pattern": r"grp_[a-z]+_{env}", "securable_types": "",
              "exceptions": "", "severity": "deny", "message": "Group names end with _{env}."}


@pytest.mark.parametrize("principal, env, flagged", [
    ("grp_sales_dev", "dev", False),
    ("grp_sales_prd", "dev", True),
    ("grp_sales_prd", "prd", False),
    ("GRP_SALES_DEV", "dev", True),
])
def test_naming_rule_with_env(monkeypatch, principal, env, flagged):
    _rules(monkeypatch, GROUP_RULE)
    findings = only(policy.evaluate(created(principals=principal, env=env), path="request"), "naming_rule")
    assert bool(findings) is flagged
    if flagged:
        assert findings[0].severity == "deny"
        assert findings[0].principal == principal
        assert findings[0].message == f'PrincipalName "{principal}": Group names end with _{env}.'


def test_naming_rule_env_is_escaped(monkeypatch):
    _rules(monkeypatch, {**GROUP_RULE, "pattern": r"grp_{env}"})
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [{"key": "d.v", "sheet_suffix": "Dv"}])
    assert only(policy.evaluate(created(principals="grp_dxv", env="d.v"), path="request"), "naming_rule")
    assert not only(policy.evaluate(created(principals="grp_d.v", env="d.v"), path="request"), "naming_rule")


def test_naming_rule_checks_only_added_principals(monkeypatch):
    _rules(monkeypatch, GROUP_RULE)
    before = row(principals="legacy_name")
    after = before.model_copy(update={"principal_name": "legacy_name;grp_sales_dev;bad_name"})
    findings = only(policy.evaluate([(before, after)], path="request"), "naming_rule")
    assert [f.principal for f in findings] == ["bad_name"]


def test_naming_rule_exceptions_and_warn_severity(monkeypatch):
    _rules(monkeypatch, {**GROUP_RULE, "exceptions": "Data_Platform_Admins, other", "severity": "warn"})
    findings = only(policy.evaluate(created(principals="data_platform_admins;bad"), path="request"), "naming_rule")
    assert [(f.principal, f.severity) for f in findings] == [("bad", "warn")]


def test_naming_rule_securable_types_and_header_field(monkeypatch):
    _rules(monkeypatch, {"field": "ObjectName", "pattern": r"v_\w+", "securable_types": "view, materialized_view",
                         "severity": "deny", "message": ""})
    assert only(policy.evaluate(created(kind="view", obj="fiscal_month", privileges="SELECT"), path="request"),
                "naming_rule")
    assert not only(policy.evaluate(created(kind="view", obj="v_fiscal", privileges="SELECT"), path="request"),
                    "naming_rule")
    assert not only(policy.evaluate(created(kind="table", obj="fiscal", privileges="SELECT"), path="request"),
                    "naming_rule")
    assert only(policy.evaluate(created(kind="materialized view", obj="mv", privileges="SELECT"), path="request"),
                "naming_rule")


def test_naming_rule_checks_only_changed_values(monkeypatch):
    _rules(monkeypatch, {"field": "catalog", "pattern": r".*_{env}", "severity": "deny"})
    before = row(catalog="main")
    added_principal = before.model_copy(update={"principal_name": "g_a;g_b"})
    assert policy.evaluate([(before, added_principal)], path="request") == []
    moved = before.model_copy(update={"catalog": "sales"})
    assert codes(policy.evaluate([(before, moved)], path="request")) == [("naming_rule", "deny")]


def test_invalid_naming_regex_is_skipped_with_a_warning(monkeypatch, caplog):
    _rules(monkeypatch, {**GROUP_RULE, "pattern": "grp_(unclosed"}, GROUP_RULE)
    with caplog.at_level(logging.WARNING, logger="app.services.rbac.policy"):
        findings = policy.evaluate(created(principals="bad"), path="request")
    assert len(only(findings, "naming_rule")) == 1
    assert "invalid pattern" in caplog.text


# --- registry helpers ---------------------------------------------------------

@pytest.mark.parametrize("kind, token, expected", [
    ("schema", "select", "SELECT"),
    ("schema", " use schema ", "USE_SCHEMA"),
    ("catalog", "All Privileges", "ALL_PRIVILEGES"),
    ("workspace entitlement", "Workspace_Access", "workspace_access"),
    ("workspace", "user", "USER"),
    ("service principal", "ROLES/SERVICEPRINCIPAL.USER", "roles/servicePrincipal.user"),
    ("folder", "can_view", "CAN_VIEW"),
    ("volume", "read files", "READ_FILES"),
    ("connection", "use_connection", "USE_CONNECTION"),
    ("widget", "Can_Do", "Can_Do"),
])
def test_canonical_privilege(kind, token, expected):
    assert policy.canonical_privilege(kind, token) == expected


@pytest.mark.parametrize("token, expected", [
    ("modify", True), ("CREATE_SCHEMA", True), ("create_anything_new", True), ("can_manage", True),
    ("IS_OWNER", True), ("all privileges", True), ("SELECT", False), ("CAN_READ", False),
    ("workspace_access", False),
])
def test_is_write_privilege(token, expected):
    assert policy.is_write_privilege(token) is expected


def test_registry_types_are_known_securable_types():
    assert set(policy.PRIVILEGES) <= set(layout.SECURABLE_TYPES)
    for kind in ("catalog", "schema", "table", "view", "volume", "workspace", "workspace entitlement"):
        assert policy.PRIVILEGES[kind]


# --- allow list -----------------------------------------------------------------

def _allow(monkeypatch, **rows):
    """Replace the allow list: ``_allow(monkeypatch, schema="USE_SCHEMA, SELECT")``."""
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES", [
        {"securable_type": kind.replace("_", " "), "privileges": privileges} for kind, privileges in rows.items()
    ])


def test_default_allow_list_keeps_todays_behaviour():
    allowed = policy.allowed_privileges()
    for kind, registry in policy.PRIVILEGES.items():
        expected = {p for p in registry if policy.normalize_privilege(p) not in OLD_NEVER_GRANTED}
        assert set(allowed.get(kind, [])) == expected, kind
    assert "recipient" not in allowed and "provider" not in allowed


def test_a_privilege_added_to_the_allow_list_later_becomes_grantable(monkeypatch):
    _allow(monkeypatch, schema="USE_SCHEMA, SELECT")
    changes = created(privileges="USE_SCHEMA;SELECT;NEW_PRIVILEGE")
    [finding] = policy.evaluate(changes, path="admin_edit")
    assert (finding.code, finding.severity, finding.privilege) == ("privilege_not_allowed", "deny", "NEW_PRIVILEGE")

    _allow(monkeypatch, schema="USE_SCHEMA, SELECT, new privilege")
    assert policy.evaluate(changes, path="request") == []
    assert policy.canonical_privilege("schema", "New_Privilege") == "NEW_PRIVILEGE"


def test_a_new_workspace_permission_takes_the_allow_lists_spelling(monkeypatch):
    _allow(monkeypatch, cluster="CAN_ATTACH_TO, CAN_FOO")
    assert policy.canonical_privilege("cluster", "can_foo") == "CAN_FOO"
    assert policy.canonical_privilege("cluster", "can_bar") == "can_bar"


def test_removing_a_privilege_from_the_allow_list_blocks_new_grants_only(monkeypatch):
    _allow(monkeypatch, schema="USE_SCHEMA")
    assert only(policy.evaluate(created(), path="request"), "privilege_not_allowed")
    before = row(principals="g_a;g_b")
    assert policy.evaluate([(before, before.model_copy(update={"principal_name": "g_a"}))], path="request") == []
    assert policy.evaluate([(before, before.model_copy(update={"owner": "other"}))], path="request") == []


def test_allow_list_matching_is_tolerant(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ALLOWED_PRIVILEGES", [
        {"securable_type": "SCHEMA", "privileges": " use schema ;select"},
        {"securable_type": "workspace_entitlement", "privileges": "WORKSPACE_ACCESS"},
    ])
    assert policy.allowed_privileges() == {
        "schema": ["USE_SCHEMA", "SELECT"], "workspace entitlement": ["workspace_access"],
    }
    assert policy.evaluate(created(privileges="use_schema;Select"), path="request") == []
    changes = created(kind="workspace entitlement", privileges="Workspace_Access", plane="workspace", catalog="",
                      schema="", owner="", workspace="ws-dev")
    assert policy.evaluate(changes, path="request") == []


def test_not_allowed_message_lists_the_types_privileges():
    [finding] = policy.evaluate(created(kind="table", obj="t", privileges="MANAGE"), path="request")
    assert "MANAGE isn't allowed on a table. Allowed on a table: APPLY_TAG, MODIFY, SELECT." in finding.message
    assert "Admin → Settings → Access Rules → Allowed privileges" in finding.message


def test_not_allowed_message_caps_a_long_list(monkeypatch):
    _allow(monkeypatch, schema=", ".join(f"P{i:02d}" for i in range(25)))
    [finding] = policy.evaluate(created(privileges="USE_SCHEMA"), path="request")
    assert "P19 and 5 more." in finding.message and "P20" not in finding.message


def test_a_type_without_a_row_takes_nothing_new(monkeypatch):
    _allow(monkeypatch, schema="USE_SCHEMA, SELECT", external_location="")
    findings = policy.evaluate(created(kind="external location", catalog="loc", schema="", plane="uc_metastore",
                                       privileges="READ_FILES"), path="admin_edit")
    [finding] = only(findings, "privilege_not_allowed")
    assert finding.severity == "deny"
    assert finding.message.startswith("Nothing can be granted on an external location yet")


def test_an_allow_listed_deprecated_token_is_the_admins_call(monkeypatch):
    _allow(monkeypatch, catalog="USE_CATALOG, USAGE")
    assert policy.evaluate(created(kind="catalog", schema="", privileges="USAGE"), path="request") == []


# --- sheet options ----------------------------------------------------------------

def test_sheet_options_follow_the_allow_list_and_environments(monkeypatch):
    _allow(monkeypatch, schema="USE_SCHEMA, select", view="SELECT", connection="USE_CONNECTION",
           workspace="USER, ADMIN", folder="CAN_READ", recipient="")
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "sheet_suffix": "Dev", "service_principals": True,
         "workspaces": "ws-dev, ws-dev-2, WS-DEV", "catalogs": "sales_dev"},
        {"key": "prd", "sheet_suffix": "Prd", "service_principals": False},
    ])

    uc = policy.sheet_options(layout.sheet_spec("UC_Securables_RBAC_Dev"))
    assert uc.model_dump() == {
        "environments": ["dev"],
        "securable_types": ["schema", "view"],
        "principal_types": {"dev": ["group", "service principal"]},
        "privileges": {"schema": ["USE_SCHEMA", "SELECT"], "view": ["SELECT"]},
        "workspaces": [],
        "catalogs": ["sales_dev"],
    }
    ws = policy.sheet_options(layout.sheet_spec("WS_Securables_RBAC_Dev"))
    assert (ws.securable_types, ws.workspaces, ws.catalogs) == (["workspace", "folder"], ["ws-dev", "ws-dev-2"], [])
    assert ws.privileges == {"workspace": ["USER", "ADMIN"], "folder": ["CAN_READ"]}
    prd = policy.sheet_options(layout.sheet_spec("WS_Securables_RBAC_Prd"))
    assert (prd.environments, prd.principal_types, prd.workspaces) == (["prd"], {"prd": ["group"]}, [])
    meta = policy.sheet_options(layout.sheet_spec("UC_metastore_RBAC"))
    assert meta.environments == ["dev", "prd"]
    assert meta.principal_types == {"dev": ["group", "service principal"], "prd": ["group"]}
    assert (meta.securable_types, meta.workspaces, meta.catalogs) == (["connection"], [], [])


# --- configured names -------------------------------------------------------------

def _names(monkeypatch, workspaces="", catalogs=""):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "sheet_suffix": "Dev", "workspaces": workspaces, "catalogs": catalogs},
    ])


def ws_row(kind="workspace", workspace="ws-dev", obj="ws-dev", privileges="USER", **kw):
    return row(kind=kind, workspace=workspace, obj=obj, privileges=privileges, plane="workspace",
               catalog="", schema="", owner="", **kw)


@pytest.mark.parametrize("path, severity", [("request", "deny"), ("admin_edit", "warn")])
def test_a_workspace_outside_the_configured_list(monkeypatch, path, severity):
    _names(monkeypatch, workspaces="ws-dev, ws-dev-2")
    findings = only(policy.evaluate([(None, ws_row(rid=None, workspace="ws-typo", obj="ws-typo"))], path=path),
                    "value_not_in_options")
    assert [(f.field, f.severity, f.principal) for f in findings] == [
        ("workspace_name", severity, "g_a"), ("object_name", severity, "g_a"),
    ]
    assert 'WorkspaceName "ws-typo"' in findings[0].message and "ws-dev, ws-dev-2" in findings[0].message


def test_configured_names_match_case_insensitively(monkeypatch):
    _names(monkeypatch, workspaces="ws-dev", catalogs="Sales_Dev")
    assert policy.evaluate([(None, ws_row(rid=None, workspace="WS-DEV", obj="ws-dev"))], path="request") == []
    assert policy.evaluate(created(catalog="sales_dev"), path="request") == []


def test_securable_name_is_checked_only_on_workspace_types(monkeypatch):
    _names(monkeypatch, workspaces="ws-dev")
    folder = ws_row(rid=None, kind="folder", obj="/Shared/reports", privileges="CAN_READ")
    assert policy.evaluate([(None, folder)], path="request") == []
    entitlement = ws_row(rid=None, kind="workspace entitlement", obj="ws-other", privileges="workspace_access")
    assert [f.field for f in only(policy.evaluate([(None, entitlement)], path="request"),
                                  "value_not_in_options")] == ["object_name"]


def test_catalog_outside_the_configured_list(monkeypatch):
    _names(monkeypatch, catalogs="sales_dev, finance_dev")
    [finding] = only(policy.evaluate(created(catalog="main"), path="request"), "value_not_in_options")
    assert (finding.field, finding.severity) == ("catalog", "deny")
    assert 'Catalog "main"' in finding.message and "sales_dev, finance_dev" in finding.message
    moved = row(catalog="sales_dev").model_copy(update={"catalog": "main"})
    assert only(policy.evaluate([(row(catalog="sales_dev"), moved)], path="request"), "value_not_in_options")


def test_names_already_in_a_row_are_not_judged(monkeypatch):
    _names(monkeypatch, workspaces="ws-dev", catalogs="sales_dev")
    legacy = ws_row(workspace="ws-legacy", obj="ws-legacy")
    assert policy.evaluate([(legacy, legacy.model_copy(update={"principal_name": "g_a;g_b"}))],
                           path="request") == []
    uc_legacy = row(catalog="main")
    assert policy.evaluate([(uc_legacy, uc_legacy.model_copy(update={"principal_name": "g_a;g_b"}))],
                           path="request") == []


def test_blank_lists_leave_names_free(monkeypatch):
    _names(monkeypatch)
    assert policy.evaluate([(None, ws_row(rid=None, workspace="anything", obj="anything"))], path="request") == []
    assert policy.evaluate(created(catalog="anything"), path="request") == []
