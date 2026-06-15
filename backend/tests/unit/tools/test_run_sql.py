"""Unit tests for the run_sql tool (managed DBSQL MCP execute_sql_read_only).

These exercise the response parsing + polling without touching Databricks: the
managed-MCP call is mocked, returning the StatementResponse shapes the real
``/api/2.0/mcp/sql`` server emits (typed ``data_array`` + manifest schema).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.providers.databricks_mcp import GenieAuthUnavailableError
from app.tools.data_chat.run_sql import run_sql


def _succeeded_response(columns, typed_rows, *, total_row_count=None, truncated=False):
    """Build a normalized MCP response mimicking execute_sql_read_only success."""
    return {
        "content": "",
        "structured": {
            "statement_id": "stmt-1",
            "status": {"state": "SUCCEEDED"},
            "manifest": {
                "format": "JSON_ARRAY",
                "schema": {
                    "column_count": len(columns),
                    "columns": [
                        {"name": name, "type_name": "STRING", "position": i}
                        for i, name in enumerate(columns)
                    ],
                },
                "total_row_count": total_row_count if total_row_count is not None else len(typed_rows),
                "truncated": truncated,
                "total_chunk_count": 1,
            },
            "result": {"chunk_index": 0, "row_offset": 0, "data_array": typed_rows},
        },
        "is_error": False,
    }


def _typed(*values):
    """A typed proto-JSON row: list of {"string_value": v} cells."""
    return {"values": [{"string_value": v} for v in values]}


@pytest.mark.asyncio
async def test_run_sql_parses_typed_data_array():
    resp = _succeeded_response(
        ["database", "tableName"],
        [_typed("bakehouse", "sales_customers"), _typed("bakehouse", "sales_transactions")],
    )
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool", new=AsyncMock(return_value=resp)
    ) as call_mock:
        result = await run_sql.execute(sql="SELECT * FROM samples.bakehouse.x", _obo_token="tok")

    assert result.get("ok") is True
    assert result["columns"] == ["database", "tableName"]
    assert result["rows"] == [
        ["bakehouse", "sales_customers"],
        ["bakehouse", "sales_transactions"],
    ]
    assert result["row_count"] == 2
    assert result["sample"][0] == {"database": "bakehouse", "tableName": "sales_customers"}
    # We must call the *read-only* server tool, never the mutating one.
    assert call_mock.await_args.args[0] == "execute_sql_read_only"
    assert call_mock.await_args.args[1] == {"query": "SELECT * FROM samples.bakehouse.x"}


@pytest.mark.asyncio
async def test_run_sql_polls_until_succeeded():
    pending = {
        "content": "",
        "structured": {"statement_id": "stmt-9", "status": {"state": "RUNNING"}},
        "is_error": False,
    }
    done = _succeeded_response(["n"], [_typed("1")])
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool",
        new=AsyncMock(side_effect=[pending, done]),
    ) as call_mock, patch("app.tools.data_chat.run_sql.asyncio.sleep", new=AsyncMock()):
        result = await run_sql.execute(sql="SELECT 1 AS n", _obo_token="tok")

    assert result.get("ok") is True
    assert result["rows"] == [["1"]]
    # First call starts the statement, second polls it.
    assert call_mock.await_args_list[0].args[0] == "execute_sql_read_only"
    assert call_mock.await_args_list[1].args[0] == "poll_sql_result"
    assert call_mock.await_args_list[1].args[1] == {"statement_id": "stmt-9"}


@pytest.mark.asyncio
async def test_run_sql_server_error_is_surfaced_not_swallowed():
    """A write blocked by the read-only server comes back as is_error -> failure."""
    resp = {
        "content": "Operation not permitted: read-only mode.",
        "structured": None,
        "is_error": True,
    }
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool", new=AsyncMock(return_value=resp)
    ):
        result = await run_sql.execute(sql="WITH t AS (SELECT 1) INSERT INTO x SELECT * FROM t")

    assert "error" in result
    assert "ok" not in result
    assert "read-only" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_sql_failed_state_returns_error_message():
    resp = {
        "content": "",
        "structured": {
            "statement_id": "s",
            "status": {"state": "FAILED", "error": {"message": "Table not found: foo"}},
        },
        "is_error": False,
    }
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool", new=AsyncMock(return_value=resp)
    ):
        result = await run_sql.execute(sql="SELECT * FROM foo")

    assert "error" in result
    assert "Table not found" in result["error"]


@pytest.mark.asyncio
async def test_run_sql_auth_error_surfaced():
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool",
        new=AsyncMock(side_effect=GenieAuthUnavailableError("No authentication available for the Databricks SQL MCP server.")),
    ):
        result = await run_sql.execute(sql="SELECT 1")

    assert "error" in result
    assert "authentication" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_sql_parses_json_text_frame_when_no_structured():
    """Some servers return the StatementResponse as a JSON text frame."""
    import json

    statement = {
        "statement_id": "s",
        "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "c", "position": 0}]}, "total_row_count": 1},
        "result": {"data_array": [["hello"]]},
    }
    resp = {"content": json.dumps(statement), "structured": None, "is_error": False}
    with patch(
        "app.tools.data_chat.run_sql.call_dbsql_tool", new=AsyncMock(return_value=resp)
    ):
        result = await run_sql.execute(sql="SELECT 'hello' AS c")

    assert result.get("ok") is True
    assert result["columns"] == ["c"]
    assert result["rows"] == [["hello"]]
