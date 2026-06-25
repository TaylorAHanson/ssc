"""Unit tests for the read-only agent-profile loader.

Covers the pure helpers (ref resolution, frontmatter parsing, prompt
composition, the ``base``/standalone flag) and a ``get_profile`` happy path
with the low-level IO patched out — no WorkspaceClient / network needed.
"""
from __future__ import annotations

import base64

import pytest

from app.providers.profiles.client import (
    STORE_VOLUME,
    STORE_WORKSPACE,
    LoadedProfile,
    ProfileError,
    ProfileProvider,
    _parse_frontmatter,
    _resolve_ref,
)


# --------------------------------------------------------------- _resolve_ref

def test_resolve_ref_volume_path():
    store, path = _resolve_ref("/Volumes/c/s/v/.agents/my-agent")
    assert store == STORE_VOLUME
    assert path == "/Volumes/c/s/v/.agents/my-agent"


def test_resolve_ref_strips_trailing_agent_md():
    store, path = _resolve_ref("/Volumes/c/s/v/.agents/my-agent/AGENT.md")
    assert store == STORE_VOLUME
    assert path == "/Volumes/c/s/v/.agents/my-agent"


def test_resolve_ref_workspace_path():
    store, path = _resolve_ref("/Workspace/Users/me/.agents/x")
    assert store == STORE_WORKSPACE
    assert path == "/Workspace/Users/me/.agents/x"


def test_resolve_ref_opaque_id_roundtrip():
    raw = f"{STORE_VOLUME}|/Volumes/c/s/v/.agents/x"
    token = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    store, path = _resolve_ref(token)
    assert store == STORE_VOLUME
    assert path == "/Volumes/c/s/v/.agents/x"


def test_resolve_ref_empty_raises():
    with pytest.raises(ProfileError):
        _resolve_ref("")


def test_resolve_ref_bad_id_raises():
    with pytest.raises(ProfileError):
        _resolve_ref("!!!not-base64-or-path!!!")


# ----------------------------------------------------------- _parse_frontmatter

def test_parse_frontmatter_flow_and_block_tools():
    flow = "---\nname: A\ntools: [x, y]\n---\nbody"
    block = "---\nname: A\ntools:\n  - x\n  - y\n---\nbody"
    for text in (flow, block):
        meta = _parse_frontmatter(text)
        assert meta["name"] == "A"
        assert meta["tools"] == ["x", "y"]
        assert meta["body"] == "body"


def test_parse_frontmatter_no_block_is_all_body():
    meta = _parse_frontmatter("just markdown, no frontmatter")
    assert meta["body"] == "just markdown, no frontmatter"
    assert meta["tools"] == []


def test_parse_frontmatter_reads_base_and_model():
    meta = _parse_frontmatter("---\nname: A\nmodel: ep1\nbase: none\n---\nb")
    assert meta["model"] == "ep1"
    assert meta["base"] == "none"


# ------------------------------------------------------------- LoadedProfile

def test_system_prompt_composes_body_and_skills():
    p = LoadedProfile(
        store=STORE_VOLUME, dir_path="/x", name="A", prompt="Persona body",
        skills=[("Skill One", "do the thing"), ("Skill Two", "do another")],
    )
    sp = p.system_prompt()
    assert "Persona body" in sp
    assert "### Skill: Skill One" in sp
    assert "do the thing" in sp
    assert "### Skill: Skill Two" in sp


def test_system_prompt_empty_body_falls_back_to_name():
    p = LoadedProfile(store=STORE_VOLUME, dir_path="/x", name="MyAgent", prompt="")
    assert "# MyAgent" in p.system_prompt()


@pytest.mark.parametrize("value,expected", [
    ("full", False), ("", False), ("anything", False),
    ("none", True), ("standalone", True), ("OFF", True), ("False", True), ("no", True),
])
def test_standalone_flag(value, expected):
    p = LoadedProfile(store=STORE_VOLUME, dir_path="/x", name="A", base=value)
    assert p.standalone is expected


# --------------------------------------------------------------- get_profile

def test_get_profile_happy_path_volume(monkeypatch):
    prov = ProfileProvider()
    agent_md = "---\nname: Sales\nmodel: ep1\ntools: [run_sql, ask_your_data]\nbase: none\n---\nYou are a sales analyst."

    monkeypatch.setattr(prov, "_client", lambda obo: object())
    monkeypatch.setattr(
        prov, "_read_volume",
        lambda client, path: agent_md if path.endswith("AGENT.md") else None,
    )
    monkeypatch.setattr(prov, "_read_skills", lambda client, store, dir_path: [("S1", "body")])

    p = prov.get_profile("tok", "/Volumes/c/s/v/.agents/sales")
    assert p.name == "Sales"
    assert p.model == "ep1"
    assert p.tools == ["run_sql", "ask_your_data"]
    assert p.standalone is True
    assert p.skills == [("S1", "body")]
    assert "sales analyst" in p.prompt


def test_get_profile_missing_raises(monkeypatch):
    prov = ProfileProvider()
    monkeypatch.setattr(prov, "_client", lambda obo: object())
    monkeypatch.setattr(prov, "_read_volume", lambda client, path: None)
    with pytest.raises(ProfileError):
        prov.get_profile("tok", "/Volumes/c/s/v/.agents/missing")
