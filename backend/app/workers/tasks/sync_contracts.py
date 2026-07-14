import logging
from datetime import datetime, timezone

from croniter import croniter, CroniterBadCronError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Track next scheduled run (module-global, like the other sync tasks).
_next_contract_sync_time = None


async def sync_contracts_task(force: bool = False):
    """Scheduled data-contract (ODCS) sync.

    Rediscovers ``dataset``-tagged tables across the catalogs the service
    principal can see and redrafts their ODCS contracts. This is the automated
    equivalent of clicking "Sync Data Contracts" in the UI. It is gated by
    ``CONTRACT_SYNC_CRON`` (empty = disabled / manual-only), because drafting a
    contract calls the LLM once per dataset and is therefore heavier than the
    data-asset cache sync.

    The poller calls this every cycle; the cron gate below makes it a no-op
    until the next scheduled time so we don't re-run on every poll.
    """
    global _next_contract_sync_time
    now = datetime.now(timezone.utc)

    cron_expr = (getattr(settings, "CONTRACT_SYNC_CRON", "") or "").strip()
    if not force:
        if not cron_expr:
            return  # Disabled — contracts refresh only via the manual button.

        if _next_contract_sync_time is None:
            try:
                _next_contract_sync_time = croniter(cron_expr, now).get_next(datetime)
            except CroniterBadCronError:
                logger.error(f"Invalid CONTRACT_SYNC_CRON expression: {cron_expr}")
                return

        if now < _next_contract_sync_time:
            logger.debug(
                "Contract sync skipped — next scheduled run at %s (cron=%s).",
                _next_contract_sync_time.isoformat(), cron_expr,
            )
            return

    # Advance the schedule up front so a long run doesn't immediately re-trigger.
    if cron_expr:
        try:
            _next_contract_sync_time = croniter(cron_expr, now).get_next(datetime)
        except CroniterBadCronError:
            pass

    logger.info("Starting scheduled data contract sync%s...", " (forced)" if force else "")
    try:
        # Imported here to avoid a circular import at module load (the API module
        # imports settings/db which are already up by the time the poller runs).
        from app.api.v1.data_contracts import (
            discover_dataset_groups,
            run_sync_contracts_background,
        )

        dataset_groups = discover_dataset_groups(None)
        if not dataset_groups:
            logger.info("Scheduled contract sync: no 'dataset'-tagged tables found — nothing to do.")
            return

        # Run inline (awaited) — unlike the HTTP endpoint we are not returning a
        # response, so there's no reason to defer to a background task.
        await run_sync_contracts_background(dataset_groups, force=False, specific_dataset_id=None)
        logger.info(
            "Scheduled contract sync complete for %d data set(s). Next run: %s.",
            len(dataset_groups),
            _next_contract_sync_time.isoformat() if _next_contract_sync_time else "n/a",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error during scheduled contract sync: {e}", exc_info=True)
