"""Catalog scoping for governed data.

Unity Catalog is metastore-global, so ``SCAN_CATALOGS`` — not the workspace the
app runs in — is what keeps one environment's deployment out of another
environment's catalogs. These tests pin both halves of that: the allowlist is
resolved the same way for every discovery path, and it is re-checked at the
certification write so an out-of-scope table can never be tagged.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core import workspaces as ws
from app.providers.databricks.handlers.dataset_handler import DatasetResourceHandler


def _client(catalog_names):
    client = MagicMock()
    client.catalogs.list.return_value = [SimpleNamespace(name=n) for n in catalog_names]
    return client


def _allowlist(monkeypatch, value):
    monkeypatch.setattr(ws.settings, "SCAN_CATALOGS", value, raising=False)


# --- catalogs_to_scan -------------------------------------------------------

def test_blank_allowlist_scans_every_visible_catalog(monkeypatch):
    _allowlist(monkeypatch, "")
    catalogs, missing = ws.catalogs_to_scan(_client(["enterprise_prd", "finance_prd", "system"]))
    assert catalogs == ["enterprise_prd", "finance_prd"]
    assert missing == []


def test_allowlist_wins_over_visible_catalogs(monkeypatch):
    _allowlist(monkeypatch, " enterprise_prd , finance_prd ")
    catalogs, missing = ws.catalogs_to_scan(_client(["enterprise_prd", "finance_prd", "hr_prd"]))
    assert catalogs == ["enterprise_prd", "finance_prd"]
    assert missing == []


def test_configured_but_invisible_catalog_is_reported(monkeypatch):
    """The missing-BROWSE case that made discovery look like 'nothing is tagged'."""
    _allowlist(monkeypatch, "enterprise_prd,finance_prd")
    catalogs, missing = ws.catalogs_to_scan(_client(["finance_prd"]))
    assert catalogs == ["enterprise_prd", "finance_prd"]
    assert missing == ["enterprise_prd"]


def test_catalog_listing_failure_falls_back_to_the_allowlist(monkeypatch):
    _allowlist(monkeypatch, "enterprise_prd")
    client = MagicMock()
    client.catalogs.list.side_effect = PermissionError("no browse")
    catalogs, missing = ws.catalogs_to_scan(client)
    assert catalogs == ["enterprise_prd"]
    assert missing == []


# --- certification write guard ---------------------------------------------

@pytest.mark.parametrize(
    "full_name, expected",
    [
        ("enterprise_stg.sales.orders", True),
        ("`enterprise_stg`.sales.orders", True),
        ("enterprise_prd.sales.orders", False),
    ],
)
def test_certification_respects_the_allowlist(monkeypatch, full_name, expected):
    """A stage deployment must not be able to tag a prod catalog."""
    monkeypatch.setattr(
        "app.providers.databricks.handlers.dataset_handler.get_scan_catalogs",
        lambda: ["enterprise_stg"],
    )
    assert DatasetResourceHandler._catalog_in_scope(full_name) is expected


def test_out_of_scope_table_is_never_tagged(monkeypatch):
    """The guard short-circuits before any ALTER statement is executed."""
    monkeypatch.setattr(
        "app.providers.databricks.handlers.dataset_handler.get_scan_catalogs",
        lambda: ["enterprise_stg"],
    )
    handler = DatasetResourceHandler.__new__(DatasetResourceHandler)
    handler.workspace_client = MagicMock()

    assert handler._apply_certification_tag("enterprise_prd.sales.orders", certified=True) is False
    handler.workspace_client.statement_execution.execute_statement.assert_not_called()


def test_blank_allowlist_still_permits_certification(monkeypatch):
    """Unscoped installs keep working — the risk is logged, not enforced."""
    monkeypatch.setattr(
        "app.providers.databricks.handlers.dataset_handler.get_scan_catalogs",
        lambda: [],
    )
    assert DatasetResourceHandler._catalog_in_scope("anything.at.all") is True
