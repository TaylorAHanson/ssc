"""Tests for ``resolve_owner_groups_from_assets``.

It backs a gate's ``approver: {"source": "approver_group_tag"}``: the gate is
routed to the group named in each asset's UC ``approver_group`` tag, falling back
to the asset owner unless the gate turns that off.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.tools import resolve_owner_groups_from_assets


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


def _patched(provider):
    return (
        patch("app.workflows.tools._get_databricks_provider", return_value=provider),
        patch("app.core.config.settings.APPROVER_GROUP_TAG_KEY", "approver_group"),
    )


@pytest.mark.asyncio
async def test_resolves_owners_from_approver_group_tag():
    provider = _provider_returning({"main.sales.orders": {"approver_group": "sales-owners"}})
    p1, p2 = _patched(provider)
    with p1, p2:
        owners = await resolve_owner_groups_from_assets(
            [{"asset_name": "main.sales.orders", "asset_type": "table"}])
    assert owners == ["sales-owners"]


@pytest.mark.asyncio
async def test_falls_back_to_asset_owner_without_tag():
    provider = _provider_returning(
        tags_by_asset={"main.sales.orders": {}},
        owners_by_asset={"main.sales.orders": "alice@corp.com"})
    p1, p2 = _patched(provider)
    with p1, p2:
        owners = await resolve_owner_groups_from_assets(
            [{"asset_name": "main.sales.orders", "asset_type": "table"}])
    assert owners == ["alice@corp.com"]


@pytest.mark.asyncio
async def test_no_owner_fallback_when_the_gate_turns_it_off():
    provider = _provider_returning(
        tags_by_asset={"main.sales.orders": {}},
        owners_by_asset={"main.sales.orders": "alice@corp.com"})
    p1, p2 = _patched(provider)
    with p1, p2:
        owners = await resolve_owner_groups_from_assets(
            [{"asset_name": "main.sales.orders", "asset_type": "table"}],
            fallback_to_owner=False)
    assert owners == []
    provider.get_asset_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_degrades_gracefully_when_provider_unavailable():
    """A provider error must not break the gate — return what we have."""
    with patch("app.workflows.tools._get_databricks_provider",
               side_effect=RuntimeError("no creds")):
        owners = await resolve_owner_groups_from_assets(
            [{"asset_name": "main.s.t", "asset_type": "table"}])
    assert owners == []


@pytest.mark.asyncio
async def test_deduplicates_and_sorts_multiple_owners():
    provider = _provider_returning({
        "main.a.t": {"approver_group": "team-b"},
        "main.a.u": {"approver_group": "team-a"},
        "main.a.v": {"approver_group": "team-b"},  # duplicate
    })
    p1, p2 = _patched(provider)
    with p1, p2:
        owners = await resolve_owner_groups_from_assets([
            {"asset_name": "main.a.t", "asset_type": "table"},
            {"asset_name": "main.a.u", "asset_type": "table"},
            {"asset_name": "main.a.v", "asset_type": "table"},
        ])
    assert owners == ["team-a", "team-b"]


@pytest.mark.asyncio
async def test_no_assets_skips_the_lookup():
    with patch("app.workflows.tools._get_databricks_provider") as get_provider:
        assert await resolve_owner_groups_from_assets([]) == []
    get_provider.assert_not_called()
