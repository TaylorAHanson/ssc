"""Tests for the BROWSE-only Unity Catalog metadata reader.

Column names and result shapes here were validated against a live metastore, so
the fake below mirrors the real ``information_schema`` layout rather than a
guess at it.
"""
import json
import threading
import time
import traceback
from types import SimpleNamespace

import pytest
import requests

from app.providers.databricks.uc_metadata import (
    ExternalLinksUnavailable, ResultTooLarge, _run_statement, fetch_uc_metadata, statement_result,
)

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

    def execute_statement(self, statement, warehouse_id, wait_timeout=None, disposition=None, format=None):
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
        self.cancelled = []

    def execute_statement(self, statement, warehouse_id, wait_timeout=None, disposition=None, format=None):
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

    def cancel_execution(self, statement_id):
        self.cancelled.append(statement_id)


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


def test_a_labelled_slow_statement_logs_that_it_is_still_running(no_sleep, monkeypatch, caplog):
    from app.providers.databricks.uc_metadata import _run_statement

    monkeypatch.setattr("app.providers.databricks.uc_metadata._PROGRESS_EVERY_SECONDS", 0)
    client = SimpleNamespace(statement_execution=SlowStatementExecution(FULL_ROWS, polls_needed=2))

    with caplog.at_level("INFO", logger="app.providers.databricks.uc_metadata"):
        _run_statement(client, "SELECT 1 FROM system.information_schema.tables ", WAREHOUSE, label="main relations")
        _run_statement(client, "SELECT 1 FROM system.information_schema.tables ", WAREHOUSE)

    progress = [r.getMessage() for r in caplog.records if "still RUNNING" in r.getMessage()]
    assert progress and all(m.startswith("main relations: statement stmt-1 still RUNNING") for m in progress)


def test_statement_that_never_finishes_fails_the_catalog(no_sleep, monkeypatch):
    monkeypatch.setattr("app.providers.databricks.uc_metadata._POLL_BUDGET_SECONDS", 0)
    client = SimpleNamespace(statement_execution=SlowStatementExecution(FULL_ROWS, polls_needed=None))

    batch = fetch_uc_metadata(client, ["main.sales.orders"], WAREHOUSE)

    assert "still RUNNING" in batch.failed_catalogs["main"]
    assert batch.not_visible == ["main.sales.orders"]
    assert client.statement_execution.cancelled == ["stmt-1"]


# --- complete results, and EXTERNAL_LINKS ------------------------------------------------

SQL = "SELECT 1 FROM system.information_schema.columns "


def _succeeded(result, total=None, truncated=None):
    return SimpleNamespace(
        statement_id="stmt-1", status=_status("SUCCEEDED"), result=result,
        manifest=SimpleNamespace(total_row_count=total, truncated=truncated),
    )


def _answering(response):
    return SimpleNamespace(statement_execution=SimpleNamespace(execute_statement=lambda **kwargs: response))


def test_a_result_shorter_than_its_manifest_raises():
    rows = SimpleNamespace(data_array=[["a"], ["b"]], next_chunk_index=None)

    assert _run_statement(_answering(_succeeded(rows, total=2)), SQL, WAREHOUSE) == [["a"], ["b"]]
    with pytest.raises(RuntimeError, match=r"has 3 row\(s\) but only 2 were read"):
        _run_statement(_answering(_succeeded(rows, total=3)), SQL, WAREHOUSE)
    with pytest.raises(RuntimeError, match="truncated"):
        _run_statement(_answering(_succeeded(rows, total=2, truncated=True)), SQL, WAREHOUSE)


class LinkStatements:
    """EXTERNAL_LINKS answers: each chunk is a presigned link, and the next chunk's index rides on the link."""

    def __init__(self, chunks, total=None, headers=None):
        self.chunks = chunks
        self.total = sum(len(c) for c in chunks) if total is None else total
        self.headers = headers or {}
        self.calls = []

    def _chunk(self, index):
        more = index + 1 < len(self.chunks)
        return SimpleNamespace(data_array=None, next_chunk_index=None, external_links=[SimpleNamespace(
            chunk_index=index, external_link=f"https://bucket.s3.amazonaws.com/results/{index}?X-Amz-Signature=secret",
            http_headers=dict(self.headers), next_chunk_index=index + 1 if more else None,
        )])

    def execute_statement(self, **kwargs):
        self.calls.append(kwargs)
        return _succeeded(self._chunk(0), total=self.total, truncated=False)

    def get_statement_result_chunk_n(self, statement_id, index):
        return self._chunk(index)


