"""The catalog list (METADATA_RECONCILE_CATALOGS) as the deployment's write boundary."""
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.metadata import authz, observed, scope
from app.services.metadata.types import Actor, ObjectKey

ADMIN = Actor(id="admin@example.com", is_admin=True)


@pytest.mark.parametrize("configured,catalog,managed", [
    ([], "anything", True),
    (["sales_dev"], "Sales_Dev", True),
    (["sales_dev"], "sales_prd", False),
    (["*_dev"], "finance_dev", True),
    (["*_dev"], "finance_dev_old", False),
    (["*"], "prod", True),
    ("sales_dev, hr_dev", "hr_dev", True),
    (["sales_dev"], "", False),
])
def test_manages(monkeypatch, configured, catalog, managed):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", configured)
    assert scope.manages(catalog) is managed


def test_catalog_of_handles_every_level():
    assert [scope.catalog_of(k) for k in ("c", "c.s", "c.s.t", "c.s.t::col")] == ["c"] * 4


def test_enforce_blocked_reason_only_when_enforce_has_no_list(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "enforce")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    assert "Catalogs to reconcile is blank" in scope.enforce_blocked_reason()
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*"])
    assert scope.enforce_blocked_reason() is None
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    assert scope.enforce_blocked_reason() is None


def test_outside_message_lists_a_few_names(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*_dev"])
    message = scope.outside_message([f"c{i}" for i in range(7)])
    assert "matching *_dev" in message and "c4 and 2 more" in message


def _visible(names):
    return SimpleNamespace(client=SimpleNamespace(catalogs=SimpleNamespace(
        list=lambda include_browse=None: [SimpleNamespace(name=n, catalog_type=None) for n in names])))


def test_scope_catalogs_expands_patterns_against_visible_catalogs(monkeypatch):
    provider = _visible(["sales_dev", "hr_dev", "sales_prd", "system"])
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*_dev", "extra"])
    assert observed.scope_catalogs(provider) == ["extra", "sales_dev", "hr_dev"]

    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*"])
    assert observed.scope_catalogs(provider) == ["sales_dev", "hr_dev", "sales_prd"]


def test_plain_names_need_no_catalog_listing(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["Sales_Dev"])
    assert observed.scope_catalogs(provider=object()) == ["sales_dev"]


def test_the_object_drawer_says_why_an_unmanaged_catalog_isnt_editable(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["sales_dev"])

    ok, reasons = authz.is_editable(None, ADMIN, ObjectKey.parse("sales_prd.bronze.orders"))

    assert ok is False and "manages only catalogs matching sales_dev" in reasons[0]
