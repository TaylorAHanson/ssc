"""Tests for the client-side tag-policy check.

This mirrors the governance repo's own validation so the requester sees the
problem in the UI. The repo stays authoritative, which is why every "can't read
the policy" path here has to fail open — blocking submits because GitHub is slow
would be worse than a late error on the PR.

``POLICY_YAML`` is a copy of that repo's ``policy/tag_policy.yml``; keep them in
step, and note the app reads the live file at runtime, not this copy.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.tag_policy import TagPolicy, load_policy

POLICY_YAML = """
reserved_prefixes:
  - "system."
key_mode: open
known_keys:
  dataset:
    required: true
    description: Logical dataset this object belongs to.
    pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
  data_owner:
    required: true
    pattern: "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$"
  reliability_window:
    required: true
    pattern: "^[0-9]+(m|h|d|w)$"
  classification:
    required: false
    pattern: "^(public|internal|confidential|restricted)$"
protected_keys: []
limits:
  max_tags_per_object: 50
  max_key_length: 256
  max_value_length: 256
key_pattern: "^[A-Za-z0-9_][A-Za-z0-9._-]*$"
"""

POLICY = TagPolicy.parse(POLICY_YAML)
TABLE = "main.sales.orders"


def _change(set_tags=None, unset=None, table=TABLE):
    return [{"table": table, "set": set_tags or {}, "unset": unset or []}]


# ---------------------------------------------------------------------------
# What passes
# ---------------------------------------------------------------------------

def test_a_conforming_change_has_no_problems():
    assert POLICY.check(_change(
        {"data_owner": "sales-eng", "reliability_window": "24h", "classification": "internal"}
    )) == []


def test_unknown_keys_are_allowed_in_open_mode():
    assert POLICY.check(_change({"cost_center": "cc-1234"})) == []


def test_strict_mode_rejects_unknown_keys():
    strict = TagPolicy.parse(POLICY_YAML.replace("key_mode: open", "key_mode: strict"))
    problems = strict.check(_change({"cost_center": "cc-1234"}))
    assert len(problems) == 1
    assert "not a governed tag key" in problems[0]
    assert "data_owner" in problems[0]  # lists what is allowed


# ---------------------------------------------------------------------------
# What gets caught
# ---------------------------------------------------------------------------

def test_required_key_cannot_be_unset():
    """Clearing a certification-contract key would silently decertify the asset."""
    problems = POLICY.check(_change(unset=["data_owner"]))
    assert len(problems) == 1
    assert "cannot be removed" in problems[0]
    assert TABLE in problems[0]


def test_required_key_can_still_be_revalued():
    assert POLICY.check(_change({"data_owner": "new-team"})) == []


def test_optional_key_can_be_unset():
    assert POLICY.check(_change(unset=["classification"])) == []


def test_value_must_match_the_key_pattern():
    problems = POLICY.check(_change({"reliability_window": "soon"}))
    assert len(problems) == 1
    assert "not a valid value for 'reliability_window'" in problems[0]


def test_pattern_must_match_in_full_not_just_a_prefix():
    """A partial match would let '24h-ish' through as '24h'."""
    assert POLICY.check(_change({"reliability_window": "24h-ish"}))


def test_reserved_keys_are_rejected_on_both_set_and_unset():
    assert "reserved" in POLICY.check(_change({"system.certification_status": "certified"}))[0]
    assert "reserved" in POLICY.check(_change(unset=["system.certification_status"]))[0]


def test_malformed_key_is_rejected():
    problems = POLICY.check(_change({"bad key!": "v"}))
    assert any("aren't allowed" in p for p in problems)


def test_oversized_value_is_rejected():
    problems = POLICY.check(_change({"cost_center": "x" * 257}))
    assert any("the limit is 256" in p for p in problems)


def test_exceeding_the_per_object_tag_limit_is_rejected():
    problems = POLICY.check(_change({"cost_center": "cc-1"}), {TABLE: 51})
    assert any("at most 50" in p for p in problems)


def test_every_problem_is_reported_not_just_the_first():
    """One submit should surface the whole list, not one error per round trip."""
    problems = POLICY.check([
        {"table": "main.a.t1", "set": {"reliability_window": "soon"}, "unset": ["data_owner"]},
        {"table": "main.a.t2", "set": {"classification": "secret"}, "unset": []},
    ])
    assert len(problems) == 3
    assert {p.split(":")[0] for p in problems} == {"main.a.t1", "main.a.t2"}


# ---------------------------------------------------------------------------
# Loading — every failure must fail open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loads_the_policy_from_the_environment_branch():
    provider = MagicMock()
    provider.get_file_content = AsyncMock(return_value=POLICY_YAML)
    policy = await load_policy(provider, repo="tag-manager", ref="prod")
    provider.get_file_content.assert_awaited_once_with(
        repo="tag-manager", path="policy/tag_policy.yml", ref="prod"
    )
    assert policy.key_mode == "open"


@pytest.mark.asyncio
async def test_a_missing_policy_file_does_not_block_submission():
    provider = MagicMock()
    provider.get_file_content = AsyncMock(return_value=None)
    assert await load_policy(provider, repo="tag-manager", ref="dev") is None


@pytest.mark.asyncio
async def test_github_failure_does_not_block_submission():
    provider = MagicMock()
    provider.get_file_content = AsyncMock(side_effect=RuntimeError("502"))
    assert await load_policy(provider, repo="tag-manager", ref="dev") is None


@pytest.mark.asyncio
async def test_unparseable_policy_does_not_block_submission():
    provider = MagicMock()
    provider.get_file_content = AsyncMock(return_value="key_mode: [unclosed")
    assert await load_policy(provider, repo="tag-manager", ref="dev") is None


def test_an_empty_policy_permits_everything():
    """A policy stripped to nothing must not start rejecting arbitrary changes."""
    assert TagPolicy.parse("").check(_change({"anything": "goes"}, unset=["whatever"])) == []
