"""
``run_sql`` tool: run a **read-only** SQL query on Databricks SQL, as the user.

This is the SQL-native sibling of ``ask_your_data`` (Genie). Where Genie turns a
natural-language question into SQL behind an opaque, slow Managed-MCP call, this
tool lets the agent run SQL it has already composed against a known table and get
the rows back *directly and deterministically* — which is exactly what the
charting pipeline needs (Genie sometimes answers in prose with no rows attached).

Identity / governance:
  * The statement runs **On-Behalf-Of the user** (their OBO token) so Unity
    Catalog permissions apply — the agent can never read data the user can't.
  * It is **read-only by construction**: the single statement must begin with one
    of SELECT / WITH / SHOW / DESCRIBE / EXPLAIN / TABLE / VALUES. Anything that
    mutates (INSERT/UPDATE/DELETE/MERGE/DDL/GRANT/…) is rejected before execution.
  * Multiple statements are rejected; an automatic ``LIMIT`` caps result sets.

Output shape is what ``parseToolChart`` (src/components/chat/toolChart.ts) already
understands — ``columns`` + ``rows`` (arrays) — so the chat renders an auto-chart
with interactive re-graph controls below the pill, and ``render_chart`` can later
re-graph the same rows.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.providers.databricks import DatabricksProvider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)

# Hard ceiling on rows shipped back (and the auto-LIMIT we inject). Keeps the SSE
# frame and the LLM-facing tool message bounded while still giving charts plenty
# of points. Mirrors the Genie preview cap for parity.
_MAX_ROWS = 5000
# Rows surfaced as readable dict records for the model to reason over. The full
# (capped) row set still rides along under ``rows`` for the chart UI.
_SAMPLE_ROWS = 15
_TIMEOUT_SECONDS = 120

# A read-only statement must start with one of these. This leading-keyword
# whitelist + the single-statement rule is the actual safety guarantee: you
# cannot run DML/DDL if the one allowed statement has to begin with a read verb.
_READ_LEADERS = {
    "select",
    "with",
    "show",
    "describe",
    "desc",
    "explain",
    "table",
    "values",
}
# Statements that produce a normal result set and accept a trailing LIMIT.
_LIMITABLE_LEADERS = {"select", "with", "table", "values"}


_DESCRIPTION = """\
Run a READ-ONLY SQL query on Databricks SQL and get the rows back. Runs as the \
current user (their Unity Catalog permissions apply), so it can only read data the \
user can already access.

Use this when you already know the table(s) and want precise, fast, chartable data \
- e.g. after discovering a table with search_data_assets / get_table_list. The rows \
come back directly, so the chat auto-renders a chart you (or the user) can re-graph, \
and you can call render_chart afterward to change the visualization.

Prefer ask_your_data (Genie) instead when the question is vague, spans unknown \
tables, or you'd be guessing at the schema - Genie grounds natural language in the \
metastore. Prefer this tool when you can write the SQL yourself.

Rules (enforced):
- One statement only, and it must be read-only: SELECT / WITH / SHOW / DESCRIBE / \
EXPLAIN / TABLE / VALUES. Anything that writes (INSERT, UPDATE, DELETE, MERGE, \
CREATE, DROP, ALTER, GRANT, ...) is rejected.
- Use fully-qualified names (catalog.schema.table).
- A LIMIT is added automatically if you omit one (max %d rows).\
""" % _MAX_ROWS


class RunSqlInput(BaseModel):
    """Schema for the ``run_sql`` tool."""

    sql: str = Field(
        ...,
        min_length=6,
        description=(
            "A single read-only SQL statement to execute (SELECT / WITH / SHOW / "
            "DESCRIBE / EXPLAIN / TABLE / VALUES). Use fully-qualified table names. "
            "Do not include a trailing semicolon or multiple statements."
        ),
    )
    warehouse_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional SQL warehouse ID to run on. Leave empty to use the app's "
            "configured default warehouse."
        ),
    )


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments for safe parsing.

    We only use the stripped form to *classify* the statement (leading keyword,
    statement count, LIMIT presence); the original SQL is what we actually run.
    """
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return no_line


