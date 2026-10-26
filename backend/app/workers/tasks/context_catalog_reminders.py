"""Poller hook: post Context Catalog stale-document reminders to Teams on a cron."""
import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter, CroniterBadCronError

from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.db.session import get_lakebase_session

logger = logging.getLogger(__name__)

# Next scheduled run; reset by settings_store when the cron is edited.
_next_reminder_time = None
_running = asyncio.Lock()


def _run_in_own_session() -> dict:
    from app.services.context_catalog_staleness import run_stale_reminders

    db = get_lakebase_session()
    try:
        return run_stale_reminders(db, trigger="schedule")
    finally:
        db.close()


async def context_catalog_reminders_task() -> None:
    """Run reminders when CONTEXT_CATALOG_STALE_REMINDER_CRON is due.

    No-op unless the Context Catalog feature and the reminder toggle are both on
    and the cron is set. The poller only calls this on the leader replica.
    """
    global _next_reminder_time
    if not is_feature_enabled("context_catalog") or not getattr(
        settings, "CONTEXT_CATALOG_STALE_REMINDERS_ENABLED", False
    ):
        _next_reminder_time = None
        return
    cron_expr = (getattr(settings, "CONTEXT_CATALOG_STALE_REMINDER_CRON", "") or "").strip()
    if not cron_expr or _running.locked():
        return
    now = datetime.now(timezone.utc)
    if _next_reminder_time is None:
        try:
            _next_reminder_time = croniter(cron_expr, now).get_next(datetime)
        except (CroniterBadCronError, ValueError):
            logger.error("Invalid CONTEXT_CATALOG_STALE_REMINDER_CRON expression: %s", cron_expr)
            return
    if now < _next_reminder_time:
        return
    # Advance first so a slow or failing run doesn't re-trigger every cycle.
    try:
        _next_reminder_time = croniter(cron_expr, now).get_next(datetime)
    except (CroniterBadCronError, ValueError):
        _next_reminder_time = None
    async with _running:
        try:
            await asyncio.to_thread(_run_in_own_session)
        except Exception as e:  # noqa: BLE001 - never break the poll cycle
            logger.error("Context Catalog stale reminders failed: %s", e, exc_info=True)
