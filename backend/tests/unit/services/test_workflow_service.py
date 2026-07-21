"""Unit tests for the DB-backed Workflow service (V2 "workflows as data").

Covers the CRUD + draft/publish lifecycle and the idempotent filesystem seed
that backfills the legacy instruction markdown into the ``workflows`` table.
"""
import pytest

from app.services.workflow_service import WorkflowService


def test_create_and_get_by_key(db_session):
    workflow = WorkflowService.create(
        db_session,
        created_by="admin@example.com",
        key="demo_workflow",
        name="Demo Workflow",
        goal="Do a demo thing",
        instructions_markdown="**Goal**: Do a demo thing",
        status="draft",
    )
    assert workflow.id
    assert workflow.status == "draft"
    assert workflow.version == 1  # fresh workflows start at v1; publish bumps it

    fetched = WorkflowService.get_by_key(db_session, "demo_workflow")
    assert fetched is not None
    assert fetched.id == workflow.id


def test_duplicate_key_rejected(db_session):
    WorkflowService.create(db_session, key="dupe", name="A")
    with pytest.raises(ValueError):
        WorkflowService.create(db_session, key="dupe", name="B")


def test_publish_unpublish_lifecycle(db_session):
    workflow = WorkflowService.create(db_session, key="lifecycle", name="L", status="draft")

    # Drafts are excluded from the published view the agent reads.
    assert workflow.key not in {s.key for s in WorkflowService.list_published(db_session)}

    published = WorkflowService.publish(db_session, workflow.id)
    assert published.status == "published"
    assert published.version == 2  # bumped from the default v1 on publish
    assert workflow.key in {s.key for s in WorkflowService.list_published(db_session)}

    # Re-publishing bumps the version again (audit trail of edits).
    WorkflowService.update(db_session, workflow.id, goal="updated goal")
    bumped = WorkflowService.publish(db_session, workflow.id)
    assert bumped.version == 3

    drafted = WorkflowService.unpublish(db_session, workflow.id)
    assert drafted.status == "draft"


def test_delete(db_session):
    workflow = WorkflowService.create(db_session, key="to_delete", name="X")
    WorkflowService.delete(db_session, workflow.id)
    assert WorkflowService.get(db_session, workflow.id) is None


def _spec_with_input():
    return {
        "name": "training_session_request",
        "stages": [
            {"kind": "gate", "name": "approval", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "approvals": ["manager"], "args": {
                 "to_email": {"$literal": "s@corp.com"},
                 "subject": {"$literal": "Request"},
                 "body": {"$concat": ["Topics: ", {"$var": "topics"}]},
             }},
        ],
    }


def test_create_persists_canonical_execution_block(db_session):
    """Authoring prose is stored WITH the graph-derived execute_workflow block."""
    wf = WorkflowService.create(
        db_session, key="training_session_request", name="Training",
        request_type="training_session_request", graph_spec=_spec_with_input(),
        instructions_markdown="# Training\n\n**Goal**: schedule team training.",
        status="draft",
    )
    assert wf.instructions_markdown.startswith("# Training")
    assert wf.instructions_markdown.count("## Execution") == 1
    assert '"workflow_type": "training_session_request"' in wf.instructions_markdown
    assert '"topics"' in wf.instructions_markdown


def test_update_resyncs_execution_block_when_graph_changes(db_session):
    """Editing the graph refreshes the persisted execute_workflow block (no drift)."""
    wf = WorkflowService.create(
        db_session, key="resync", name="R", request_type="resync",
        graph_spec={"name": "resync", "stages": [
            {"kind": "step", "name": "n", "tool": "send_notification",
             "args": {"to_email": {"$literal": "a@b"}, "body": {"$var": "topics"}}}]},
        instructions_markdown="# R\n\n**Goal**: x.",
        status="draft",
    )
    assert '"topics"' in wf.instructions_markdown

    # Change the graph to reference a different input; the stored block follows.
    updated = WorkflowService.update(db_session, wf.id, graph_spec={
        "name": "resync", "stages": [
            {"kind": "step", "name": "n", "tool": "send_notification",
             "args": {"to_email": {"$literal": "a@b"}, "body": {"$var": "headcount"}}}]})
    assert updated.instructions_markdown.count("## Execution") == 1
    assert '"headcount"' in updated.instructions_markdown
    assert '"topics"' not in updated.instructions_markdown


