"""Listing target workspaces reads config only; credentials resolve only on connect."""
import asyncio

import pytest

from app.core import workspaces as ws
from app.services import workspace_cache
from app.tools.self_service.get_target_workspaces import get_target_workspaces as list_tool

TARGETS = {
    "target_workspaces": [
        {
            "name": "prod-a", "host": "https://A.cloud.databricks.com/", "environment": "prod",
            "client_id_key": "sp_a_id", "client_secret_key": "sp_a_secret",
        },
        {
            "name": "enterprise-hub", "host": "https://hub.cloud.databricks.com",
            "environment": "prod", "client_id_key": "sp_hub_id",
            "client_secret_key": "sp_hub_secret",
        },
    ],
}


@pytest.fixture
def secret_reads(monkeypatch):
    """Record every secret read instead of calling Databricks."""
    reads = []

    def read(scope, key):
        reads.append((scope, key))
        return f"value-of-{key}"

    ws._secret_cache.clear()
    monkeypatch.setattr(ws, "_read_secret", read)
    monkeypatch.setattr(ws.settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "install_scope", raising=False)
    yield reads
    ws._secret_cache.clear()


@pytest.fixture
def no_cache(monkeypatch):
    monkeypatch.setattr(workspace_cache, "cached_by_host", lambda: {})


def test_listing_reads_no_secrets(monkeypatch, secret_reads, no_cache):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)

    infos = ws.get_target_workspace_info()

    assert [(i.name, i.environment, i.workspace_type) for i in infos] == [
        ("prod-a", "prod", "domain"), ("enterprise-hub", "prod", "enterprise"),
    ]
    assert not hasattr(infos[0], "client_secret")
    assert secret_reads == []


def test_listing_enriches_from_the_cache_by_normalized_host(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)
    monkeypatch.setattr(workspace_cache, "cached_by_host", lambda: {
        "a.cloud.databricks.com": {"workspace_id": "111", "status": "RUNNING"},
    })

    infos = ws.get_target_workspace_info()

    assert (infos[0].workspace_id, infos[0].status) == ("111", "RUNNING")
    assert (infos[1].workspace_id, infos[1].status) == (None, None)
    assert secret_reads == []


def test_unreadable_cache_still_lists(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(workspace_cache, "cached_by_host", boom)

    assert [i.name for i in ws.get_target_workspace_info()] == ["prod-a", "enterprise-hub"]


def test_enrich_false_skips_the_cache(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)
    monkeypatch.setattr(workspace_cache, "cached_by_host", lambda: pytest.fail("cache read"))

    assert len(ws.get_target_workspace_info(enrich=False)) == 2


def test_no_targets_falls_back_to_the_home_workspace(monkeypatch, secret_reads, no_cache):
    monkeypatch.setattr(ws, "_yaml_config", {})
    monkeypatch.setattr(ws.settings, "DATABRICKS_HOST", "https://home.cloud.databricks.com")
    monkeypatch.setattr(ws.settings, "ENVIRONMENT", "dev")

    infos = ws.get_target_workspace_info()

    assert [(i.name, i.host, i.environment) for i in infos] == [
        ("default", "https://home.cloud.databricks.com", "dev"),
    ]


def test_the_agent_tool_lists_without_reading_secrets(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)
    monkeypatch.setattr(workspace_cache, "cached_by_host", lambda: {
        "hub.cloud.databricks.com": {"workspace_id": "222", "status": "RUNNING"},
    })

    out = asyncio.run(list_tool.execute())

    assert out["count"] == 2
    assert out["workspaces"][0] == {
        "name": "prod-a", "host": "https://A.cloud.databricks.com/", "environment": "prod",
    }
    assert out["workspaces"][1]["workspace_id"] == "222"
    assert secret_reads == []


def test_get_workspace_config_resolves_only_the_match(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)

    by_name = ws.get_workspace_config("enterprise-hub")
    assert by_name.client_id == "value-of-sp_hub_id"
    assert {key for _, key in secret_reads} == {"sp_hub_id", "sp_hub_secret"}

    secret_reads.clear()
    by_host = ws.get_workspace_config("https://A.cloud.databricks.com")
    assert by_host.name == "prod-a"
    assert {key for _, key in secret_reads} == {"sp_a_id", "sp_a_secret"}


def test_full_resolution_still_reads_every_workspace(monkeypatch, secret_reads):
    monkeypatch.setattr(ws, "_yaml_config", TARGETS)

    resolved = ws.get_target_workspaces()

    assert [w.client_secret for w in resolved] == ["value-of-sp_a_secret", "value-of-sp_hub_secret"]
    assert len(secret_reads) == 4
