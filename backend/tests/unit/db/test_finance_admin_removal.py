"""The retired Finance Admin role: the startup migration over stored data."""
import json

from sqlalchemy import create_engine, text

from app.db.migrate import remove_finance_admin_role


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
            "CREATE TABLE approvals (id TEXT PRIMARY KEY, approval_type TEXT, status TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO role_mappings (external_role, internal_role) VALUES "
            "('finance_admin', 'Finance Admin'), ('cfo-team', 'finance_admin'), "
            "('stewards', 'Governance Admin')"
        ))
        for rid, name, roles in [
            ("1", "shared_tool", ["Application Admin", "Finance Admin"]),
            ("2", "finance_only", ["Finance Admin"]),
            ("3", "open_tool", []),
        ]:
            conn.execute(text("INSERT INTO tool_registry VALUES (:id, :n, :r)"),
                         {"id": rid, "n": name, "r": json.dumps(roles)})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "RBAC_VIEWER_ROLES", "v": json.dumps(["Finance Admin", "Governance Admin"])})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "EMBEDDED_APPS", "v": json.dumps([{"id": "x", "allowed_personas": ["finance_admin"]}])})
        conn.execute(text("INSERT INTO app_settings VALUES (:k, :v)"),
                     {"k": "BRAND_NAME", "v": json.dumps("Finance Admin Portal")})
        conn.execute(text(
            "INSERT INTO approvals VALUES ('a1', 'finance_admin', 'pending'), "
            "('a2', 'finance_admin', 'approved'), ('a3', 'security_admin', 'pending')"
        ))
    return engine


def _rows(engine, sql):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql)).all()]


def _setting(engine, key):
    with engine.connect() as conn:
        return json.loads(conn.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar())


def test_mappings_to_the_role_are_deleted_in_any_spelling():
    engine = _engine()
    assert remove_finance_admin_role(engine)["role_mappings"] == 2
    assert _rows(engine, "SELECT external_role, internal_role FROM role_mappings") == [
        ("stewards", "Governance Admin")]


def test_a_tool_only_finance_admins_could_use_is_not_opened_to_everyone():
    engine = _engine()
    remove_finance_admin_role(engine)
    roles = {name: json.loads(r) for name, r in _rows(engine, "SELECT tool_name, allowed_roles FROM tool_registry")}
    assert roles["shared_tool"] == ["Application Admin"]
    assert roles["finance_only"] == ["Super Admin"]
    assert roles["open_tool"] == []


def test_role_lists_in_settings_drop_it_and_other_text_is_left_alone():
    engine = _engine()
    remove_finance_admin_role(engine)
    assert _setting(engine, "RBAC_VIEWER_ROLES") == ["Governance Admin"]
    assert _setting(engine, "EMBEDDED_APPS") == [{"id": "x", "allowed_personas": []}]
    assert _setting(engine, "BRAND_NAME") == "Finance Admin Portal"


def test_pending_steps_move_to_application_admins_and_history_is_kept():
    engine = _engine()
    assert remove_finance_admin_role(engine)["approvals"] == 1
    assert sorted(_rows(engine, "SELECT id, approval_type FROM approvals")) == [
        ("a1", "platform_admin"), ("a2", "finance_admin"), ("a3", "security_admin")]


def test_second_run_is_a_no_op():
    engine = _engine()
    remove_finance_admin_role(engine)
    assert not any(remove_finance_admin_role(engine).values())
