"""Tests for ``open_tag_change_pr``, the whole mutation of the tag-change flow.

The app never runs ``ALTER ... TAGS`` itself — it commits generated SQL to the
governance repo and merging the PR applies it. So the file this tool writes is a
cross-repo contract, and the governance repo rejects the PR on anything its
parser does not recognize. ``test_matches_the_governance_repo_fixture`` pins the
exact bytes against that repo's own ``valid_basic.sql`` fixture; the rest cover
the config and replay behavior that decides whether a request strands.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import PermanentError
from app.workflows.tag_sql import build_migration_file, build_tag_sql, migration_filename
from app.workflows.tools import open_tag_change_pr

REQUEST_ID = "req-1a2b3c4d"
SUBMITTED_AT = "2026-07-27T22:31:04+00:00"

CHANGES = [
    {
        "table": "main.sales.orders",
        "set": {"data_owner": "sales-eng", "access_group": "sales-readers"},
        "unset": ["classification"],
    },
    {"table": "main.sales.order_items", "set": {"data_owner": "sales-eng"}, "unset": []},
]

# What the governance repo's tests/fixtures/valid_basic.sql contains. Duplicated
# rather than read across repos so this suite runs standalone; if the two drift,
# that repo's parser is the one that decides, so update both together.
VALID_BASIC = """\
-- Tag change request: req-1a2b3c4d
-- Dataset: customer_360
-- Requested by: Jane Doe <jane.doe@example.com>
-- Generated: 2026-07-27T22:31:04+00:00

ALTER TABLE main.sales.orders SET TAGS ('data_owner' = 'sales-eng', 'access_group' = 'sales-readers');
ALTER TABLE main.sales.orders UNSET TAGS ('classification');
ALTER TABLE main.sales.order_items SET TAGS ('data_owner' = 'sales-eng');
"""


def _github():
    provider = AsyncMock()
    provider.create_branch = AsyncMock(return_value={})
    provider.create_or_update_file = AsyncMock(return_value={})
    provider.create_pull_request = AsyncMock(
        return_value={"number": 42, "html_url": "https://github.test/o/r/pull/42"}
    )
    return provider


async def _run(provider, settings_overrides=None, **overrides):
    """Invoke the tool with a stub GitHub provider and no real DB write."""
    from app.core.config import settings

    defaults = {
        "GOVERNANCE_TAGS_REPO": "tag-manager",
        "GOVERNANCE_TAGS_BASE_BRANCH": "dev",
        "GOVERNANCE_TAGS_PATH": "tags/migrations",
    }
    defaults.update(settings_overrides or {})

    args = {
        "dataset_name": "customer_360",
        "tags_sql": build_tag_sql(CHANGES),
        "submitted_at": SUBMITTED_AT,
        "requested_by": "Jane Doe",
        "requested_by_email": "jane.doe@example.com",
        "changes": CHANGES,
        "_request_id": REQUEST_ID,
    }
    args.update(overrides)

    with patch.multiple(settings, **defaults), \
            patch("app.workflows.tools._get_github_provider", return_value=provider), \
            patch("app.state_machines.facts.add_fact") as add_fact, \
            patch("app.db.session.get_db", return_value=iter([MagicMock()])):
        result = await open_tag_change_pr.execute(**args)
    return result, add_fact


# ---------------------------------------------------------------------------
# The cross-repo file contract
# ---------------------------------------------------------------------------

def test_matches_the_governance_repo_fixture():
    """Byte-for-byte equality with the fixture that repo's parser is tested on."""
    content = build_migration_file(
        request_id=REQUEST_ID,
        dataset_name="customer_360",
        requested_by="Jane Doe",
        requested_by_email="jane.doe@example.com",
        generated_at=datetime(2026, 7, 27, 22, 31, 4, tzinfo=timezone.utc),
        sql=build_tag_sql(CHANGES),
    )
    assert content == VALID_BASIC


def test_requested_by_falls_back_to_the_email_alone():
    """No display name must not yield a dangling '<...>' the parser chokes on."""
    content = build_migration_file(
        request_id=REQUEST_ID,
        dataset_name="d",
        requested_by=None,
        requested_by_email="jane.doe@example.com",
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        sql="ALTER TABLE a.b.c SET TAGS ('k' = 'v');",
    )
    assert "-- Requested by: jane.doe@example.com" in content
    assert "<" not in content


