import pytest
from datetime import datetime, timezone
from app.providers.calendar.client import CalendarProvider

MOCK_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp//NONSGML Event Calendar//EN
BEGIN:VEVENT
UID:uid1@example.com
DTSTAMP:20260130T100000Z
DTSTART:20260210T090000Z
DTEND:20260210T110000Z
SUMMARY:Test Workshop
DESCRIPTION:This is a test workshop for ATLAS.
LOCATION:Virtual (Teams)
END:VEVENT
BEGIN:VEVENT
UID:uid2@example.com
DTSTAMP:20260130T100000Z
DTSTART:20260215T140000Z
SUMMARY:Community Meetup
DESCRIPTION:Join us for a meetup! https://teams.microsoft.com/l/meetup-join/19%3ameeting_test%40thread.v2/0?context=%7b%22Tid%22%3a%22test%22%7d
LOCATION:Building Q
END:VEVENT
END:VCALENDAR"""

def test_parse_ics():
    provider = CalendarProvider()
    events = provider.parse_ics(MOCK_ICS)
    
    assert len(events) == 2
    
    # Check first event
    e1 = events[0]
    assert e1["id"] == "uid1@example.com"
    assert e1["title"] == "Test Workshop"
    assert e1["type"] == "Workshop"
    assert e1["duration"] == "2 hours"
    assert e1["location"] == "Virtual (Teams)"
    
    # Check second event
    e2 = events[1]
    assert e2["id"] == "uid2@example.com"
    assert e2["title"] == "Community Meetup"
    assert e2["type"] == "Community Meetup"
    assert e2["duration"] == "1 hour" # Default
    assert e2["joinLink"] is not None
    assert "meeting_test" in e2["joinLink"]

def test_extract_join_link():
    provider = CalendarProvider()
    desc = "Some text before https://teams.microsoft.com/l/meetup-join/abc-123 some text after"
    link = provider._extract_join_link(desc)
    assert link == "https://teams.microsoft.com/l/meetup-join/abc-123"

    desc_no_link = "No link here"
    assert provider._extract_join_link(desc_no_link) is None
