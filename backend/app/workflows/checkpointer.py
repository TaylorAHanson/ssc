"""
V2 durable-execution checkpointer.

LangGraph persists graph state — the durable replacement for V1's fact-derived
state reconstruction — via a checkpointer. Every run is keyed on the request id
as the LangGraph ``thread_id`` so a crash mid-workflow resumes from the last
committed node instead of restarting.

Backend selection mirrors the app DB:
  * local dev (SQLite)  -> AsyncSqliteSaver (sibling ``v2_checkpoints.db`` file)
  * Lakebase (Postgres) -> AsyncPostgresSaver (same connection string)

The checkpointer is opened per advance() call. For SQLite this is cheap and the
state is durably on disk; for Postgres a longer-lived pooled saver is the
production optimization (tracked for the cutover).
"""
import logging
import os
from contextlib import asynccontextmanager

from app.db.session import get_database_url

logger = logging.getLogger(__name__)


def _sqlite_checkpoint_path(url: str) -> str:
    """Derive a sibling checkpoint DB path from the app's sqlite URL."""
    # url like "sqlite:///<abs-or-rel path>"
    raw = url.replace("sqlite:///", "", 1)
    if not raw or raw == ":memory:":
        return "v2_checkpoints.db"
    directory = os.path.dirname(raw) or "."
    return os.path.join(directory, "v2_checkpoints.db")


@asynccontextmanager
async def build_checkpointer():
    """Yield an async LangGraph checkpointer matching the configured DB."""
    url = get_database_url()
    if url.startswith("sqlite"):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = _sqlite_checkpoint_path(url)
        async with AsyncSqliteSaver.from_conn_string(path) as cp:
            yield cp
    else:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        # get_database_url returns a libpq-style postgresql:// URL already.
        async with AsyncPostgresSaver.from_conn_string(url) as cp:
            try:
                await cp.setup()  # idempotent; creates checkpoint tables once
            except Exception as e:
                logger.warning("V2 checkpointer setup() note: %s", e)
            yield cp