class FakeStorage:
    """``requests.get`` for the links: the first chunk answers last, and every request is recorded."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.failure = None
        self.requests = []
        self.in_flight = self.max_in_flight = 0
        self._lock = threading.Lock()

    def get(self, url, headers=None, timeout=None, **kwargs):
        index = int(url.split("/results/")[1].split("?")[0])
        with self._lock:
            self.requests.append({"headers": dict(headers or {}), "timeout": timeout, **kwargs})
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(0.02 * (len(self.chunks) - index))
        finally:
            with self._lock:
                self.in_flight -= 1
        if self.failure == "connection":
            raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
        if self.failure == "forbidden":
            return SimpleNamespace(ok=False, status_code=403, json=lambda: {"Code": "AccessDenied"})
        if self.failure == "not_json":
            return SimpleNamespace(ok=True, status_code=200, json=lambda: json.loads("<Error/>"))
        body = json.dumps(self.chunks[index])
        return SimpleNamespace(ok=True, status_code=200, json=lambda: json.loads(body))


CHUNKS = [[["s", "t0", "a"]], [["s", "t1", "b"], ["s", "t1", "c"]], [["s", "t2", None]]]
SSE_HEADERS = {"x-amz-server-side-encryption-customer-key": "k"}


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage(CHUNKS)
    monkeypatch.setattr("app.providers.databricks.uc_metadata.requests.get", fake.get)
    return fake


def test_external_links_are_read_in_chunk_order_without_the_workspace_credentials(storage):
    from databricks.sdk.service.sql import Disposition, Format

    statements = LinkStatements(CHUNKS, headers=SSE_HEADERS)

    result = statement_result(SimpleNamespace(statement_execution=statements), SQL, WAREHOUSE, external_links=True)

    assert result.rows == [row for chunk in CHUNKS for row in chunk]
    assert (result.chunks, result.external_links) == (3, True)
    call = statements.calls[0]
    assert (call["disposition"], call["format"]) == (Disposition.EXTERNAL_LINKS, Format.JSON_ARRAY)
    assert len(storage.requests) == 3 and storage.max_in_flight > 1
    for sent in storage.requests:
        assert sent["headers"] == SSE_HEADERS
        assert "auth" not in sent and sent["timeout"]


def test_a_result_over_the_row_limit_raises_before_any_chunk_is_downloaded(storage):
    statements = LinkStatements(CHUNKS)
    client = SimpleNamespace(statement_execution=statements)

    with pytest.raises(ResultTooLarge, match="4 rows, more than the 3 one read may hold"):
        statement_result(client, SQL, WAREHOUSE, external_links=True, max_rows=3)
    assert storage.requests == []
    assert len(statement_result(client, SQL, WAREHOUSE, external_links=True, max_rows=4).rows) == 4


@pytest.mark.parametrize("failure, problem", [
    ("connection", "ConnectionError"), ("forbidden", "HTTP 403"), ("not_json", "isn't a JSON array"),
])
def test_a_failed_download_raises_external_links_unavailable_without_the_link(storage, failure, problem):
    storage.failure = failure
    client = SimpleNamespace(statement_execution=LinkStatements(CHUNKS))

    with pytest.raises(ExternalLinksUnavailable) as raised:
        statement_result(client, SQL, WAREHOUSE, external_links=True)

    message = str(raised.value)
    assert problem in message and "bucket.s3.amazonaws.com" in message
    assert "secret" not in "".join(traceback.format_exception(raised.value))


def test_a_short_external_links_read_raises_instead_of_falling_back(storage):
    client = SimpleNamespace(statement_execution=LinkStatements(CHUNKS, total=5))

    with pytest.raises(RuntimeError, match=r"has 5 row\(s\) but only 4 were read") as raised:
        statement_result(client, SQL, WAREHOUSE, external_links=True)

    assert not isinstance(raised.value, ExternalLinksUnavailable)


def test_a_refused_external_links_request_raises_external_links_unavailable():
    from databricks.sdk.errors import BadRequest

    def refuse(**kwargs):
        raise BadRequest("EXTERNAL_LINKS disposition is not enabled for this workspace")

    client = SimpleNamespace(statement_execution=SimpleNamespace(execute_statement=refuse))

    with pytest.raises(ExternalLinksUnavailable, match="not enabled"):
        statement_result(client, SQL, WAREHOUSE, external_links=True)
    with pytest.raises(BadRequest):
        statement_result(client, SQL, WAREHOUSE)
