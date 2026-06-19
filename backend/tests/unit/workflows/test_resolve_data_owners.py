"""Tests for the ``resolve_data_owners`` V2 step tool.

This tool is what lets the data-access workflow be fully data-defined (no
dedicated code graph): it discovers the approver group(s) for the requested
assets so a ``data_owner`` gate's ``approvers_from`` can route the approval.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.tools import resolve_data_owners


def _provider_returning(tags_by_asset, owners_by_asset=None):
    owners_by_asset = owners_by_asset or {}
    provider = AsyncMock()

    async def get_asset_tags(atype, name, keys):
        return tags_by_asset.get(name, {})

    async def get_asset_owner(atype, name):
        return owners_by_asset.get(name)

    provider.get_asset_tags = AsyncMock(side_effect=get_asset_tags)
    provider.get_asset_owner = AsyncMock(side_effect=get_asset_owner)
    return provider


@pytest.mark.asyncio
async def test_passthrough_when_owners_already_known():
    """Pre-supplied owners short-circuit the lookup (no provider call)."""
    with patch("app.workflows.tools._get_databricks_provider") as get_provider:
        res = await resolve_data_owners.execute(
            assets=[{"asset_name": "main.s.t", "asset_type": "table"}],
            data_owners=["grp-x"],
        )
    assert res == {"ok": True, "data_owners": ["grp-x"]}
    get_provider.assert_not_called()


@pytest.mark.asyncio
async def test_resolves_owners_from_approver_group_tag():
    provider = _provider_returning(
        {"main.sales.orders": {"approver_group": "sales-owners"}})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(
            assets=[{"asset_name": "main.sales.orders", "asset_type": "table"}])
    assert res["data_owners"] == ["sales-owners"]


@pytest.mark.asyncio
async def test_falls_back_to_asset_owner_without_tag():
    provider = _provider_returning(
        tags_by_asset={"main.sales.orders": {}},
        owners_by_asset={"main.sales.orders": "alice@corp.com"})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(
            assets=[{"asset_name": "main.sales.orders", "asset_type": "table"}])
    assert res["data_owners"] == ["alice@corp.com"]


@pytest.mark.asyncio
async def test_degrades_gracefully_when_provider_unavailable():
    """A provider error must not break the gate — return what we have."""
    with patch("app.workflows.tools._get_databricks_provider",
               side_effect=RuntimeError("no creds")):
        res = await resolve_data_owners.execute(
            assets=[{"asset_name": "main.s.t", "asset_type": "table"}])
    assert res == {"ok": True, "data_owners": []}


@pytest.mark.asyncio
async def test_deduplicates_and_sorts_multiple_owners():
    provider = _provider_returning({
        "main.a.t": {"approver_group": "team-b"},
        "main.a.u": {"approver_group": "team-a"},
        "main.a.v": {"approver_group": "team-b"},  # duplicate
    })
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(assets=[
            {"asset_name": "main.a.t", "asset_type": "table"},
            {"asset_name": "main.a.u", "asset_type": "table"},
            {"asset_name": "main.a.v", "asset_type": "table"},
        ])
    assert res["data_owners"] == ["team-a", "team-b"]


# --- Resilience to malformed agent params (loose input coercion) -------------
# The agent occasionally emits `assets` as a bare name string or a single dict,
# and sometimes injects a stray `data_owners` string. These must not fail the
# step — we coerce to the structured form and resolve owners anyway.

@pytest.mark.asyncio
async def test_coerces_bare_string_assets_using_asset_type():
    """`assets` as a bare name string + `asset_type` is coerced and resolved."""
    provider = _provider_returning(
        tags_by_asset={},
        owners_by_asset={"atlas_dev_catalog.supply_chain": "owner@corp.com"})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(
            assets="atlas_dev_catalog.supply_chain", asset_type="schema")
    assert res["data_owners"] == ["owner@corp.com"]
    provider.get_asset_owner.assert_awaited_with("schema", "atlas_dev_catalog.supply_chain")


@pytest.mark.asyncio
async def test_falls_back_to_single_asset_name_and_type():
    """When `assets` is absent, the backwards-compat single fields are used."""
    provider = _provider_returning({"main.s.t": {"approver_group": "grp"}})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(asset_name="main.s.t", asset_type="table")
    assert res["data_owners"] == ["grp"]


@pytest.mark.asyncio
async def test_normalizes_single_dict_assets():
    provider = _provider_returning({"main.s.t": {"approver_group": "grp"}})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(
            assets={"asset_name": "main.s.t", "asset_type": "table"})
    assert res["data_owners"] == ["grp"]


@pytest.mark.asyncio
async def test_ignores_stray_string_data_owners_and_resolves_from_assets():
    """A non-list `data_owners` (agent quirk) is not trusted; resolve from assets."""
    provider = _provider_returning({"main.s.t": {"approver_group": "real-owners"}})
    with patch("app.workflows.tools._get_databricks_provider", return_value=provider), \
         patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"):
        res = await resolve_data_owners.execute(
            assets=[{"asset_name": "main.s.t", "asset_type": "table"}],
            data_owners="someone@corp.com")  # stray string, must be ignored
    assert res["data_owners"] == ["real-owners"]
