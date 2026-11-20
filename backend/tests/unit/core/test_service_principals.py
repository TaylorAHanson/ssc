"""Named service principals: resolving credentials, the who-uses-what list, and the settings that pick one."""
import base64

import pytest

from app.core import service_principals, settings_store
from app.core.config import _yaml_config, settings

SP = {"name": "rbac-writer", "client_id_key": "w_id", "client_secret_key": "w_secret", "purpose": "exports"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_PRINCIPALS", [dict(SP)])
    monkeypatch.setattr(settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "app-scope")
    monkeypatch.setattr(settings, "RBAC_EXPORT_SERVICE_PRINCIPAL", "")
    monkeypatch.setattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "")
    monkeypatch.setattr(settings, "DATABRICKS_HOST", "https://home.cloud.databricks.com")
    monkeypatch.setitem(_yaml_config, "target_workspaces", [])
    service_principals._secret_cache.clear()
    yield
    service_principals._secret_cache.clear()


class _Secret:
    def __init__(self, value):
        self.value = base64.b64encode(value.encode()).decode()


class FakeSecrets:
    values = {}
    reads = []

    def get_secret(self, scope, key):
        FakeSecrets.reads.append((scope, key))
        if (scope, key) not in FakeSecrets.values:
            raise RuntimeError("RESOURCE_DOES_NOT_EXIST")
        return _Secret(FakeSecrets.values[(scope, key)])


class FakeClient:
    made = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.secrets = FakeSecrets()
        FakeClient.made.append(self)


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeSecrets.values = {("app-scope", "w_id"): "client-123", ("app-scope", "w_secret"): "s3cret"}
    FakeSecrets.reads = []
    FakeClient.made = []
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", FakeClient)
    return FakeClient


def test_credentials_read_both_keys_from_the_scope(fake_sdk):
    assert service_principals.credentials("rbac-writer") == ("client-123", "s3cret")


def test_workspace_client_signs_in_as_the_sp_on_the_home_workspace(fake_sdk):
    client = service_principals.workspace_client("rbac-writer")
    assert client.kwargs == {
        "host": "https://home.cloud.databricks.com", "client_id": "client-123",
        "client_secret": "s3cret", "auth_type": "oauth-m2m",
    }


def test_unknown_name_and_missing_scope_say_what_to_fix(fake_sdk, monkeypatch):
    with pytest.raises(service_principals.ServicePrincipalError, match='no service principal named "nope"'):
        service_principals.credentials("nope")
    monkeypatch.setattr(settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "")
    with pytest.raises(service_principals.ServicePrincipalError, match="No secret scope"):
        service_principals.credentials("rbac-writer")


def test_failed_reads_are_not_cached(fake_sdk):
    del FakeSecrets.values[("app-scope", "w_secret")]
    with pytest.raises(service_principals.ServicePrincipalError, match="READ"):
        service_principals.credentials("rbac-writer")
    FakeSecrets.values[("app-scope", "w_secret")] = "fixed"
    assert service_principals.credentials("rbac-writer") == ("client-123", "fixed")


def test_usage_shows_the_export_sp_and_flags_a_missing_one():
    rows = {r["feature"]: r for r in service_principals.usage()}
    assert rows["Access Rules exports"]["kind"] == "app"

    settings.RBAC_EXPORT_SERVICE_PRINCIPAL = "rbac-writer"
    export = next(r for r in service_principals.usage() if r["feature"] == "Access Rules exports")
    assert export["identity"] == "rbac-writer" and export["ok"]

    settings.RBAC_EXPORT_SERVICE_PRINCIPAL = "gone"
    export = next(r for r in service_principals.usage() if r["feature"] == "Access Rules exports")
    assert not export["ok"] and "gone" in export["problem"]


def test_usage_names_list_entries_used_by_target_workspaces_and_governance(monkeypatch):
    monkeypatch.setitem(_yaml_config, "target_workspaces", [
        {"name": "prod", "host": "https://p", "environment": "prod", "client_id_key": "w_id", "client_secret_key": "w_secret"},
        {"name": "dev", "host": "https://d", "environment": "dev", "client_id_key": "d_id", "client_secret_key": "d_secret"},
        {"name": "sandbox", "host": "https://s", "environment": "dev"},
    ])
    monkeypatch.setattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "prod")
    rows = {r["feature"]: r for r in service_principals.usage()}
    assert rows["Target workspace prod"]["identity"] == "rbac-writer (from the list)"
    assert rows["Target workspace dev"]["kind"] == "target"
    assert rows["Target workspace sandbox"]["kind"] == "app"
    assert rows["Governance"]["identity"].startswith("rbac-writer") and rows["Governance"]["ok"]

    monkeypatch.setattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "missing")
    gov = next(r for r in service_principals.usage() if r["feature"] == "Governance")
    assert not gov["ok"] and gov["kind"] == "app"


# --- Settings: the export SP is picked from the list ------------------------------

def _field(key):
    return next(f for f in settings_store.get_state()["fields"] if f["key"] == key)


def test_export_sp_choices_come_from_the_list():
    assert _field("RBAC_EXPORT_SERVICE_PRINCIPAL")["options"] == ["", "rbac-writer"]
    settings.RBAC_EXPORT_SERVICE_PRINCIPAL = "gone"
    # A saved name that's no longer listed still shows, so the page doesn't silently blank it.
    assert _field("RBAC_EXPORT_SERVICE_PRINCIPAL")["options"] == ["", "rbac-writer", "gone"]


def test_saving_an_unlisted_export_sp_is_refused(db_session):
    with pytest.raises(ValueError, match="Export service principal must be one of"):
        settings_store.set_many(db_session, {"RBAC_EXPORT_SERVICE_PRINCIPAL": "nope"})


def test_an_sp_added_and_picked_in_one_save_is_accepted(db_session):
    new = {"name": "second", "client_id_key": "a", "client_secret_key": "b"}
    settings_store.set_many(db_session, {
        "RBAC_EXPORT_SERVICE_PRINCIPAL": "second",
        "SERVICE_PRINCIPALS": [dict(SP), new],
    })
    assert settings.RBAC_EXPORT_SERVICE_PRINCIPAL == "second"
    assert service_principals.names() == ["rbac-writer", "second"]


def test_a_saved_export_sp_survives_startup_after_its_entry_is_removed():
    field = settings_store._validate_key("RBAC_EXPORT_SERVICE_PRINCIPAL")
    assert settings_store._coerce(field, "gone") == "gone"


def test_service_principal_rows_need_both_keys():
    field = settings_store._validate_key("SERVICE_PRINCIPALS")
    with pytest.raises(ValueError):
        settings_store._coerce_collection(field, [{"name": "x", "client_id_key": "a"}])
