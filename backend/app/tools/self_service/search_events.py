from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.agents.content_registry import get_content

class SearchEventsInput(BaseModel):
    query: Optional[str] = Field(None, description="Optional search term to filter events by title or description")
    event_type: Optional[str] = Field(None, description="Filter by event type: 'Workshop', 'Webinar', 'Office Hours', 'Community Meetup'")
    start_date: Optional[str] = Field(None, description="Filter events starting from this date (ISO format: YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="Filter events up to this date (ISO format: YYYY-MM-DD)")

@tool(
    name="search_events",
    description="Search for upcoming workshops, webinars, office hours, and community events. Use this to help users find training and support sessions.",
    args_schema=SearchEventsInput
)
def search_events(query: Optional[str] = None, event_type: Optional[str] = None, 
            start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute the event search.
    """
    # Load events from content registry
    events = get_content("events.json")
    if not isinstance(events, list):
        return {"count": 0, "events": []}

    filtered_events = []
    now = datetime.now()

    for event in events:
        # Parse event date
        try:
            event_date = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            continue

        # Basic filter: only show upcoming events (or recent past if explicitly asked, but default to upcoming)
        if not start_date and event_date.date() < now.date():
            continue

        # Filter by query
        if query:
            q = query.lower()
            title = event.get('title', '').lower()
            desc = event.get('description', '').lower()
            if q not in title and q not in desc:
                continue

        # Filter by type
        if event_type and event.get('type') != event_type:
            continue

        # Filter by date range
        if start_date:
            try:
                start = datetime.fromisoformat(start_date).date()
                if event_date.date() < start:
                    continue
            except ValueError:
                pass

        if end_date:
            try:
                end = datetime.fromisoformat(end_date).date()
                if event_date.date() > end:
                    continue
            except ValueError:
                pass

        filtered_events.append(event)

    # Sort by date
    filtered_events.sort(key=lambda x: x['date'])
    
    # Limit to 10 results to keep context small
    results = filtered_events[:10]
    return {
        "count": len(results),
        "events": results
    }