def test_create_without_graph_leaves_instructions_untouched(db_session):
    """Legacy/code workflows with no graph keep their hand-written instructions as-is."""
    wf = WorkflowService.create(
        db_session, key="legacy", name="L",
        instructions_markdown="# Legacy\n\nDo the thing.",
        status="draft",
    )
    assert wf.instructions_markdown == "# Legacy\n\nDo the thing."


def test_create_with_graph_but_blank_instructions_generates_baseline(db_session):
    """Manual-Save path (blank prose + a graph) gets the 'never empty' baseline too."""
    wf = WorkflowService.create(
        db_session, key="manual_blank", name="M",
        request_type="manual_blank", graph_spec=_spec_with_input(),
        instructions_markdown="",  # editor textarea was empty
        status="draft",
    )
    assert wf.instructions_markdown and wf.instructions_markdown.strip()
    assert "Information to Gather" in wf.instructions_markdown
    assert wf.instructions_markdown.count("## Execution") == 1
    assert '"topics"' in wf.instructions_markdown


def test_update_to_blank_instructions_regenerates_baseline(db_session):
    """Clearing instructions on a graph-backed workflow falls back to the baseline."""
    wf = WorkflowService.create(
        db_session, key="clearable", name="C", request_type="clearable",
        graph_spec=_spec_with_input(), instructions_markdown="# Mine\n\nstuff",
        status="draft",
    )
    updated = WorkflowService.update(db_session, wf.id, instructions_markdown="")
    assert updated.instructions_markdown and updated.instructions_markdown.strip()
    assert "Information to Gather" in updated.instructions_markdown


def test_seed_specs_from_catalog_backfills_and_is_idempotent(db_session):
    """The code workflow catalog is attached to workflows as editable graph_spec."""
    n = WorkflowService.seed_specs_from_catalog(db_session)
    assert n >= 20  # ~22 workflows in the catalog

    ws = WorkflowService.get_by_key(db_session, "workspace_access")
    assert ws is not None
    assert ws.graph_spec and ws.graph_spec["name"] == "workspace_access"
    assert ws.request_type == "workspace_access"

    # Re-running is a no-op (never clobbers).
    assert WorkflowService.seed_specs_from_catalog(db_session) == 0


def test_deleting_seeded_workflow_is_not_re_seeded(db_session):
    """A deleted catalog/bundled workflow stays gone across the next boot's seed."""
    WorkflowService.seed_specs_from_catalog(db_session)
    ws = WorkflowService.get_by_key(db_session, "workspace_access")
    assert ws is not None

    WorkflowService.delete(db_session, ws.id, deleted_by="admin@example.com")
    assert WorkflowService.get_by_key(db_session, "workspace_access") is None

    # Simulate the next deploy re-running the seeders.
    WorkflowService.seed_specs_from_catalog(db_session)
    WorkflowService.seed_from_filesystem(db_session)
    assert WorkflowService.get_by_key(db_session, "workspace_access") is None


def test_recreating_deleted_key_clears_tombstone(db_session):
    """Re-creating a deleted key revives it and lets the catalog seed it again."""
    WorkflowService.seed_specs_from_catalog(db_session)
    ws = WorkflowService.get_by_key(db_session, "workspace_access")
    WorkflowService.delete(db_session, ws.id)
    assert WorkflowService.get_by_key(db_session, "workspace_access") is None

    WorkflowService.create(
        db_session, key="workspace_access", name="WS",
        request_type="workspace_access", status="published",
    )
    # Tombstone cleared, so a subsequent catalog seed backfills its graph again.
    WorkflowService.seed_specs_from_catalog(db_session)
    revived = WorkflowService.get_by_key(db_session, "workspace_access")
    assert revived is not None and revived.graph_spec


def test_seed_specs_does_not_clobber_existing_graph(db_session):
    custom = {"name": "workspace_access", "stages": []}
    WorkflowService.create(db_session, key="workspace_access", name="WS",
                        request_type="workspace_access", graph_spec=custom, status="published")
    WorkflowService.seed_specs_from_catalog(db_session)
    ws = WorkflowService.get_by_key(db_session, "workspace_access")
    assert ws.graph_spec == custom  # author's graph preserved


