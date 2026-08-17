"""Tests for scoring a workflow's ``goal``.

The goal is the workflow's entire line in the runtime agent's Capabilities menu
(``- <key>: <goal>``), so the only property that matters is whether it
DISCRIMINATES: a stub, a restatement of the key, or a line that reads like a
sibling's all route users to the wrong workflow. These tests pin the distinctions
that make the rubric worth trusting — chiefly that "vague" and "collides with
another workflow" are reported as different problems.
"""
from app.workflows.goal_quality import menu_siblings, score_goal


class _Row:
    def __init__(self, key, goal):
        self.key = key
        self.goal = goal


def _findings(result, severity=None):
    return [
        f["message"] for f in result["findings"]
        if severity is None or f["severity"] == severity
    ]


def test_empty_goal_is_critical_because_the_menu_line_is_blank():
    out = score_goal("", key="data_access_request")
    assert out["score"] == 0 and out["tier"] == "poor"
    assert out["findings"][0]["severity"] == "critical"
    assert "Capabilities menu" in out["findings"][0]["message"]


def test_auto_stub_scores_poor():
    """"Fulfill a campaign request." is what a workflow gets when nobody wrote a
    goal; it restates the key and carries no routing signal."""
    out = score_goal("Fulfill a campaign request.", key="campaign")
    assert out["tier"] == "poor"
    assert out["summary"]["is_stub"] is True
    assert any("auto-generated stub" in m for m in _findings(out, "high"))


def test_restating_the_key_is_caught_even_when_it_is_not_the_stub_wording():
    out = score_goal("Change a tag.", key="tag_change", name="Tag Change")
    assert out["score"] < 65
    assert any("repeats words already in the workflow key" in m for m in _findings(out))


def test_a_discriminating_goal_scores_well():
    out = score_goal(
        "Request read access to an existing schema, table, view, or volume for one "
        "user — not bulk sharing and not creating the asset.",
        key="data_access_request",
        siblings=[("workspace_provision", "Provision a new Databricks Workspace.")],
    )
    assert out["tier"] == "excellent"
    assert out["findings"] == []


def test_identical_goals_are_critical():
    out = score_goal(
        "Request access to a Databricks workspace.",
        key="workspace_access",
        siblings=[("workspace_provision", "Request access to a Databricks workspace.")],
    )
    assert any("IDENTICAL" in m for m in _findings(out, "critical"))
    assert out["summary"]["collisions"][0]["identical"] is True


def test_near_duplicate_sibling_is_reported_with_the_colliding_key():
    """The complaint this rubric exists for: two menu lines the agent must guess
    between. The finding has to NAME the other workflow to be actionable."""
    out = score_goal(
        "Request access to an existing Databricks workspace.",
        key="workspace_access",
        siblings=[
            ("workspace_provision", "Provision a new Databricks Workspace."),
            ("training_links", "Help the user find training schedules and materials."),
        ],
    )
    collisions = out["summary"]["collisions"]
    assert [c["key"] for c in collisions] == ["workspace_provision"]
    message = " ".join(_findings(out, "high"))
    assert "workspace_provision" in message
    # And it must say what to do about it, not just that it's bad.
    fixes = " ".join(f["fix"] for f in out["findings"])
    assert "existing vs. new" in fixes or "discriminator" in fixes


def test_unrelated_workflows_are_not_flagged_as_collisions():
    """Shared vocabulary ("Databricks", "user") must not make every workflow look
    like a duplicate, or the signal is worthless."""
    out = score_goal(
        "Verify that a user has completed a required training course before access "
        "is granted.",
        key="training_verification",
        siblings=[
            ("volume_creation", "Help the user create a new Unity Catalog volume."),
            ("service_principal", "Provision a new Service Principal for CI/CD."),
        ],
    )
    assert out["summary"]["collisions"] == []
    assert out["tier"] == "excellent"


def test_near_misses_are_reported_but_not_penalized():
    """A neighbour that merely shares vocabulary is worth showing the author
    without dragging the score down on every save. These two are the real seeded
    goals, which share "GitHub repository" but state opposite operations."""
    goal = "Create a new GitHub repository in the organization."
    clean = score_goal(goal, key="github_repo_creation")
    with_neighbour = score_goal(
        goal,
        key="github_repo_creation",
        siblings=[("github_repo_access",
                   "Help the user get access to an existing GitHub repository.")],
    )
    assert with_neighbour["score"] == clean["score"]
    assert with_neighbour["summary"]["collisions"] == []
    assert [n["key"] for n in with_neighbour["summary"]["similar_to"]] == [
        "github_repo_access"
    ]


def test_a_goal_that_is_too_terse_is_flagged():
    out = score_goal("Get a workspace.", key="workspace_access")
    assert any("too terse" in m for m in _findings(out))


def test_a_paragraph_goal_is_flagged_as_menu_bloat():
    """Every published goal sits in every conversation's system prompt, so a
    paragraph competes with the other lines instead of standing out."""
    long_goal = (
        "Act as an automated governance pipeline for Unity Catalog. Scan the target "
        "catalog against the reference catalog to detect near-duplicate assets, "
        "score each candidate pair, open a review task for the data owner, and then "
        "archive whichever copy the owner rejects while preserving lineage and the "
        "audit trail for the retained asset in every downstream workspace."
    )
    out = score_goal(long_goal, key="asset_deduplication")
    assert "system prompt" in " ".join(_findings(out, "low"))
    assert out["summary"]["chars"] > 240


def test_a_multi_sentence_goal_is_flagged():
    out = score_goal(
        "Scan the target catalog for duplicates. Score each pair. Ask the data "
        "owner which copy to archive.",
        key="asset_deduplication",
    )
    assert out["summary"]["sentences"] == 3
    assert any("one line per capability" in m for m in _findings(out, "low"))


def test_a_workflow_does_not_collide_with_itself():
    """Re-saving a workflow re-scores it against the published catalog, which
    already contains its own row."""
    out = score_goal(
        "Request access to an existing Databricks workspace, not a new one.",
        key="workspace_access",
        siblings=[("workspace_access",
                   "Request access to an existing Databricks workspace, not a new one.")],
    )
    assert out["summary"]["collisions"] == []


def test_menu_siblings_skips_the_workflow_itself_and_keeps_the_rest():
    rows = [
        _Row("workspace_access", "Request access to an existing workspace."),
        _Row("workspace_provision", "Provision a new workspace."),
        _Row("no_goal", None),
    ]
    out = menu_siblings(rows, exclude_key="workspace_access")
    assert ("workspace_access", rows[0].goal) not in out
    assert ("workspace_provision", "Provision a new workspace.") in out
    # A goal-less workflow is passed through; score_goal ignores it.
    assert ("no_goal", None) in out
