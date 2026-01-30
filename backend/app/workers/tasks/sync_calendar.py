import logging
import asyncio
from datetime import datetime
from app.providers.calendar.client import CalendarProvider
from app.agents.content_registry import save_content
from app.core.config import settings

logger = logging.getLogger(__name__)

# Track last sync time
_last_sync_time = None

async def sync_calendar_task(force: bool = False):
    """
    Task to sync the external calendar feed to events.json.
    Designed to be called periodically from the poller.
    """
    global _last_sync_time
    
    # Check if we should sync based on interval
    interval_minutes = getattr(settings, 'EVENT_SYNC_INTERVAL_MINUTES', 60)
    now = datetime.now()
    
    if not force and _last_sync_time is not None:
        elapsed = (now - _last_sync_time).total_seconds() / 60
        if elapsed < interval_minutes:
            return # Too soon to sync again
            
    logger.info("Starting calendar sync...")
    
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
                _last_sync_time = now
            else:
                logger.error("Failed to save synced events to content registry")
        else:
            logger.warning("No events fetched from calendar feed")
            # We still update last_sync_time to avoid tight loops on empty/broken feeds
            _last_sync_time = now
            
    except Exception as e:
        # Don't fail the whole loop, just log the error
        logger.error(f"Error during calendar sync task: {e}", exc_info=True)
        # We don't update last_sync_time on error so we can retry sooner
