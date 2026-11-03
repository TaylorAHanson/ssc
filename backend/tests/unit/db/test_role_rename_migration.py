"""Platform Admin -> Application Admin: the startup migration over stored role names."""
import json

from sqlalchemy import create_engine, text

from app.db import migrate
from app.db.migrate import rename_platform_admin_role
from app.tools.mcp import McpTool


def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE role_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "external_role TEXT, internal_role TEXT)"
        ))
        conn.execute(text("CREATE TABLE tool_registry (id TEXT PRIMARY KEY, tool_name TEXT, allowed_roles JSON)"))
        conn.execute(text("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value JSON)"))
        conn.execute(text(
            "INSERT INTO role_mappings (external_role, internal_role) VALUES "
            "('admins', 'Platform Admin'), ('owners', 'Platform Admin'), ('owners', 'Super Admin'), "
            "('stewards', 'Governance Admin')"
        ))
        rows = [
            ("1", "custom_tool", ["Platform Admin", "Finance Admin"]),
            ("2", "dup_tool", ["Platform Admin", "Application Admin"]),
            ("3", "publish_workflow", ["Governance Admin"]),
            ("4", "lmws_probe_read", ["Platform Admin"]),
            ("5", "open_tool", []),
            ("6", "get_workflow", ["Finance Admin"]),
        ]
        for rid, name, roles in rows:
            conn.execute(text("INSERT INTO tool_registry VALUES (:id, :n, :r)"),
                         {"id": rid, "n": name, "r": json.dumps(roles)})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "RBAC_VIEWER_ROLES", "v": json.dumps(["platform_admin", "Governance Admin"])})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "EMBEDDED_APPS", "v": json.dumps([{"name": "x", "allowedPersonas": ["Platform Admin"]}])})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "BRAND_NAME", "v": json.dumps("Platform Admin Portal")})
    return engine


def _mappings(engine):
    with engine.connect() as conn:
        return sorted(tuple(r) for r in conn.execute(
            text("SELECT external_role, internal_role FROM role_mappings")).all())


def _tool_roles(engine):
    with engine.connect() as conn:
        return {r.tool_name: json.loads(r.allowed_roles) for r in conn.execute(
            text("SELECT tool_name, allowed_roles FROM tool_registry")).all()}


def _setting(engine, key):
    with engine.connect() as conn:
        return json.loads(conn.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar())


def test_renames_mappings_and_adds_one_super_admin_twin():
    engine = _engine()
    counts = rename_platform_admin_role(engine)

    assert counts["role_mappings"] == 2
    assert counts["super_admin_rows"] == 1
    assert _mappings(engine) == sorted([
        ("admins", "Application Admin"), ("admins", "Super Admin"),
        ("owners", "Application Admin"), ("owners", "Super Admin"),
        ("stewards", "Governance Admin"),
    ])


def test_rewrites_tool_registry_and_widens_untouched_seeds():
    engine = _engine()
    rename_platform_admin_role(engine)
    roles = _tool_roles(engine)

    assert roles["custom_tool"] == ["Application Admin", "Finance Admin"]
    assert roles["dup_tool"] == ["Application Admin"]
    assert roles["publish_workflow"] == ["Application Admin", "Governance Admin"]
    assert roles["lmws_probe_read"] == ["Application Admin", "Ops Admin"]
    assert roles["open_tool"] == []
    # An admin-edited list on a known tool is left as they set it.
    assert roles["get_workflow"] == ["Finance Admin"]


def test_rewrites_role_lists_in_settings_only():
    engine = _engine()
    rename_platform_admin_role(engine)

    assert _setting(engine, "RBAC_VIEWER_ROLES") == ["Application Admin", "Governance Admin"]
    assert _setting(engine, "EMBEDDED_APPS") == [{"name": "x", "allowedPersonas": ["Application Admin"]}]
    assert _setting(engine, "BRAND_NAME") == "Platform Admin Portal"


def test_second_run_is_a_no_op():
    engine = _engine()
    rename_platform_admin_role(engine)
    before = (_mappings(engine), _tool_roles(engine))

    counts = rename_platform_admin_role(engine)

    assert not any(counts.values())
    assert (_mappings(engine), _tool_roles(engine)) == before


def test_no_widening_without_a_rename():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM role_mappings WHERE internal_role = 'Platform Admin'"))

    rename_platform_admin_role(engine)

    assert _tool_roles(engine)["publish_workflow"] == ["Governance Admin"]


def test_missing_tables_are_skipped():
    assert not any(rename_platform_admin_role(create_engine("sqlite://")).values())


def _tools_declaring(capability_id, *modules):
    names = set()
    for module in modules:
        for value in vars(module).values():
            if isinstance(value, McpTool) and value.required_capability == capability_id:
                names.add(value.name)
    return names


def test_widened_tool_lists_match_declared_capabilities():
    from app.tools.authoring import workflow_authoring
    from app.tools.self_service import lmws_n2k_probe

    assert set(migrate._AUTHORING_TOOLS) == _tools_declaring("workflows.author", workflow_authoring)
    assert set(migrate._PROBE_TOOLS) == _tools_declaring("platform.probe", lmws_n2k_probe)
