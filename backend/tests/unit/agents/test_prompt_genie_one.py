"""The prompt sends data analysis to Genie One, with a link it never has to invent."""
from __future__ import annotations

from app.agents import prompts
from app.core import config, settings_store


def _setup(monkeypatch, url="", workspaces=True):
    monkeypatch.setitem(config._yaml_config, "links", {"genie_one_url": url})
    monkeypatch.setattr(settings_store, "effective_ui_tabs", lambda: {"workspaces": workspaces})


def test_the_account_level_link_is_given_when_an_admin_set_one(monkeypatch):
    _setup(monkeypatch, url="accounts.example.com/one")
    section = prompts._get_genie_one_section()
    assert "[Genie One](https://accounts.example.com/one)" in section
    assert "No account-level Genie One link" not in section


def test_without_a_link_it_falls_back_to_the_workspaces_own_genie_one(monkeypatch):
    _setup(monkeypatch)
    section = prompts._get_genie_one_section()
    assert "No account-level Genie One link is set up" in section
    assert "`<host>/one`" in section and "get_target_workspaces" in section
    assert "Never make up a URL" in section


def test_the_workspaces_page_is_mentioned_only_while_it_is_on(monkeypatch):
    _setup(monkeypatch, workspaces=True)
    assert "(/workspaces)" in prompts._get_genie_one_section()
    _setup(monkeypatch, workspaces=False)
    assert "/workspaces" not in prompts._get_genie_one_section()


def test_the_runtime_prompt_refuses_analysis_but_keeps_discovery(monkeypatch):
    _setup(monkeypatch, url="https://accounts.example.com/one")
    prompt = prompts.get_agent_prompt(tools_override=[])
    assert "## Genie One (where data analysis happens)" in prompt
    assert "You do not analyze it" in prompt
    assert "Never run `run_sql` to answer, preview or sanity-check the analysis" in prompt
    # Access probes and quick looks still use SQL.
    assert "Use `run_sql` only for an access probe or a quick look at one table" in prompt
    # Charts of the app's own activity stay allowed.
    assert '"plot requests over time"' in prompt


def test_the_authoring_prompt_has_no_genie_one_section(monkeypatch):
    _setup(monkeypatch, url="https://accounts.example.com/one")
    assert "## Genie One" not in prompts.get_agent_prompt(tools_override=[], mode="authoring")


def test_a_settings_failure_never_breaks_prompt_assembly(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(settings_store, "effective_ui_tabs", boom)
    assert "No account-level Genie One link" in prompts._get_genie_one_section()
