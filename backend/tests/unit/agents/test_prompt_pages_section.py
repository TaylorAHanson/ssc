"""What the assistant's prompt says about tools, training, community links and Genie.

The prompt used to restate every tool's schema, dump the training and community-link
content, and describe ``ask_your_data``. The model already gets tool definitions
natively, the content is fetched with ``get_learning_resources``, and the Genie tool
carries its own warning. These tests keep that out of the prompt, and keep the page
guidance honest about which pages are switched on.
"""
from __future__ import annotations

import pytest

from app.agents import prompts
from app.core import config, settings_store
from app.tools.mcp import tool

LINKS = [{"name": "Docs", "links": ["Databricks docs | https://docs.databricks.com | book | Official docs"]}]


def _tabs(monkeypatch, **tabs):
    monkeypatch.setattr(settings_store, "effective_ui_tabs", lambda: dict(tabs))


@pytest.fixture
def probe_tool():
    @tool(name="zz_prompt_probe", description="UNIQUE-PROBE-DESCRIPTION do a thing")
    def zz_prompt_probe() -> dict:
        return {}

    return zz_prompt_probe


@pytest.fixture(autouse=True)
def _links(monkeypatch):
    monkeypatch.setitem(config._yaml_config, "community_links", {"enabled": True, "categories": LINKS})


# ------------------------------------------------------------------ cuts


def test_runtime_prompt_does_not_restate_tools(probe_tool, monkeypatch):
    _tabs(monkeypatch, training=True, community_links=True)
    prompt = prompts.get_agent_prompt(tools_override=[probe_tool])
    assert "## Available Tools" not in prompt
    assert "UNIQUE-PROBE-DESCRIPTION" not in prompt
    assert "zz_prompt_probe" not in prompt


def test_authoring_prompt_keeps_its_tool_list(probe_tool):
    prompt = prompts.get_agent_prompt(tools_override=[probe_tool], mode="authoring")
    assert "## Available Tools" in prompt
    assert "UNIQUE-PROBE-DESCRIPTION" in prompt


def test_runtime_prompt_no_longer_dumps_content_files(monkeypatch):
    _tabs(monkeypatch, training=True, community_links=True)
    prompt = prompts.get_agent_prompt(tools_override=[])
    assert "Community Resources & Content" not in prompt
    assert "```json" not in prompt
    assert not hasattr(prompts, "_get_cached_content_section")


def test_runtime_prompt_does_not_mention_the_genie_tool(monkeypatch):
    """It is just a tool: its own description carries the slowness warning."""
    _tabs(monkeypatch, training=True, community_links=True)
    prompt = prompts.get_agent_prompt(tools_override=[])
    assert "ask_your_data" not in prompt
    assert "Asking Genie" not in prompt


# -------------------------------------------------------- page guidance


def test_pages_section_follows_the_settings(monkeypatch):
    _tabs(monkeypatch, training=True, community_links=True, data_discovery=True, templates_assets=True)
    section = prompts._get_pages_section()
    assert "get_learning_resources" in section
    assert "(/community/training)" in section
    assert "(/community/links)" in section
    assert "(/community/assets)" in section
    assert "(/discovery)" in section

    _tabs(monkeypatch, training=False, community_links=False, data_discovery=False, templates_assets=False)
    assert prompts._get_pages_section() == ""


def test_pages_section_drops_a_page_that_is_off(monkeypatch):
    _tabs(monkeypatch, training=True, community_links=False, data_discovery=True, templates_assets=False)
    section = prompts._get_pages_section()
    assert "(/community/training)" in section
    assert "/community/links" not in section
    assert "/community/assets" not in section
    # The lookup tool is still offered while training is on.
    assert "get_learning_resources" in section

    _tabs(monkeypatch, training=False, community_links=True, data_discovery=False, templates_assets=False)
    section = prompts._get_pages_section()
    assert "/community/training" not in section
    assert "(/community/links)" in section
    assert "/discovery" not in section


def test_community_links_with_no_links_are_not_recommended(monkeypatch):
    monkeypatch.setitem(config._yaml_config, "community_links", {"enabled": True, "categories": []})
    _tabs(monkeypatch, training=False, community_links=True, data_discovery=False, templates_assets=False)
    assert prompts._get_pages_section() == ""


def test_pages_section_never_breaks_prompt_assembly(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(settings_store, "effective_ui_tabs", boom)
    assert prompts._get_pages_section() == ""
    assert prompts.get_agent_prompt(tools_override=[])  # still builds


def test_no_static_training_or_asset_links_remain_in_the_base_prompt(monkeypatch):
    """Everything about these pages is conditional, so with them all off none of it shows."""
    _tabs(monkeypatch, training=False, community_links=False, data_discovery=False, templates_assets=False)
    prompt = prompts.get_agent_prompt(tools_override=[])
    for fragment in ("/community/training", "/community/assets", "/community/links", "Databricks Academy"):
        assert fragment not in prompt, fragment
