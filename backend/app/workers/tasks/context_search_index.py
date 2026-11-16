"""Poller hook: keep Context Catalog passages embedded for semantic search.

Called on every poll cycle on the leader replica only. It is cheap when idle: the service
decides when the next look is due (``context_search_index.due``), so most cycles return at
once. Where the database can't do semantic search (SQLite, no pgvector) or the feature is
switched off, a pass does nothing and checks again in 30 s.
"""
import asyncio
import logging

from app.core.feature_flags import is_feature_enabled
from app.db.session import get_lakebase_session
from app.services import context_search_index

logger = logging.getLogger(__name__)

_running = asyncio.Lock()
_DB_ERROR_RETRY_S = 60.0


def _run_in_own_session() -> None:
    db = get_lakebase_session()
    try:
        if context_search_index.ensure_due():
            context_search_index.ensure_capabilities(db.get_bind())
        context_search_index.pass_once(db)
    finally:
        db.close()


async def context_search_index_task() -> None:
    if not is_feature_enabled("context_catalog"):
        return
    if _running.locked() or not context_search_index.due():
        return
    async with _running:
        try:
            await asyncio.to_thread(_run_in_own_session)
        except Exception as e:  # noqa: BLE001 - never break the poll cycle
            logger.error("Context Catalog search indexing failed: %s", e, exc_info=True)
            context_search_index.postpone(_DB_ERROR_RETRY_S)