def test_migration_filename_sorts_by_submission_time():
    """Filename order is apply order, so it must track submission time."""
    earlier = migration_filename("req-b", datetime(2026, 7, 27, 1, 0, 0, tzinfo=timezone.utc))
    later = migration_filename("req-a", datetime(2026, 7, 27, 2, 0, 0, tzinfo=timezone.utc))
    assert earlier < later
    assert later == "20260727020000-req-a.sql"


# ---------------------------------------------------------------------------
# What the tool sends to GitHub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_opens_the_pr_against_the_configured_repo_and_branch():
    provider = _github()
    result, _ = await _run(provider)

    provider.create_branch.assert_awaited_once_with(
        repo="tag-manager", branch=f"tag-change/{REQUEST_ID}", from_branch="dev"
    )
    file_call = provider.create_or_update_file.await_args.kwargs
    assert file_call["repo"] == "tag-manager"
    assert file_call["path"] == f"tags/migrations/20260727223104-{REQUEST_ID}.sql"
    assert file_call["branch"] == f"tag-change/{REQUEST_ID}"
    assert file_call["message"] == f"Tag change: customer_360 ({REQUEST_ID})"
    assert file_call["content"] == VALID_BASIC

    pr_call = provider.create_pull_request.await_args.kwargs
    assert pr_call["repo"] == "tag-manager"
    assert pr_call["title"] == "Tag change: customer_360"
    assert pr_call["head"] == f"tag-change/{REQUEST_ID}"
    assert pr_call["base"] == "dev"

    assert result == {
        "pr_number": 42,
        "pr_url": "https://github.test/o/r/pull/42",
        "repo": "tag-manager",
        "branch": f"tag-change/{REQUEST_ID}",
        "base": "dev",
        "file_path": f"tags/migrations/20260727223104-{REQUEST_ID}.sql",
    }


@pytest.mark.asyncio
async def test_branch_is_cut_from_the_environment_branch_not_main():
    """The governance repo has no 'main'; a wrong base 404s at branch creation."""
    provider = _github()
    await _run(provider, settings_overrides={"GOVERNANCE_TAGS_BASE_BRANCH": "prod"})
    assert provider.create_branch.await_args.kwargs["from_branch"] == "prod"
    assert provider.create_pull_request.await_args.kwargs["base"] == "prod"


@pytest.mark.asyncio
async def test_replay_targets_the_same_file():
    """A retried step must update its migration, not append a second one."""
    first, _ = await _run(_github())
    second, _ = await _run(_github())
    assert first["file_path"] == second["file_path"]


@pytest.mark.asyncio
async def test_records_the_pr_as_a_flat_fact():
    """The gate poller and the changes list read pr_url/pr_number off the top level."""
    _, add_fact = await _run(_github())
    add_fact.assert_called_once()
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == (REQUEST_ID, "pr_created")
    assert payload["pr_url"] == "https://github.test/o/r/pull/42"
    assert payload["pr_number"] == 42


@pytest.mark.asyncio
async def test_pr_body_summarizes_every_table():
    provider = _github()
    await _run(provider)
    body = provider.create_pull_request.await_args.kwargs["body"]
    assert "main.sales.orders" in body
    assert "main.sales.order_items" in body
    assert REQUEST_ID in body


# ---------------------------------------------------------------------------
# Refusals — each of these would otherwise strand the request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_repo_is_refused_before_any_github_call():
    provider = _github()
    with pytest.raises(PermanentError, match="GOVERNANCE_TAGS_REPO"):
        await _run(provider, settings_overrides={"GOVERNANCE_TAGS_REPO": ""})
    provider.create_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_base_branch_is_refused_rather_than_guessed():
    provider = _github()
    with pytest.raises(PermanentError, match="GOVERNANCE_TAGS_BASE_BRANCH"):
        await _run(provider, settings_overrides={"GOVERNANCE_TAGS_BASE_BRANCH": ""})
    provider.create_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_sql_is_refused():
    """An empty migration would merge as a silent no-op and look like success."""
    provider = _github()
    with pytest.raises(PermanentError, match="no SQL"):
        await _run(provider, tags_sql="   ")
    provider.create_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_outside_a_request_is_refused():
    """The request id names the branch and file; there is no safe substitute."""
    provider = _github()
    with pytest.raises(PermanentError, match="request workflow"):
        await _run(provider, _request_id=None)
    provider.create_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unparseable_submitted_at_still_opens_the_pr():
    """A bad timestamp degrades to 'now' rather than failing the request."""
    provider = _github()
    result, _ = await _run(provider, submitted_at="not-a-date")
    assert result["file_path"].endswith(f"-{REQUEST_ID}.sql")
    provider.create_pull_request.assert_awaited_once()
