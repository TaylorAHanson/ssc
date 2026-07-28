"""Tests for the ``pr_merge`` gate resolving itself from GitHub.

Merging the PR is what makes the governance repo apply the tag change, so the
gate has no in-app approval to wait on — the poller has to observe GitHub. These
cover the three outcomes that matter: merged advances, closed-unmerged rejects,
and anything else leaves the request waiting rather than guessing.
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.poller import _pr_gate_from_github, _v2_resume_value

REQUEST = types.SimpleNamespace(id="req-1a2b3c4d")


def _pr_created_fact(repo="tag-manager", pr_number=42):
    return types.SimpleNamespace(event_data={"repo": repo, "pr_number": pr_number})


async def _resolve(pr_state, fact=None, github_raises=None):
    """Run the gate against a stubbed GitHub, returning (resume_value, facts)."""
    provider = MagicMock()
    provider.get_pull_request = AsyncMock(
        side_effect=github_raises) if github_raises else AsyncMock(return_value=pr_state)

    db = MagicMock()
    with patch("app.state_machines.facts.get_latest_fact",
               return_value=_pr_created_fact() if fact is None else fact), \
            patch("app.state_machines.facts.add_fact") as add_fact, \
            patch("app.workflows.tools._get_github_provider", return_value=provider):
        value = await _pr_gate_from_github(db, REQUEST)
    return value, add_fact, provider


@pytest.mark.asyncio
async def test_merged_pr_advances_the_gate():
    value, add_fact, provider = await _resolve(
        {"merged": True, "merged_at": "2026-07-27T23:00:00Z",
         "merged_by": {"login": "governance-admin"}, "state": "closed"}
    )
    assert value == {"approved": True}
    provider.get_pull_request.assert_awaited_once_with(repo="tag-manager", number=42)
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == ("req-1a2b3c4d", "pr_merged")
    assert payload["merged_by"] == "governance-admin"


@pytest.mark.asyncio
async def test_open_pr_keeps_waiting():
    value, add_fact, _ = await _resolve({"state": "open", "merged": False})
    assert value is None
    add_fact.assert_not_called()


@pytest.mark.asyncio
async def test_closed_without_merge_rejects_the_request():
    """Declining the change must not leave the request waiting forever."""
    value, add_fact, _ = await _resolve(
        {"state": "closed", "merged": False, "closed_at": "2026-07-27T23:00:00Z"}
    )
    assert value["approved"] is False
    assert "without merging" in value["reason"]
    assert add_fact.call_args.args[2] == "pr_closed_unmerged"


@pytest.mark.asyncio
async def test_github_being_down_leaves_the_gate_waiting():
    """A failed lookup must not be read as a rejection."""
    value, add_fact, _ = await _resolve(None, github_raises=RuntimeError("502"))
    assert value is None
    add_fact.assert_not_called()


@pytest.mark.asyncio
async def test_missing_pr_created_fact_does_not_call_github():
    value, add_fact, provider = await _resolve({"state": "open"}, fact=False)
    assert value is None
    provider.get_pull_request.assert_not_awaited()
    add_fact.assert_not_called()


@pytest.mark.asyncio
async def test_manual_pr_merged_fact_still_overrides_github():
    """An admin marking it merged short-circuits the lookup entirely."""
    result = types.SimpleNamespace(
        interrupted=True, interrupt_payload={"type": "pr_merge"})
    db = MagicMock()
    with patch("app.state_machines.facts.has_fact",
               side_effect=lambda _db, _rid, ft: ft == "pr_merged"), \
            patch("app.workers.poller._pr_gate_from_github") as from_github:
        value = await _v2_resume_value(db, REQUEST, result)
    assert value == {"approved": True}
    from_github.assert_not_called()
