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


@pytest.mark.parametrize("configured,fqn_key,enforced", [
    ([], "c", False),
    ([], "c.s.t::col", False),
    (["*"], "c", True),
    (["*"], "c.s.t::col", True),
    (["c.pilot"], "c.pilot", True),
    (["c.pilot"], "C.Pilot.Orders::Email", True),
    (["c.pilot"], "c.pilot_old.t", False),
    (["c.pilot"], "c.other.t", False),
    (["c.pilot"], "c", False),
    (["c.pilot"], "d.pilot.t", False),
    (["c.*_raw"], "c.sales_raw.t", True),
    ("c.a, c.b", "c.b.t", True),
])
def test_enforces(monkeypatch, configured, fqn_key, enforced):
    monkeypatch.setattr(settings, "METADATA_ENFORCE_SCHEMAS", configured)
    assert scope.enforces(fqn_key) is enforced


def test_catalog_of_handles_every_level():
    assert [scope.catalog_of(k) for k in ("c", "c.s", "c.s.t", "c.s.t::col")] == ["c"] * 4


def test_enforce_blocked_reason_only_when_enforce_has_no_list(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "enforce")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    monkeypatch.setattr(settings, "METADATA_ENFORCE_SCHEMAS", ["*"])
    assert "Catalogs to reconcile is blank" in scope.enforce_blocked_reason()
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*"])
    assert scope.enforce_blocked_reason() is None
    monkeypatch.setattr(settings, "METADATA_ENFORCE_SCHEMAS", [])
    assert "Enforce only in these schemas is blank" in scope.enforce_blocked_reason()
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    assert scope.enforce_blocked_reason() is None


def test_a_cleared_schema_list_turns_enforce_into_report_only(monkeypatch):
    from app.services.metadata import reconciler

    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "enforce")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["c"])
    monkeypatch.setattr(settings, "METADATA_ENFORCE_SCHEMAS", ["c.pilot"])
    assert reconciler.current_mode() == "enforce"
    monkeypatch.setattr(settings, "METADATA_ENFORCE_SCHEMAS", [])
    assert reconciler.current_mode() == "plan"


@pytest.mark.parametrize("value,ok", [
    ("*", True), ("sales_dev.bronze", True), ("sales_dev.*_raw", True), ("*_dev.bronze", True),
    ("sales_dev", False), ("sales_dev.bronze.orders", False), ("sales_dev.", False), (".bronze", False),
    ("sales dev.bronze", False), ("sales_dev.`x`", False),
])
def test_schema_list_entries_must_be_catalog_dot_schema(value, ok):
    assert (scope.schema_pattern_problem(value) is None) is ok


def test_saving_a_bad_schema_entry_is_refused():
    from app.core import settings_store

    field = settings_store._validate_key("METADATA_ENFORCE_SCHEMAS")
    assert settings_store._coerce(field, ["sales_dev.bronze", "*"]) == ["sales_dev.bronze", "*"]
    with pytest.raises(ValueError, match="isn't catalog.schema"):
        settings_store._coerce(field, ["sales_dev"])


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
