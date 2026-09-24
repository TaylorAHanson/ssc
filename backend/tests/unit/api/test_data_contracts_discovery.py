"""Contract-sync discovery quotes the caller-supplied dataset_id.

``POST /data-contracts/sync?dataset_id=cat.schema.table`` splits the id and
interpolates it into ``FROM <catalog>.information_schema.table_tags`` plus three
string comparisons, so each part must be quoted as an identifier or a literal.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api.v1.data_contracts import discover_dataset_groups


def _run(dataset_id, scan_catalogs=("main",), rows=None):
    provider = MagicMock()
    provider.client.current_user.me.return_value = SimpleNamespace(user_name="sp", display_name="sp")
    provider.client.statement_execution.execute_statement.return_value = SimpleNamespace(
        result=SimpleNamespace(data_array=rows or [])
    )
    with patch("app.core.workspaces.get_governance_uc_provider", return_value=provider), \
            patch("app.core.workspaces.catalogs_to_scan", return_value=(list(scan_catalogs), [])):
        groups = discover_dataset_groups(dataset_id)
    statements = [
        c.kwargs["statement"]
        for c in provider.client.statement_execution.execute_statement.call_args_list
    ]
    return groups, statements


def test_table_lookup_quotes_catalog_and_values():
    groups, statements = _run(
        "main.sales.orders", rows=[["main", "sales", "orders", "orders_ds"]],
    )

    assert groups == {"orders_ds": ["main.sales.orders"]}
    first = statements[0]
    assert "FROM `main`.information_schema.table_tags" in first
    assert "catalog_name = 'main'" in first
    assert "schema_name = 'sales'" in first
    assert "table_name = 'orders'" in first


def test_injection_in_dataset_id_stays_quoted():
    _groups, statements = _run("x`.t; DROP TABLE y; --.s.t'or'1'='1")

    # Four dot-separated parts, so the table lookup is skipped; the tag-value
    # fallback scans the configured catalogs and never interpolates the id.
    assert all("DROP TABLE" not in s for s in statements)

    _groups, statements = _run("cat`--.sch'x.tbl")
    first = statements[0]
    assert "FROM `cat``--`.information_schema.table_tags" in first
    assert "schema_name = 'sch''x'" in first


def test_tag_value_fallback_quotes_scanned_catalog_names():
    _groups, statements = _run("demand-planning", scan_catalogs=("my-catalog",))

    assert statements == [
        "SELECT catalog_name, schema_name, table_name, tag_value FROM `my-catalog`"
        ".information_schema.table_tags WHERE tag_name IN ('dataset', 'data_set')"
    ]
