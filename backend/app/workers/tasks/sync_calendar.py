import logging
import asyncio
from datetime import datetime, timezone
from croniter import croniter, CroniterBadCronError
from app.providers.calendar.client import CalendarProvider
from app.agents.content_registry import save_content
from app.core.config import settings

logger = logging.getLogger(__name__)

# Track next sync time
_next_sync_time = None

async def sync_calendar_task(force: bool = False):
    """
    Task to sync the external calendar feed to events.json.
    Designed to be called periodically from the poller.
    """
    global _next_sync_time
    now = datetime.now(timezone.utc)
    
    # Check if we should sync based on cron
    cron_expr = getattr(settings, 'EVENT_SYNC_CRON', '0 * * * *')
    if not force:
        if not cron_expr:
            return # Disabled
            
        if _next_sync_time is None:
            try:
                iter = croniter(cron_expr, now)
                _next_sync_time = iter.get_next(datetime)
            except CroniterBadCronError:
                logger.error(f"Invalid EVENT_SYNC_CRON expression: {cron_expr}")
                return
                
        if now < _next_sync_time:
            return # Too soon to sync again
            
    logger.info("Starting calendar sync...")
    
    # Calculate next time for the future
    if cron_expr:
        try:
            iter = croniter(cron_expr, now)
            _next_sync_time = iter.get_next(datetime)
        except CroniterBadCronError:
            pass
    
    try:
        provider = CalendarProvider()
        events = await provider.fetch_events()
        
        if events:
            # Save to content registry (this will update events.json)
            # We don't create a version every time to avoid bloat, 
            # maybe just once a day or if it's been a while.
            success = save_content("events.json", events, create_version=False)
            if success:
                logger.info(f"Successfully synced {len(events)} events to events.json")
            else:
                logger.error("Failed to save synced events to content registry")
        else:
            logger.warning("No events fetched from calendar feed")
            
    except Exception as e:
        # Don't fail the whole loop, just log the error
        logger.error(f"Error during calendar sync task: {e}", exc_info=True)
