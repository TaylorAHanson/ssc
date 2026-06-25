"""Unit tests for ``_apply_agent_profile`` (agent profile -> runner inputs).

Verifies the review-hardening behaviors:
  * prompt layering (default) vs standalone (``base: none``)
  * tool allowlist intersection, and the empty-intersection fallback
  * model allowlist gating
  * load failure fail-safe
  * observability counters
"""
from __future__ import annotations

import pytest

import app.api.v1.agent as agent_mod
from app.api.v1.agent import _apply_agent_profile, _apply_inline_profile, get_profile_metrics
from app.providers.profiles.client import LoadedProfile


class _Tool:
    """Minimal tool stand-in carrying what the prompt formatter reads."""

    def __init__(self, name: str):
        self.name = name
        self.description = f"fake tool {name}"
        self.input_schema = {"type": "object", "properties": {}, "required": []}
        self.required_role = None


class _FakeProvider:
    def __init__(self, profile=None, exc=None):
        self._profile = profile
        self._exc = exc

    def get_profile(self, obo_token, profile_ref):
        if self._exc is not None:
            raise self._exc
        return self._profile


def _patch_provider(monkeypatch, *, profile=None, exc=None):
    monkeypatch.setattr(
        "app.providers.profiles.get_profile_provider",
        lambda: _FakeProvider(profile=profile, exc=exc),
    )


@pytest.fixture(autouse=True)
def _reset_metrics():
    agent_mod._PROFILE_METRICS.clear()
    agent_mod._PROFILE_LOAD_MS_TOTAL.update({"sum": 0.0, "n": 0})
    yield


def _surface_tools():
    return [_Tool("a"), _Tool("b"), _Tool("c")]


# ------------------------------------------------------------------- prompt

def test_layered_prompt_keeps_base_and_adds_profile(monkeypatch):
    prof = LoadedProfile(store="volume", dir_path="/x", name="Sales", prompt="ACT AS SALES", base="full", tools=["a"])
    _patch_provider(monkeypatch, profile=prof)

    sp, tools, model = _apply_agent_profile("ref", "tok", _surface_tools(), {"email": "u@x.com"})

    assert "ACTIVE AGENT PROFILE" in sp          # layering wrapper present
    assert "ACT AS SALES" in sp                  # profile body present
    assert "CURRENT USER IDENTITY" in sp         # identity appended
    # minimal structural scaffold is the base — NOT the Self-Service persona
    assert "Output formatting" in sp
    assert "FinOps" not in sp                    # no Self-Service persona leaks in
    # the scaffold lists the granted tools
    assert "Available Tools" in sp


def test_empty_tool_allowlist_grants_no_tools(monkeypatch):
    # A blank/new profile (no tools selected) must NOT inherit the full surface,
    # otherwise an unconfigured agent describes itself like the Self-Service one.
    prof = LoadedProfile(store="volume", dir_path="/x", name="Blank", prompt="P", base="full", tools=[])
    _patch_provider(monkeypatch, profile=prof)

    sp, tools, _model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert tools == []                           # empty allowlist => no tools granted
    assert "Available Tools" not in sp           # scaffold omits the tool section


def test_standalone_prompt_replaces_base(monkeypatch):
    prof = LoadedProfile(store="volume", dir_path="/x", name="Narrow", prompt="ONLY ME", base="none")
    _patch_provider(monkeypatch, profile=prof)

    sp, _tools_out, _model = _apply_agent_profile("ref", "tok", _surface_tools(), {"email": "u@x.com"})

    assert "ONLY ME" in sp
    assert "CURRENT USER IDENTITY" in sp
    assert "ACTIVE AGENT PROFILE" not in sp      # no layering wrapper
    assert "Available Tools" not in sp           # base prompt omitted


# -------------------------------------------------------------------- tools

def test_tool_allowlist_narrows(monkeypatch):
    prof = LoadedProfile(store="volume", dir_path="/x", name="A", prompt="p", tools=["a", "b"])
    _patch_provider(monkeypatch, profile=prof)

    _sp, tools, _model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert {t.name for t in tools} == {"a", "b"}


def test_server_qualified_tool_ids_match_bare_names(monkeypatch):
    # Profiles store canonical "<server>/<tool>" ids; the runtime keys on bare
    # names. The matcher must bind "sql/a" to this surface's "a".
    prof = LoadedProfile(store="volume", dir_path="/x", name="A", prompt="p", tools=["sql/a", "genie/b"])
    _patch_provider(monkeypatch, profile=prof)

    _sp, tools, _model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert {t.name for t in tools} == {"a", "b"}


def test_empty_tool_intersection_falls_back_to_full(monkeypatch):
    prof = LoadedProfile(store="volume", dir_path="/x", name="A", prompt="p", tools=["zzz", "qqq"])
    _patch_provider(monkeypatch, profile=prof)

    _sp, tools, _model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert {t.name for t in tools} == {"a", "b", "c"}         # full surface restored
    assert get_profile_metrics().get("tool_fallback") == 1


# -------------------------------------------------------------------- model

def test_model_rejected_when_not_allowlisted(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "AGENT_PROFILE_MODEL_ALLOWLIST", "")
    prof = LoadedProfile(store="volume", dir_path="/x", name="A", prompt="p", model="ep1")
    _patch_provider(monkeypatch, profile=prof)

    _sp, _tools, model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert model is None
    assert get_profile_metrics().get("model_rejected") == 1


def test_model_allowed_when_allowlisted(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "AGENT_PROFILE_MODEL_ALLOWLIST", "ep1,ep2")
    prof = LoadedProfile(store="volume", dir_path="/x", name="A", prompt="p", model="ep1")
    _patch_provider(monkeypatch, profile=prof)

    _sp, _tools, model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert model == "ep1"


# ------------------------------------------------------------ inline (Try-it)

def test_inline_profile_applies_without_provider():
    spec = {
        "name": "Draft",
        "prompt": "DRAFT PERSONA",
        "base": "full",
        "tools": ["sql/a"],
        "skills": [{"name": "S1", "content": "skill body"}],
    }
    sp, tools, model = _apply_inline_profile(spec, _surface_tools(), {"email": "u@x.com"})
    assert "DRAFT PERSONA" in sp
    assert "skill body" in sp
    assert {t.name for t in tools} == {"a"}      # server-qualified id binds
    assert model is None                          # no model pinned
    assert get_profile_metrics().get("inline_applied") == 1


def test_inline_standalone_replaces_base():
    spec = {"name": "D", "prompt": "ONLY", "base": "none", "tools": []}
    sp, _tools, _model = _apply_inline_profile(spec, _surface_tools(), {})
    assert "ONLY" in sp
    assert "Available Tools" not in sp


# ----------------------------------------------------------------- fail-safe

def test_load_failure_falls_back_to_defaults(monkeypatch):
    from app.providers.profiles import ProfileError

    _patch_provider(monkeypatch, exc=ProfileError("nope"))

    sp, tools, model = _apply_agent_profile("ref", "tok", _surface_tools(), {})
    assert sp is None                                   # default prompt path
    assert {t.name for t in tools} == {"a", "b", "c"}   # full surface
    assert model is None
    assert get_profile_metrics().get("load_error") == 1