def _classify(sql: str) -> Dict[str, Any]:
    """Validate a single read-only statement and report whether it's limitable.

    Returns ``{"ok": True, "leader": str, "limitable": bool}`` or
    ``{"ok": False, "error": str}``. The check is intentionally conservative:
    leading-keyword whitelist + single-statement enforcement.
    """
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        return {"ok": False, "error": "Empty SQL after removing comments."}

    # Reject multiple statements. A single trailing ';' is fine.
    inner = cleaned[:-1] if cleaned.endswith(";") else cleaned
    if ";" in inner:
        return {
            "ok": False,
            "error": (
                "Only a single SQL statement is allowed. Remove extra ';'-separated "
                "statements and run them one at a time."
            ),
        }

    # Allow a leading '(' for parenthesized set operations: (SELECT ...) UNION ...
    probe = inner.lstrip()
    while probe.startswith("("):
        probe = probe[1:].lstrip()

    m = re.match(r"([a-zA-Z]+)", probe)
    leader = m.group(1).lower() if m else ""
    if leader not in _READ_LEADERS:
        return {
            "ok": False,
            "error": (
                f"Only read-only queries are allowed. The statement starts with "
                f"'{leader or '?'}', but it must begin with one of: "
                f"SELECT, WITH, SHOW, DESCRIBE, EXPLAIN, TABLE, VALUES. "
                "This tool will not run statements that modify data or objects."
            ),
        }
    return {"ok": True, "leader": leader, "limitable": leader in _LIMITABLE_LEADERS}


def _has_limit(sql: str) -> bool:
    """Heuristic: does the (comment-stripped) query already cap rows with LIMIT?"""
    cleaned = _strip_sql_comments(sql)
    return re.search(r"\blimit\b\s+\d+", cleaned, flags=re.IGNORECASE) is not None


def _apply_limit(sql: str, limitable: bool) -> str:
    """Append ``LIMIT _MAX_ROWS`` to limitable queries that don't already cap rows."""
    if not limitable or _has_limit(sql):
        return sql
    trimmed = sql.rstrip()
    if trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()
    return f"{trimmed}\nLIMIT {_MAX_ROWS}"


def _get_provider() -> DatabricksProvider:
    return DatabricksProvider(
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
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
async def run_sql(
    sql: str,
    warehouse_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute a validated read-only query under the user's identity and return rows.

    Returns ``{ok, columns, rows, row_count, sample, sql, truncated}`` on success
    (the ``columns``/``rows`` shape feeds the chart UI), or ``{"error": ...}`` on a
    validation or execution failure (surfaced as a failed pill, no false success).
    """
    verdict = _classify(sql)
    if not verdict.get("ok"):
        return {"error": verdict["error"]}

    effective_sql = _apply_limit(sql, bool(verdict.get("limitable")))
    obo_token: Optional[str] = kwargs.get("_obo_token")

    try:
        provider = _get_provider()
        result = await provider.execute_sql(
            query=effective_sql,
            warehouse=warehouse_id,
            timeout_seconds=_TIMEOUT_SECONDS,
            obo_token=obo_token,
        )
    except Exception as e:  # RetryableError or config/auth failures
        logger.warning("run_sql failed: %s", e)
        return {"error": f"SQL execution failed: {e}"}

    raw_rows: List[Any] = result.get("rows") or []
    columns: List[str] = result.get("schema") or []
    if not columns and raw_rows and isinstance(raw_rows[0], dict):
        columns = list(raw_rows[0].keys())

    # Normalize to row-arrays aligned to ``columns`` (what the chart parser wants),
    # capped to the row ceiling. Dict rows are common from the SDK path.
    rows: List[List[Any]] = []
    for row in raw_rows[:_MAX_ROWS]:
        if isinstance(row, dict):
            rows.append([row.get(c) for c in columns])
        elif isinstance(row, (list, tuple)):
            rows.append(list(row))
        else:
            rows.append([row])

    truncated = len(raw_rows) > _MAX_ROWS
    # A small readable sample for the model to reason over without it having to
    # parse the full ``rows`` arrays (which the prompt budget may truncate).
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
        "sql": effective_sql,
        "row_count": len(rows),
        "columns": columns,
        "truncated": truncated,
        "sample": sample,
        "rows": rows,
    }


__all__ = ["run_sql"]
