"""Training and community content the assistant looks up, and the settings that gate it.

The assistant used to carry all of this in its system prompt. It now fetches it with
``get_learning_resources`` and only knows about a page while that page is switched on.
"""
from __future__ import annotations

import pytest

from app.core import config, settings_store
from app.models.user import User
from app.services import learning_resources as lr
from app.services.training_service import TrainingService


def _tabs(monkeypatch, **tabs):
    """Pretend Admin -> Settings shows exactly these tabs on or off."""
    monkeypatch.setattr(settings_store, "effective_ui_tabs", lambda: dict(tabs))


def _links(monkeypatch, categories, enabled=True):
    monkeypatch.setitem(config._yaml_config, "community_links", {"enabled": enabled, "categories": categories})


LINKS = [
    {"name": "Docs", "links": ["Databricks docs | https://docs.databricks.com | book | Official docs"]},
    {"name": "Ask", "links": [{"title": "Slack help", "url": "https://slack.example/help", "description": "Ask people"}]},
]


class _KeepOpen:
    """A session the code under test may ``close()`` without ending the test's transaction."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.fixture
def seeded_training(db_session, monkeypatch):
    monkeypatch.setattr("app.db.session.get_session_local", lambda: (lambda: _KeepOpen(db_session)))
    track = TrainingService.create_track(db_session, name="Business User", persona="Business User")
    TrainingService.create_course(
        db_session, track_id=track.id, title="SQL for analysts", section="Basics", duration="2h",
        external_url="https://academy.example/sql",
    )
    TrainingService.create_course(db_session, track_id=track.id, title="Dashboards 101")
    TrainingService.create_course(db_session, track_id=track.id, title="Hidden draft", status="draft")
    draft_track = TrainingService.create_track(db_session, name="Admins", status="draft")
    TrainingService.create_course(db_session, track_id=draft_track.id, title="Admin only")
    return track


# ----------------------------------------------------------------- links


def test_links_accept_the_compact_string_and_dict_forms(monkeypatch):
    _tabs(monkeypatch, community_links=True)
    _links(monkeypatch, LINKS + [{"name": "Empty", "links": ["no url here"]}, {"links": ["x | y"]}])
    categories = lr.community_links()
    assert [c["name"] for c in categories] == ["Docs", "Ask"]  # empty/unnamed categories dropped
    assert categories[0]["links"] == [
        {"title": "Databricks docs", "url": "https://docs.databricks.com", "description": "Official docs"}
    ]
    assert categories[1]["links"][0]["url"] == "https://slack.example/help"


def test_links_are_empty_when_the_page_or_the_config_is_off(monkeypatch):
    _links(monkeypatch, LINKS)
    _tabs(monkeypatch, community_links=False)
    assert lr.community_links() == [] and not lr.community_links_enabled()
    _tabs(monkeypatch, community_links=True)
    assert lr.community_links_enabled()
    _links(monkeypatch, LINKS, enabled=False)
    assert lr.community_links() == []
    _links(monkeypatch, [])
    assert not lr.community_links_enabled()  # on, but nothing to point to


@pytest.mark.parametrize(
    "training, links, expected",
    [(True, True, True), (True, False, True), (False, True, True), (False, False, False)],
)
def test_learning_available_when_either_page_is_on(monkeypatch, training, links, expected):
    _tabs(monkeypatch, training=training, community_links=links)
    _links(monkeypatch, LINKS)
    assert lr.learning_available() is expected


# -------------------------------------------------------------- the lookup


def test_lookup_returns_only_what_is_switched_on(monkeypatch, seeded_training):
    _links(monkeypatch, LINKS)
    _tabs(monkeypatch, training=True, community_links=False)
    result = lr.learning_resources()
    assert set(result) == {"training"}
    assert result["training"]["page"] == "/community/training"

    _tabs(monkeypatch, training=False, community_links=True)
    result = lr.learning_resources()
    assert set(result) == {"community_links"}
    assert result["community_links"]["page"] == "/community/links"

    _tabs(monkeypatch, training=True, community_links=True)
    assert set(lr.learning_resources()) == {"training", "community_links"}
    assert set(lr.learning_resources(kind="community")) == {"community_links"}


def test_lookup_with_everything_off_tells_the_model_not_to_invent(monkeypatch):
    _tabs(monkeypatch, training=False, community_links=False)
    result = lr.learning_resources()
    assert set(result) == {"message"}
    assert "do not invent" in result["message"]


def test_training_shows_published_courses_only(monkeypatch, seeded_training):
    _tabs(monkeypatch, training=True)
    (track,) = lr.training_tracks()
    assert track["track"] == "Business User"
    assert track["courses"] == [
        "SQL for analysts (Basics, 2h) https://academy.example/sql",
        "Dashboards 101",
    ]
    # An unpublished track never appears, exactly as on the learner page.
    assert "Admin only" not in str(lr.training_tracks())


def test_training_narrows_by_persona_and_topic(monkeypatch, seeded_training):
    _tabs(monkeypatch, training=True)
    assert lr.training_tracks(persona="business")
    assert lr.training_tracks(persona="engineer") == []
    (track,) = lr.training_tracks(topic="sql")
    assert track["courses"] == ["SQL for analysts (Basics, 2h) https://academy.example/sql"]
    assert lr.training_tracks(topic="kubernetes") == []


def test_community_links_narrow_by_topic(monkeypatch):
    _tabs(monkeypatch, training=False, community_links=True)
    _links(monkeypatch, LINKS)
    result = lr.learning_resources(kind="community", topic="slack")
    (category,) = result["community_links"]["categories"]
    assert category["name"] == "Ask"


# ------------------------------------------------- the tool and its gating


def test_tool_is_registered_and_follows_the_pages(monkeypatch):
    from app.tools.catalog import all_tools

    tool = {name: t for name, t, _origin in all_tools()}["get_learning_resources"]
    _links(monkeypatch, LINKS)
    _tabs(monkeypatch, training=False, community_links=False)
    assert tool.is_available() is False
    _tabs(monkeypatch, training=True, community_links=False)
    assert tool.is_available() is True
    _tabs(monkeypatch, training=False, community_links=True)
    assert tool.is_available() is True


@pytest.mark.asyncio
async def test_tool_executes_through_the_service(monkeypatch):
    from app.tools.catalog import all_tools

    tool = {name: t for name, t, _origin in all_tools()}["get_learning_resources"]
    _tabs(monkeypatch, training=False, community_links=True)
    _links(monkeypatch, LINKS)
    result = await tool.execute(kind="community", topic="docs")
    assert result["community_links"]["categories"][0]["name"] == "Docs"


def test_registry_hides_the_tool_while_both_pages_are_off(db_session, monkeypatch):
    from app.services.tool_registry_service import ToolRegistryService

    ToolRegistryService.sync_local_tools(db_session)
    user = User(id="u@example.com", email="u@example.com", full_name="U", roles=["User"])
    _links(monkeypatch, LINKS)

    def names():
        return {
            getattr(t, "name", None)
            for t in ToolRegistryService.resolve_tools_for_surface(db_session, "main_agent", user)
        }

    _tabs(monkeypatch, training=False, community_links=False)
    assert "get_learning_resources" not in names()
    _tabs(monkeypatch, training=True, community_links=False)
    assert "get_learning_resources" in names()


def test_a_failing_predicate_leaves_the_tool_available(monkeypatch):
    """A broken settings read must not silently remove a tool from the assistant."""
    from app.tools.mcp import tool

    def boom():
        raise RuntimeError("settings unavailable")

    @tool(name="gated_probe", description="probe", available_when=boom)
    def probe() -> dict:
        return {}

    assert probe.is_available() is True


def test_tools_without_a_predicate_are_always_available():
    from app.tools.mcp import tool

    @tool(name="plain_probe", description="probe")
    def probe() -> dict:
        return {}

    assert probe.is_available() is True