def test_seed_from_filesystem_is_idempotent(db_session):
    """Seeding imports legacy instruction files once; re-running inserts none."""
    first = WorkflowService.seed_from_filesystem(db_session)
    # There are legacy instruction markdown files in app/agents/instructions.
    assert first >= 1
    published_keys = {s.key for s in WorkflowService.list_published(db_session)}
    assert published_keys, "seeded workflows should be published"

    # Second run must be a no-op (unchanged files produce no writes).
    second = WorkflowService.seed_from_filesystem(db_session)
    assert second == 0


def test_seed_from_filesystem_resyncs_seed_rows(db_session):
    """Bundled .md edits re-deploy: a seed-owned row with stale prose is refreshed."""
    WorkflowService.seed_from_filesystem(db_session)
    row = WorkflowService.get_by_key(db_session, "workspace_access")
    assert row is not None and row.source == "seed"
    fresh = row.instructions_markdown

    # Simulate a stale DB row (seeded by an older bundle) and re-run the seed.
    row.instructions_markdown = "STALE"
    db_session.commit()

    changed = WorkflowService.seed_from_filesystem(db_session)
    assert changed >= 1
    row2 = WorkflowService.get_by_key(db_session, "workspace_access")
    assert row2.instructions_markdown == fresh  # re-synced from the .md
    assert row2.instructions_markdown != "STALE"


def test_seed_from_filesystem_does_not_clobber_user_edits(db_session):
    """Admin-edited (source='user') instructions survive the instruction re-sync."""
    WorkflowService.create(
        db_session, key="workspace_access", name="WS",
        request_type="workspace_access",
        instructions_markdown="# My custom guidance\nGather X.",
        status="published",
    )
    WorkflowService.seed_from_filesystem(db_session)
    row = WorkflowService.get_by_key(db_session, "workspace_access")
    assert row.source == "user"
    assert row.instructions_markdown.startswith("# My custom guidance")


# --- data-driven request-type registry -----------------------------------

def test_known_request_types_includes_catalog_and_published(db_session):
    """Known types come from the bundled catalog + published DB workflows +
    system constants — no enum gate."""
    from app.workflows.graphs.specs import SPECS

    known = WorkflowService.known_request_types(db_session)
    # Bundled defaults are valid even before any seeding runs.
    assert SPECS.keys() <= known
    # System constants are always known.
    assert {"enforcement_sentinel", "report_execution", "tag_change"} <= known

    # Publishing a brand-new type makes it known with no code change.
    WorkflowService.create(db_session, key="brand_new_flow", name="New",
                           request_type="brand_new_flow", status="published")
    refreshed = WorkflowService.known_request_types(db_session)
    assert "brand_new_flow" in refreshed


def test_is_known_request_type(db_session):
    assert WorkflowService.is_known_request_type(db_session, "workspace_access") is True
    assert WorkflowService.is_known_request_type(db_session, "totally_made_up") is False
    assert WorkflowService.is_known_request_type(db_session, "") is False
    assert WorkflowService.is_known_request_type(db_session, None) is False


def test_spec_requires_training_derived_from_training_gate(db_session):
    # workspace_provision has a training gate in the bundled catalog.
    assert WorkflowService.spec_requires_training(db_session, "workspace_provision") is True
    # workspace_access has no training gate.
    assert WorkflowService.spec_requires_training(db_session, "workspace_access") is False
    # Unknown / instruction-only types have no executable spec -> no training.
    assert WorkflowService.spec_requires_training(db_session, "totally_made_up") is False


def test_effective_spec_prefers_published_db_graph(db_session):
    """A published DB graph_spec overrides the bundled code catalog."""
    override = {"name": "workspace_access", "stages": [
        {"kind": "gate", "name": "training_pending", "type": "training"},
    ]}
    WorkflowService.create(db_session, key="workspace_access", name="WS",
                           request_type="workspace_access", graph_spec=override,
                           status="published")
    spec = WorkflowService.effective_spec(db_session, "workspace_access")
    assert spec == override
    # ...and the training derivation now follows the published override.
    assert WorkflowService.spec_requires_training(db_session, "workspace_access") is True
