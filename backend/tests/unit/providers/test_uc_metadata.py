"""Tests for the BROWSE-only Unity Catalog metadata reader.

Column names and result shapes here were validated against a live metastore, so
the fake below mirrors the real ``information_schema`` layout rather than a
guess at it.
"""
from types import SimpleNamespace

import pytest

from app.providers.databricks.uc_metadata import fetch_uc_metadata

WAREHOUSE = "wh-1"


class FakeStatementExecution:
    """Answers information_schema queries from in-memory rows.

    Dispatches on the table being selected from, which keeps the fake honest
    about *which* query produced which rows without parsing real SQL.
    """

    def __init__(self, rows_by_table, fail_with=None):
        self.rows_by_table = rows_by_table
        self.fail_with = fail_with
        self.statements = []

    def execute_statement(self, statement, warehouse_id, wait_timeout=None):
        self.statements.append(statement)
        if self.fail_with:
            raise self.fail_with
        for table, rows in self.rows_by_table.items():
            if f"information_schema.{table} " in statement + " ":
                return SimpleNamespace(
                    status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None),
                    result=SimpleNamespace(data_array=rows),
                )
        return SimpleNamespace(
            status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None),
            result=SimpleNamespace(data_array=[]),
        )


def make_client(rows_by_table=None, fail_with=None):
    return SimpleNamespace(
        statement_execution=FakeStatementExecution(rows_by_table or {}, fail_with)
    )


FULL_ROWS = {
    "tables": [["sales", "orders", "MANAGED", "Order facts"]],
    "columns": [
        ["sales", "orders", "order_id", "bigint", "Primary key"],
        ["sales", "orders", "amount", "decimal(10,2)", None],
    ],
    "table_tags": [
        ["sales", "orders", "dataset", "sales-order"],
        ["sales", "orders", "reliability_window", "7-days"],
    ],
    "schemata": [["sales", "Sales schema"]],
    "catalogs": [["main", "Main catalog"]],
}


def test_reads_every_field_the_checklist_needs():
    client = make_client(FULL_ROWS)

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    meta = batch.get("main.sales.orders")
    assert meta is not None
    assert meta.table_type == "MANAGED"
    assert meta.is_view is False
    assert meta.comment == "Order facts"
    assert meta.catalog_description == "Main catalog"
    assert meta.schema_description == "Sales schema"
    assert meta.tags == {"dataset": "sales-order", "reliability_window": "7-days"}
    assert [c.name for c in meta.columns] == ["order_id", "amount"]
    assert meta.columns[1].data_type == "decimal(10,2)"
    assert meta.missing_column_descriptions == ["amount"]
    assert batch.not_visible == []


def test_never_calls_the_select_gated_sdk_metadata_apis():
    """The whole point of this module: metadata must come from information_schema
    so BROWSE suffices. A client with no tables/catalogs/schemas attributes would
    raise if the reader reached for tables.get."""
    client = make_client(FULL_ROWS)

    fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert not hasattr(client, "tables")
    assert all(
        "information_schema" in s for s in client.statement_execution.statements
    )


def test_table_absent_from_information_schema_is_reported_not_visible():
    client = make_client({"tables": []})

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert batch.get("main.sales.orders") is None
    assert batch.not_visible == ["main.sales.orders"]
    assert batch.failed_catalogs == {}


def test_query_failure_is_distinguished_from_an_empty_result():
    """A failed catalog is a louder problem than an invisible table, and the
    caller degrades differently for each, so they must not be conflated."""
    client = make_client(fail_with=RuntimeError("warehouse unavailable"))

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert "main" in batch.failed_catalogs
    assert batch.not_visible == ["main.sales.orders"]


def test_identifiers_are_matched_case_insensitively():
    """information_schema lowercases stored identifiers, so a contract written
    with mixed case must still match."""
    client = make_client(FULL_ROWS)

    batch = fetch_uc_metadata(client, ["MAIN.Sales.Orders"], WAREHOUSE)

    assert batch.get("main.sales.orders") is not None
    assert batch.not_visible == []


def test_single_quotes_in_names_cannot_break_out_of_the_literal():
    client = make_client({"tables": []})

    fetch_uc_metadata(client, ["main.sales.o'rders"], WAREHOUSE)

    for statement in client.statement_execution.statements:
        assert "o''rders" in statement or "o'rders" not in statement


def test_malformed_names_are_rejected_without_querying():
    client = make_client(FULL_ROWS)

    batch = fetch_uc_metadata(client, ["not_a_full_name"], WAREHOUSE)

    assert batch.not_visible == ["not_a_full_name"]
    assert client.statement_execution.statements == []


def test_tables_are_batched_per_catalog_not_per_table():
    """Five queries per catalog regardless of table count — the old code made
    several SDK round-trips per table."""
    client = make_client(FULL_ROWS)

    fetch_uc_metadata(
        client,
        ["main.sales.orders", "main.sales.customers", "main.ops.events"],
        WAREHOUSE,
    )

    assert len(client.statement_execution.statements) == 5


def test_missing_warehouse_id_fails_loudly():
    client = make_client(FULL_ROWS)

    with pytest.raises(ValueError, match="warehouse"):
        fetch_uc_metadata(client, ["main.sales.orders"], "")
