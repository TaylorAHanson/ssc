"""
``run_sql`` tool: run a **read-only** SQL query on Databricks SQL, as the user.

This is the SQL-native sibling of ``ask_your_data`` (Genie). Where Genie turns a
natural-language question into SQL behind an opaque, slow Managed-MCP call, this
tool lets the agent run SQL it has already composed against a known table and get
the rows back *directly and deterministically* — which is exactly what the
charting pipeline needs (Genie sometimes answers in prose with no rows attached).

Identity / governance:
  * The query runs through the **Databricks-managed DBSQL MCP server**
    (``/api/2.0/mcp/sql``) using the ``execute_sql_read_only`` tool, so the
    **read-only guarantee is enforced server-side by Databricks** — there is no
    hand-rolled SQL parsing to bypass.
  * It runs **On-Behalf-Of the user** (their OBO token), so Unity Catalog
    permissions apply — the agent can never read data the user can't. The SP
    fallback is only allowed in true local dev (same policy as Genie).

The server is asynchronous for long queries: ``execute_sql_read_only`` starts the
statement and returns a ``statement_id`` if it doesn't finish immediately; we then
poll ``poll_sql_result`` until it reaches a terminal state. Output is shaped as
``columns`` + ``rows`` so the chat auto-renders a chart with interactive re-graph
controls, and ``render_chart`` can later re-graph the same rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.providers.databricks_mcp import GenieAuthUnavailableError, call_dbsql_tool
from app.tools.mcp import tool

logger = logging.getLogger(__name__)

# Row ceiling shipped back (and surfaced to the chart UI). Keeps the SSE frame and
# the LLM-facing tool message bounded; mirrors the Genie preview cap for parity.
_MAX_ROWS = 5000
# Rows surfaced as readable dict records for the model to reason over; the full
# (capped) row set still rides along under ``rows`` for the chart UI.
_SAMPLE_ROWS = 15
# Total wall-clock budget for polling a long-running statement before giving up.
_POLL_TIMEOUT_SECONDS = 110
_POLL_INTERVAL_SECONDS = 2.0

_STATE_SUCCEEDED = "SUCCEEDED"
_TERMINAL_BAD = {"FAILED", "CANCELED", "CANCELLED", "CLOSED"}

_DESCRIPTION = """\
Run a READ-ONLY SQL query on Databricks SQL and get the rows back. The query runs \
through Databricks' managed SQL server as the current user (their Unity Catalog \
permissions apply) and is read-only — the server rejects anything that writes.

Use this when you already know the table(s) and want precise, fast, chartable data \
- e.g. after discovering a table with search_data_assets / get_table_list. The rows \
come back directly, so the chat auto-renders a chart you (or the user) can re-graph, \
and you can call render_chart afterward to change the visualization.

Prefer ask_your_data (Genie) instead when the question is vague, spans unknown \
tables, or you'd be guessing at the schema - Genie grounds natural language in the \
metastore. Prefer this tool when you can write the SQL yourself.

Tips:
- Use fully-qualified names (catalog.schema.table), including system.* tables \
(system.billing.usage, system.lakeflow.jobs, ...) for cost/usage/job questions.
- Add your own LIMIT for large tables; only one statement per call.\
"""


class RunSqlInput(BaseModel):
    """Schema for the ``run_sql`` tool."""

    sql: str = Field(
        ...,
        min_length=6,
        description=(
            "A single read-only SQL statement to execute (SELECT / WITH / SHOW / "
            "DESCRIBE / EXPLAIN). Use fully-qualified table names. The server "
            "rejects any statement that modifies data or objects."
        ),
    )


@tool(
    name="run_sql",
    description=_DESCRIPTION,
    args_schema=RunSqlInput,
    feature_flag="run_sql",
    side_effect_class="read",
    friendly_label="Running SQL...",
    friendly_completion_label="Query complete",
)
async def run_sql(sql: str, **kwargs: Any) -> Dict[str, Any]:
    """Execute a read-only query via the managed DBSQL MCP server and return rows.

    Returns ``{ok, columns, rows, row_count, sample, truncated}`` on success (the
    ``columns``/``rows`` shape feeds the chart UI), or ``{"error": ...}`` on an
    auth/validation/execution failure (surfaced as a failed pill — no false
    success).
    """
    obo_token: Optional[str] = kwargs.get("_obo_token")

    try:
        resp = await call_dbsql_tool(
            "execute_sql_read_only", {"query": sql}, obo_token=obo_token
        )
    except GenieAuthUnavailableError as e:
        return {"error": str(e)}
    except Exception as e:  # network / MCP protocol / config errors
        logger.warning("run_sql execute_sql_read_only failed: %s", e)
        return {"error": f"SQL execution failed: {e}"}

    if resp.get("is_error"):
        # Server-side rejection (incl. a write attempt blocked by the read-only
        # tool, or a SQL/permission error) — surface it verbatim as a failure.
        return {"error": resp.get("content") or "The Databricks SQL server reported an error."}

    payload = _statement_payload(resp)
    if payload is None:
        return {"error": "Could not parse the SQL response from Databricks."}

    # Poll until the statement reaches a terminal state (or we time out).
    statement_id = payload.get("statement_id")
    state = _state(payload)
    waited = 0.0
    while state != _STATE_SUCCEEDED and state not in _TERMINAL_BAD:
        if not statement_id:
            return {
                "error": (
                    f"SQL is still '{state or 'pending'}' but Databricks returned "
                    "no statement_id to poll."
                )
            }
        if waited >= _POLL_TIMEOUT_SECONDS:
            return {
                "error": (
                    f"SQL query did not finish within {int(_POLL_TIMEOUT_SECONDS)}s "
                    f"(statement {statement_id}). Try a more selective query or a LIMIT."
                )
            }
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        waited += _POLL_INTERVAL_SECONDS
        try:
            presp = await call_dbsql_tool(
                "poll_sql_result", {"statement_id": statement_id}, obo_token=obo_token
            )
        except Exception as e:
            logger.warning("run_sql poll_sql_result failed: %s", e)
            return {"error": f"SQL poll failed: {e}"}
        if presp.get("is_error"):
            return {"error": presp.get("content") or "SQL poll reported an error."}
        payload = _statement_payload(presp) or payload
        state = _state(payload)

    if state in _TERMINAL_BAD:
        return {"error": _failure_message(payload, state)}

    columns = _columns(payload)
    rows = _rows(payload, columns)
    total_rows = _total_row_count(payload, len(rows))
    truncated = total_rows > len(rows) or _manifest_truncated(payload) or len(rows) >= _MAX_ROWS
    sample = [dict(zip(columns, r)) for r in rows[:_SAMPLE_ROWS]]

    logger.info(
        "run_sql ok: %d cols, %d rows (truncated=%s, obo=%s)",
        len(columns),
        len(rows),
        truncated,
        bool(obo_token),
    )

    return {
        "ok": True,
        "row_count": len(rows),
        "columns": columns,
        "truncated": truncated,
        "sample": sample,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Response parsing (defensive: the managed server returns a StatementResponse-
# shaped object, either as structuredContent or a JSON text frame).
# ---------------------------------------------------------------------------
def _statement_payload(resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the StatementResponse dict from a normalized MCP tool response."""
    structured = resp.get("structured")
    if isinstance(structured, dict) and _looks_like_statement(structured):
        return structured
    # Some MCP servers wrap the real payload one level down.
    if isinstance(structured, dict):
        for v in structured.values():
            if isinstance(v, dict) and _looks_like_statement(v):
                return v
    content = resp.get("content")
    if isinstance(content, str) and content.strip():
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            if _looks_like_statement(decoded):
                return decoded
            for v in decoded.values():
                if isinstance(v, dict) and _looks_like_statement(v):
                    return v
    return None


