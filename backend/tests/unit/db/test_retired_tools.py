"""Retired chat tools stay gone, and the two network-reaching tools get a role floor.

The cost, audit-log and over-provisioning tools were removed in favour of Databricks
Genie Code; ``check_object_permissions`` was merged into ``check_resource_access``.
"""
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core import default_config
from app.db.migrate import RETIRED_TOOL_NAMES, delete_retired_tool_rows
from app.db.tool_registry import TOOL_ORIGIN_LOCAL, ToolRegistryModel
from app.services.tool_registry_service import ToolRegistryService

BACKEND = Path(__file__).resolve().parents[3]


def _engine(rows):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tool_registry (id TEXT PRIMARY KEY, tool_name TEXT, origin TEXT)"))
        for rid, name, origin in rows:
            conn.execute(text("INSERT INTO tool_registry VALUES (:i, :n, :o)"), {"i": rid, "n": name, "o": origin})
    return engine


def _names(engine):
    with engine.connect() as conn:
        return sorted((r.tool_name, r.origin) for r in conn.execute(text("SELECT tool_name, origin FROM tool_registry")))


# ------------------------------------------------------------- startup cleanup
def test_cleanup_deletes_retired_local_rows_once_and_logs_a_summary(caplog):
    engine = _engine([
        ("1", "get_cost_summary", "local"),
        ("2", "check_object_permissions", "local"),
        ("3", "search_audit_logs", "workflow"),
        ("4", "search_audit_logs", "mcp"),
        ("5", "check_resource_access", "local"),
    ])

    with caplog.at_level(logging.INFO, logger="app.db.migrate"):
        assert delete_retired_tool_rows(engine) == 3
    assert [r.levelname for r in caplog.records] == ["INFO"]
    assert _names(engine) == [("check_resource_access", "local"), ("search_audit_logs", "mcp")]

    caplog.clear()
    assert delete_retired_tool_rows(engine) == 0
    assert not caplog.records


def test_cleanup_skips_a_database_without_the_registry():
    assert delete_retired_tool_rows(create_engine("sqlite://")) == 0


# ------------------------------------------------------------ nothing points back
def test_retired_tools_are_not_in_the_catalog_or_the_tool_map():
    from app.tools import catalog

    known = {name for name, _tool, _origin in catalog.all_tools()}
    assert not set(RETIRED_TOOL_NAMES) & known
    assert not set(RETIRED_TOOL_NAMES) & set(default_config.DEFAULT_CONFIG["tools"])


def test_prompts_and_eval_cases_do_not_name_retired_tools():
    texts = [BACKEND / "app/agents/prompts.py", *sorted((BACKEND / "app/agents/eval/cases").glob("*.yaml"))]
    for path in texts:
        body = path.read_text()
        named = [n for n in RETIRED_TOOL_NAMES if n in body]
        assert not named, f"{path.name} still names {named}"


# --------------------------------------------------------------- role floors
def _user(*roles):
    from app.models.user import User

    return User(id="u@example.com", email="u@example.com", full_name="U", roles=list(roles))


def _row(db, name):
    return db.query(ToolRegistryModel).filter_by(tool_name=name).one()


@pytest.mark.parametrize("name, roles", [
    ("ping_url", ["Application Admin", "Ops Admin"]),
    ("draft_odps_document", ["Governance Admin"]),
])
def test_network_reaching_tools_are_restricted_even_on_existing_open_rows(db_session, name, roles):
    db_session.add(ToolRegistryModel(
        id=str(uuid.uuid4()), tool_name=name, origin=TOOL_ORIGIN_LOCAL,
        enabled=True, enabled_for_main_agent=True, allowed_roles=[],
    ))
    db_session.commit()

    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session, name).allowed_roles == roles

    visible = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("User"))}
    assert name not in visible


@pytest.mark.parametrize("name", ["ping_url", "draft_odps_document"])
def test_an_admin_choice_of_roles_is_kept(db_session, name):
    db_session.add(ToolRegistryModel(
        id=str(uuid.uuid4()), tool_name=name, origin=TOOL_ORIGIN_LOCAL,
        enabled=True, enabled_for_main_agent=True, allowed_roles=["Security Admin"],
    ))
    db_session.commit()

    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session, name).allowed_roles == ["Security Admin"]


def test_check_resource_access_stays_open_to_everyone(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session, "check_resource_access").allowed_roles == []
