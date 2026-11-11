"""The Workspaces page list: the workspaces a person's groups hold in the Access Rules."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.db.base import Base
from app.db.rbac import RbacRuleModel
from app.db.workspace_cache import CachedWorkspaceModel
from app.services import my_workspaces
from app.services.rbac import audit, layout

WS_DEV = "WS_Securables_RBAC_Dev"
WS_PRD = "WS_Securables_RBAC_Prd"


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


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "aliases": "development", "sheet_suffix": "Dev"},
        {"key": "prd", "aliases": "prod", "sheet_suffix": "Prd"},
    ])
    monkeypatch.setattr(settings, "RBAC_WS_SHEET_PATTERN", "WS_Securables_RBAC_{suffix}")
    monkeypatch.setattr(settings, "RBAC_SYSTEM_GROUPS", ["account users", "users"])
    monkeypatch.setattr(settings, "RBAC_WORKSPACE_ADDRESSES", [])
    monkeypatch.setattr("app.core.workspaces.get_target_workspace_info", lambda enrich=True: [])


def seed(db, *, workspace, principals, privileges="USER", kind="workspace", sheet=WS_DEV, status="active"):
    spec = layout.sheet_spec(sheet)
    cells = dict(
        environment_value=spec.environment or "", securable_type=kind, catalog="", schema_name="",
        object_name="", workspace_name=workspace, owner="", principal_type="group",
        principal_name=principals, privilege=privileges,
    )
    db.add(RbacRuleModel(
        id=audit.new_id(), sheet=sheet, plane=spec.plane, environment=spec.environment,
        position=db.query(RbacRuleModel).count(), extra={}, status=status, version=1,
        row_hash=audit.compute_row_hash(sheet, cells, {}), source="import", **cells,
    ))
    db.commit()


def cache(db, name, url):
    db.add(CachedWorkspaceModel(workspace_id=name, workspace_name=name, workspace_url=url,
                                status="RUNNING", synced_at=datetime.utcnow()))
    db.commit()


def test_groups_drop_the_users_email_and_built_in_groups():
    groups = my_workspaces.user_groups(["me@example.com", "Users", "g_analysts", "g_analysts"], "ME@example.com")
    assert groups == ["g_analysts"]


def test_only_workspaces_held_by_the_users_groups_are_listed(db):
    seed(db, workspace="ws-sales", principals="g_analysts;g_engineers")
    seed(db, workspace="ws-finance", principals="g_finance")
    seed(db, workspace="ws-sales", principals="g_analysts", kind="workspace entitlement",
         privileges="workspace_access;databricks_sql_access")
    cache(db, "ws-sales", "fevm-sales.cloud.databricks.com/")

    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["name"] == "ws-sales"
    assert ws["groups"] == ["g_analysts"]
    assert ws["privileges"] == ["USER", "workspace_access", "databricks_sql_access"]
    assert ws["host"] == "https://fevm-sales.cloud.databricks.com"
    assert ws["lakehouse_url"] == "https://fevm-sales.cloud.databricks.com"
    assert ws["genie_one_url"] == "https://fevm-sales.cloud.databricks.com/one"
    assert ws["is_admin"] is False


def test_a_partial_group_name_does_not_count(db):
    seed(db, workspace="ws-sales", principals="g_analysts_eu")
    assert my_workspaces.workspaces_for(db, ["g_analysts"]) == []


def test_folders_and_deleted_rules_are_not_workspaces(db):
    seed(db, workspace="ws-sales", principals="g_analysts", kind="folder")
    seed(db, workspace="ws-old", principals="g_analysts", status="deleted")
    assert my_workspaces.workspaces_for(db, ["g_analysts"]) == []


def test_one_entry_per_workspace_across_environments_with_admin_flag(db):
    seed(db, workspace="ws-shared", principals="g_admins", privileges="ADMIN")
    seed(db, workspace="ws-shared", principals="g_analysts", sheet=WS_PRD)
    [ws] = my_workspaces.workspaces_for(db, ["g_admins", "g_analysts"])
    assert ws["environments"] == ["dev", "prd"]
    assert ws["is_admin"] is True


def test_a_workspace_without_a_known_address_is_listed_without_links(db):
    seed(db, workspace="ws-unknown", principals="g_analysts")
    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["host"] == "" and ws["genie_one_url"] == "" and ws["lakehouse_url"] == ""


def test_target_workspaces_supply_an_address_the_cache_lacks(db, monkeypatch):
    from app.core.workspaces import TargetWorkspaceInfo

    monkeypatch.setattr(
        "app.core.workspaces.get_target_workspace_info",
        lambda enrich=True: [TargetWorkspaceInfo(name="WS-Sales", host="https://sales.example.com", environment="dev")],
    )
    seed(db, workspace="ws-sales", principals="g_analysts")
    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["genie_one_url"] == "https://sales.example.com/one"


def test_no_groups_means_no_workspaces(db):
    seed(db, workspace="ws-sales", principals="g_analysts")
    assert my_workspaces.workspaces_for(db, []) == []


def _user(*groups):
    from app.models.user import User

    return User(id="me@example.com", email="me@example.com", full_name="Me", roles=[],
                entitlements=["me@example.com", *groups], is_active=True)


def test_endpoint_lists_the_callers_workspaces(db, monkeypatch):
    from app.api.v1 import workspaces as api

    monkeypatch.setattr(api, "is_feature_enabled", lambda name: True)
    seed(db, workspace="ws-sales", principals="g_analysts")
    body = api.list_my_workspaces(db=db, current_user=_user("g_analysts"))
    assert [w["name"] for w in body["workspaces"]] == ["ws-sales"]
    assert body["access_rules_enabled"] is True and body["groups_known"] is True
    assert body["default_environment"] == "prd"


def test_endpoint_only_built_in_groups_still_counts_as_known(db, monkeypatch):
    from app.api.v1 import workspaces as api

    monkeypatch.setattr(api, "is_feature_enabled", lambda name: True)
    body = api.list_my_workspaces(db=db, current_user=_user("users"))
    assert body["workspaces"] == [] and body["groups_known"] is True
    assert api.list_my_workspaces(db=db, current_user=_user())["groups_known"] is False


def test_endpoint_reports_when_access_rules_are_off(db, monkeypatch):
    from app.api.v1 import workspaces as api

    monkeypatch.setattr(api, "is_feature_enabled", lambda name: False)
    seed(db, workspace="ws-sales", principals="g_analysts")
    body = api.list_my_workspaces(db=db, current_user=_user("g_analysts"))
    assert body["workspaces"] == [] and body["access_rules_enabled"] is False


def test_names_match_ignoring_case_and_separators(db):
    seed(db, workspace="ControlTower_WS_Dev", principals="g_analysts")
    cache(db, "controltower-ws-dev", "https://adb-111.azuredatabricks.net")
    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["host"] == "https://adb-111.azuredatabricks.net"


def test_a_workspace_url_subdomain_counts_as_its_name(db):
    seed(db, workspace="fevm_sales", principals="g_analysts")
    cache(db, "Sales (production)", "https://fevm-sales.cloud.databricks.com")
    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["host"] == "https://fevm-sales.cloud.databricks.com"


def test_admin_addresses_win_over_the_workspace_list(db, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_WORKSPACE_ADDRESSES", [
        {"name": "controltower_ws_dev", "url": "ct-dev.cloud.databricks.com/"},
    ])
    seed(db, workspace="controltower_ws_dev", principals="g_analysts")
    cache(db, "controltower_ws_dev", "https://somewhere-else.cloud.databricks.com")
    [ws] = my_workspaces.workspaces_for(db, ["g_analysts"])
    assert ws["genie_one_url"] == "https://ct-dev.cloud.databricks.com/one"


def test_environment_options_start_on_production(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [
        {"key": "dev", "aliases": "development"},
        {"key": "live", "aliases": "prod, production"},
    ])
    opts = my_workspaces.environment_options()
    assert [e["key"] for e in opts["environments"]] == ["dev", "live"]
    assert opts["default_environment"] == "live"
    monkeypatch.setattr(settings, "RBAC_ENVIRONMENTS", [{"key": "dev"}])
    assert my_workspaces.environment_options()["default_environment"] == ""