def _looks_like_statement(obj: Dict[str, Any]) -> bool:
    return any(k in obj for k in ("status", "statement_id", "manifest", "result"))


def _state(payload: Dict[str, Any]) -> str:
    """Normalize the statement state to an upper-case string ('' if unknown)."""
    status = payload.get("status")
    if isinstance(status, dict):
        return str(status.get("state") or "").upper()
    if isinstance(status, str):
        return status.upper()
    return ""


def _failure_message(payload: Dict[str, Any], state: str) -> str:
    status = payload.get("status")
    if isinstance(status, dict):
        err = status.get("error")
        if isinstance(err, dict) and err.get("message"):
            return f"SQL {state.lower()}: {err['message']}"
        if isinstance(err, str) and err:
            return f"SQL {state.lower()}: {err}"
    return f"SQL execution {state.lower()}."


def _columns(payload: Dict[str, Any]) -> List[str]:
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        schema = manifest.get("schema")
        if isinstance(schema, dict):
            cols = schema.get("columns")
            if isinstance(cols, list) and cols:
                ordered = sorted(
                    (c for c in cols if isinstance(c, dict)),
                    key=lambda c: c.get("position", 0),
                )
                return [str(c.get("name", f"col_{i}")) for i, c in enumerate(ordered)]
    # Fallback: infer from the first result row if it's a record dict.
    result = payload.get("result")
    if isinstance(result, dict):
        data = result.get("data_array")
        if isinstance(data, list) and data and isinstance(data[0], dict) and "values" not in data[0]:
            return [str(k) for k in data[0].keys()]
    return []


def _rows(payload: Dict[str, Any], columns: List[str]) -> List[List[Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    data = result.get("data_array")
    if not isinstance(data, list):
        return []
    rows: List[List[Any]] = []
    for raw in data[:_MAX_ROWS]:
        if isinstance(raw, dict) and isinstance(raw.get("values"), list):
            # Typed proto-JSON form: {"values": [{"string_value": "x"}, ...]}
            rows.append([_cell_scalar(v) for v in raw["values"]])
        elif isinstance(raw, dict):
            # Record form keyed by column name.
            rows.append([raw.get(c) for c in columns])
        elif isinstance(raw, (list, tuple)):
            rows.append(list(raw))
        else:
            rows.append([raw])
    return rows


def _cell_scalar(cell: Any) -> Any:
    """Pull the scalar out of a typed value cell ({"string_value": "x"} → "x")."""
    if isinstance(cell, dict):
        for k, v in cell.items():
            if k in ("null", "null_value", "is_null") and v:
                return None
        for k, v in cell.items():
            if k.endswith("_value") or k == "value":
                return v
        for v in cell.values():  # last-resort: first value present
            return v
        return None
    return cell


def _total_row_count(payload: Dict[str, Any], fallback: int) -> int:
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        total = manifest.get("total_row_count")
        if isinstance(total, int):
            return total
    return fallback


def _manifest_truncated(payload: Dict[str, Any]) -> bool:
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        if manifest.get("truncated") is True:
            return True
        chunks = manifest.get("total_chunk_count")
        if isinstance(chunks, int) and chunks > 1:
            return True
    return False


__all__ = ["run_sql"]
