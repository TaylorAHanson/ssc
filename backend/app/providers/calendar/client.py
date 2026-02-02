import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from icalendar import Calendar as ICalendar
from app.providers.base import BaseProvider
from app.core.config import settings

logger = logging.getLogger(__name__)

class CalendarProvider(BaseProvider):
    """
    Provider for fetching and parsing ICS calendar feeds.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.url = self.config.get("url") or settings.EVENT_CALENDAR_URL

    async def health_check(self) -> bool:
        """Verify the calendar URL is accessible."""
        if not self.url:
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.head(self.url, timeout=10.0)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Calendar health check failed for {self.url}: {e}")
            return False

    async def fetch_events(self) -> List[Dict[str, Any]]:
        """
        Fetch and parse events from the configured ICS URL.
        
        Returns:
            List of event dictionaries in the format expected by the frontend.
        """
        if not self.url:
            logger.warning("No EVENT_CALENDAR_URL configured. Returning empty list.")
            return []

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.url, timeout=30.0)
                response.raise_for_status()
                ics_data = response.text
        except Exception as e:
            logger.error(f"Failed to fetch calendar from {self.url}: {e}")
            return []

        return self.parse_ics(ics_data)

    def parse_ics(self, ics_data: str) -> List[Dict[str, Any]]:
        """
        Parse ICS data into a list of event dictionaries.
        """
        events = []
        try:
            cal = ICalendar.from_ical(ics_data)
            
            for component in cal.walk():
                if component.name == "VEVENT":
                    dtstart = component.get('dtstart').dt
                    dtend = component.get('dtend').dt if component.get('dtend') else None
                    
                    # Ensure datetime objects
                    if not isinstance(dtstart, datetime):
                        # It's a date object, convert to datetime at midnight
                        dtstart = datetime.combine(dtstart, datetime.min.time(), tzinfo=timezone.utc)
                    
                    if dtend and not isinstance(dtend, datetime):
                        dtend = datetime.combine(dtend, datetime.min.time(), tzinfo=timezone.utc)

                    # Format for frontend
                    event_id = str(component.get('uid'))
                    title = str(component.get('summary'))
                    description = str(component.get('description', ''))
                    location = str(component.get('location', 'TBD'))
                    
                    # Calculate duration
                    duration_str = "1 hour"
                    if dtend:
                        diff = dtend - dtstart
                        hours = diff.total_seconds() / 3600
                        if hours >= 1:
                            duration_str = f"{hours:.1f} hours" if hours % 1 != 0 else f"{int(hours)} hours"
                        else:
                            duration_str = f"{int(diff.total_seconds() / 60)} minutes"

                    # Determine type (heuristic based on title/description)
                    event_type = "Webinar"
                    lower_text = (title + description).lower()
                    if "workshop" in lower_text:
                        event_type = "Workshop"
                    elif "office hours" in lower_text:
                        event_type = "Office Hours"
                    elif "meetup" in lower_text or "community" in lower_text:
                        event_type = "Community Meetup"

                    # Filter events: 6 months past to 1 year future
                    now = datetime.now(timezone.utc)
                    future_horizon = now + timedelta(days=365)
                    past_horizon = now - timedelta(days=180)
                    
                    if dtstart > future_horizon or dtstart < past_horizon:
                        continue

                    events.append({
                        "id": event_id,
                        "title": title,
                        "description": description,
                        "date": dtstart.isoformat(),
                        "time": dtstart.strftime("%I:%M %p"),
                        "duration": duration_str,
                        "location": location,
                        "type": event_type,
                        "attendees": 0, # Cannot get from ICS easily
                        "joinLink": self._extract_join_link(description)
                    })
            
            # Sort by date
            events.sort(key=lambda x: x['date'])
            return events
            
        except Exception as e:
            logger.error(f"Error parsing ICS data: {e}")
            return []

    def _extract_join_link(self, description: str) -> Optional[str]:
        """Intelligently extract join links (Teams, Meet, Zoom, etc.) from description."""
        import re
        patterns = [
            # Teams
            r'https://teams\.microsoft\.com/l/meetup-join/[^\s<>"]+',
            # Google Meet
            r'https://meet\.google\.com/[a-z0-9-]+',
            # Zoom
            r'https://[a-z0-9]+\.zoom\.us/j/[0-9]+(?:\?pwd=[a-zA-Z0-9]+)?',
            # Generic Join Link
            r'https?://[^\s<>"]+join[^\s<>"]*'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group(0)
        return None
