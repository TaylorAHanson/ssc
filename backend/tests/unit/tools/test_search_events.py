import pytest
from unittest.mock import patch
from app.tools.self_service.search_events import SearchEventsTool

MOCK_EVENTS = [
    {
        "id": "1",
        "title": "Databricks Workshop",
        "description": "Learn Databricks fundamentals.",
        "date": "2026-02-10T09:00:00Z",
        "type": "Workshop"
    },
    {
        "id": "2",
        "title": "Community Meetup",
        "description": "Networking event.",
        "date": "2026-02-15T18:00:00Z",
        "type": "Community Meetup"
    }
]

@patch("app.tools.self_service.search_events.get_content")
@pytest.mark.asyncio
async def test_search_events_all(mock_get_content):
    mock_get_content.return_value = MOCK_EVENTS
    tool = SearchEventsTool()
    
    # By default it only shows upcoming events. 
    # Since mock dates are in Feb 2026, they should be upcoming relative to Jan 2026 (mock today)
    # Actually tool uses datetime.now()
    results = await tool.execute()
    assert len(results) == 2

@patch("app.tools.self_service.search_events.get_content")
@pytest.mark.asyncio
async def test_search_events_query(mock_get_content):
    mock_get_content.return_value = MOCK_EVENTS
    tool = SearchEventsTool()
    
    results = await tool.execute(query="Workshop")
    assert len(results) == 1
    assert results[0]["title"] == "Databricks Workshop"

@patch("app.tools.self_service.search_events.get_content")
@pytest.mark.asyncio
async def test_search_events_type(mock_get_content):
    mock_get_content.return_value = MOCK_EVENTS
    tool = SearchEventsTool()
    
    results = await tool.execute(event_type="Community Meetup")
    assert len(results) == 1
    assert results[0]["type"] == "Community Meetup"

@patch("app.tools.self_service.search_events.get_content")
@pytest.mark.asyncio
async def test_search_events_date_range(mock_get_content):
    mock_get_content.return_value = MOCK_EVENTS
    tool = SearchEventsTool()
    
    # Filter for mid-Feb
    results = await tool.execute(start_date="2026-02-12", end_date="2026-02-20")
    assert len(results) == 1
    assert results[0]["id"] == "2"
