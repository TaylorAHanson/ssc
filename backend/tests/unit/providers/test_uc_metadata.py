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


def _status(state):
    return SimpleNamespace(state=SimpleNamespace(value=state), error=None)


class SlowStatementExecution(FakeStatementExecution):
    """Returns every statement still RUNNING from ``execute_statement``, the way
    the real API does once its 50s wait elapses, and finishes it after
    ``polls_needed`` ``get_statement`` calls (never, if ``None``)."""

    def __init__(self, rows_by_table, polls_needed=1, chunk_size=None):
        super().__init__(rows_by_table)
        self.polls_needed = polls_needed
        self.chunk_size = chunk_size
        self.pending = {}
        self.chunks = {}

    def execute_statement(self, statement, warehouse_id, wait_timeout=None):
        finished = super().execute_statement(statement, warehouse_id, wait_timeout)
        statement_id = f"stmt-{len(self.statements)}"
        self.pending[statement_id] = [0, finished.result.data_array]
        return SimpleNamespace(statement_id=statement_id, status=_status("RUNNING"), result=None)

    def _chunk(self, statement_id, rows, index):
        size = self.chunk_size or max(len(rows), 1)
        part = rows[index * size:(index + 1) * size]
        more = (index + 1) * size < len(rows)
        return SimpleNamespace(data_array=part, next_chunk_index=index + 1 if more else None)

    def get_statement(self, statement_id):
        entry = self.pending[statement_id]
        entry[0] += 1
        if self.polls_needed is None or entry[0] < self.polls_needed:
            return SimpleNamespace(statement_id=statement_id, status=_status("RUNNING"), result=None)
        return SimpleNamespace(
            statement_id=statement_id,
            status=_status("SUCCEEDED"),
            result=self._chunk(statement_id, entry[1], 0),
        )

    def get_statement_result_chunk_n(self, statement_id, index):
        return self._chunk(statement_id, self.pending[statement_id][1], index)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.providers.databricks.uc_metadata.time.sleep", lambda _s: None)


def test_statement_still_running_after_the_wait_is_polled_to_completion(no_sleep):
    client = SimpleNamespace(statement_execution=SlowStatementExecution(FULL_ROWS, polls_needed=3))

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert batch.failed_catalogs == {}
    assert batch.not_visible == []
    assert batch.get("main.sales.orders").catalog_description == "Main catalog"


def test_rows_spread_across_result_chunks_are_all_read(no_sleep):
    client = SimpleNamespace(
        statement_execution=SlowStatementExecution(FULL_ROWS, polls_needed=1, chunk_size=1)
    )

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    meta = batch.get("main.sales.orders")
    assert [c.name for c in meta.columns] == ["order_id", "amount"]
    assert meta.tags == {"dataset": "sales-order", "reliability_window": "7-days"}


def test_statement_that_never_finishes_fails_the_catalog(no_sleep, monkeypatch):
    monkeypatch.setattr("app.providers.databricks.uc_metadata._POLL_BUDGET_SECONDS", 0)
    client = SimpleNamespace(statement_execution=SlowStatementExecution(FULL_ROWS, polls_needed=None))

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert "still RUNNING" in batch.failed_catalogs["main"]
    assert batch.not_visible == ["main.sales.orders"]
