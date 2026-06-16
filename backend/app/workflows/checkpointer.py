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
state is durably on disk.

Why per-call (not a long-lived pool) for Postgres: ``get_database_url()`` mints a
*fresh short-lived Lakebase OAuth token* on every call and embeds it in the DSN.
Opening the saver per call is what keeps that token current — a naive cached
pool would pin the first token and start failing auth once it expires (the
SQLAlchemy engine solves the same problem with a do_connect token-refresh hook +
pool_recycle, which a raw psycopg pool here would not get). What we *can* safely
avoid is re-running the idempotent ``setup()`` DDL on every advance: the
checkpoint tables only need to be created once per process, so we gate it behind
a module flag below.
"""
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

from app.db.session import get_database_url, get_db_schema

logger = logging.getLogger(__name__)

# setup() creates the checkpoint tables (CREATE TABLE IF NOT EXISTS ...). It's
# idempotent but issues several DDL round-trips, so we only run it once per
# process instead of on every advance()/peek().
_pg_setup_done = False


def _pg_conn_string_with_schema(url: str, schema: str) -> str:
    """Pin ``search_path`` on the raw psycopg connection the saver uses.

    The checkpointer connects with bare psycopg (NOT the SQLAlchemy engine, which
    sets ``search_path`` via ``connect_args``). Without this the connection
    defaults to ``public`` — where PG 15+/Lakebase revokes CREATE for non-owner
    roles — so ``cp.setup()`` can't create the checkpoint tables and every
    read fails with ``relation "checkpoints" does not exist``. libpq's
    ``options`` keyword applies the schema at connect time (survives the pool's
    rollback-on-return, same rationale as the engine's connect_args).
    """
    opts = quote(f"-c search_path={schema},public")
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options={opts}"


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

        global _pg_setup_done
        # get_database_url returns a libpq-style postgresql:// URL already, but
        # WITHOUT the app's search_path — pin it so the checkpoint tables land in
        # (and are read from) the app schema, not the locked-down `public`.
        conn_string = _pg_conn_string_with_schema(url, get_db_schema())
        async with AsyncPostgresSaver.from_conn_string(conn_string) as cp:
            if not _pg_setup_done:
                try:
                    await cp.setup()  # idempotent; creates checkpoint tables once
                    _pg_setup_done = True
                except Exception as e:
                    logger.warning("V2 checkpointer setup() note: %s", e)
            yield cp
