"""A bundled PostgreSQL server for local development and tests (dev dependency only).

``pixeltable-pgserver`` ships a Postgres (with pgvector) inside a wheel, so local dev runs against
the same database engine as production, and semantic Context Catalog search works locally, with no
Docker or brew. Install it with ``pip install -r backend/requirements-dev.txt``; ``./dev.sh`` does.

Opt-in: ``get_database_url`` uses it only when ``LOCAL_POSTGRES`` is truthy (``./dev.sh`` sets it;
``./dev.sh --sqlite`` doesn't) and no real Postgres is configured. Anything else, including a missing
package, keeps SQLite, where semantic search is simply off and search uses words only.

The server is left running after the app exits (``cleanup_mode=None``) so ``uvicorn --reload``
restarts attach to it instead of booting a new one. Data lives in ``backend/.pgdata`` (gitignored),
or ``LOCAL_POSTGRES_DIR``.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TRUE = ("1", "true", "yes", "on")
_server = None
_lock = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("LOCAL_POSTGRES") or "").strip().lower() in _TRUE


def data_dir() -> Path:
    override = (os.environ.get("LOCAL_POSTGRES_DIR") or "").strip()
    return Path(override) if override else _BACKEND_DIR / ".pgdata"


def is_active() -> bool:
    """Whether this process is connected to the bundled server (so Lakebase credential hooks must stand down)."""
    return _server is not None


def local_uri() -> Optional[str]:
    """The bundled server's connection URI (started on first use), or None if the package isn't installed."""
    global _server
    with _lock:
        if _server is None:
            try:
                import pixeltable_pgserver as pgserver  # type: ignore[import-not-found]
            except ImportError:
                return None
            path = data_dir()
            path.mkdir(parents=True, exist_ok=True)
            _server = pgserver.get_server(path, cleanup_mode=None)
            logger.info("Using bundled local PostgreSQL", extra={"data": {"data_dir": str(path)}})
        return _server.get_uri()
