"""Unit tests for the pluggable IdentityGroupProvider (de-Qualcomm generalization).

The factory selects a backend from ``settings.IDENTITY_PROVIDER`` and defaults to
the vendor-neutral noop provider so the app runs out-of-the-box.
"""
import pytest

from app.providers.identity import get_identity_provider
from app.providers.identity.base import IdentityGroupProvider
from app.providers.identity.noop import NoopIdentityProvider


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    # get_identity_provider is lru_cached; reset around each test so settings
    # overrides take effect and we don't leak a provider between tests.
    get_identity_provider.cache_clear()
    yield
    get_identity_provider.cache_clear()


def test_factory_defaults_to_noop(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "IDENTITY_PROVIDER", "noop", raising=False)
    provider = get_identity_provider()
    assert isinstance(provider, NoopIdentityProvider)
    assert isinstance(provider, IdentityGroupProvider)


def test_unknown_provider_falls_back_to_noop(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "IDENTITY_PROVIDER", "does-not-exist", raising=False)
    assert isinstance(get_identity_provider(), NoopIdentityProvider)


@pytest.mark.asyncio
async def test_noop_provider_records_without_external_calls():
    provider = NoopIdentityProvider()

    added = await provider.list_members_add("grp", ["a@corp.com"], justification="why")
    assert added["group"] == "grp"
    assert added["added"] == ["a@corp.com"]
    assert added["applied"] is False
    assert added["provider"] == "noop"

    removed = await provider.list_members_remove("grp", ["a@corp.com"])
    assert removed["removed"] == ["a@corp.com"]

    members = await provider.list_members_retrieve("grp")
    assert members["group"] == "grp"
    assert members["members"] == []

    groups = await provider.member_retrieve("a@corp.com")
    assert groups["member"] == "a@corp.com"
    assert groups["groups"] == []
