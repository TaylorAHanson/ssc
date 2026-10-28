"""Page views and browser errors reported to the server log."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import client_events
from app.core import config
from app.models.user import User


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(client_events.router, prefix="/api/v1/client-events")
    app.dependency_overrides[get_current_user] = lambda: User(
        id="ana@example.com", email="ana@example.com", full_name="Ana", entitlements=[], roles=["User"],
    )
    return TestClient(app)


def _lines(caplog):
    return [r for r in caplog.records if r.name == client_events.logger.name]


def test_page_views_and_errors_are_logged(client, caplog, monkeypatch):
    monkeypatch.setitem(config._yaml_config, "logging", {"client_events": True})
    caplog.set_level(logging.INFO)
    response = client.post("/api/v1/client-events", json={"events": [
        {"type": "page_view", "path": "/discovery?q=secret#top", "app_version": "2.6.0"},
        {"type": "error", "path": "/admin", "source": "react", "message": "TypeError: x is undefined",
         "stack": "at Foo", "request_id": "req-1"},
    ]})
    assert response.status_code == 204

    page, error = _lines(caplog)
    # The query string and fragment can hold search terms, so only the path is kept.
    assert page.getMessage() == "PAGE_VIEW /discovery"
    assert page.levelno == logging.INFO
    assert error.levelno == logging.WARNING
    assert "TypeError: x is undefined" in error.getMessage()
    assert error.data["failed_request_id"] == "req-1"
    assert error.data["stack"] == "at Foo"


def test_oversized_fields_are_cut_and_the_batch_is_capped(client, caplog, monkeypatch):
    monkeypatch.setitem(config._yaml_config, "logging", {"client_events": True})
    caplog.set_level(logging.INFO)
    events = [{"type": "error", "path": "/x", "message": "m" * 5000, "stack": "s" * 50_000}]
    events += [{"type": "page_view", "path": f"/p{i}"} for i in range(40)]
    assert client.post("/api/v1/client-events", json={"events": events}).status_code == 204

    lines = _lines(caplog)
    assert len(lines) == client_events.MAX_EVENTS
    assert len(lines[0].data["error"]) == 1000
    assert len(lines[0].data["stack"]) == 3000


def test_nothing_is_logged_while_the_setting_is_off(client, caplog, monkeypatch):
    monkeypatch.setitem(config._yaml_config, "logging", {"client_events": False})
    caplog.set_level(logging.INFO)
    response = client.post("/api/v1/client-events", json={"events": [{"type": "page_view", "path": "/"}]})
    assert response.status_code == 204
    assert _lines(caplog) == []


def test_unknown_event_types_are_refused(client):
    response = client.post("/api/v1/client-events", json={"events": [{"type": "keystroke", "path": "/"}]})
    assert response.status_code == 422
