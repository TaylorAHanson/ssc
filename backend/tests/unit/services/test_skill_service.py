"""Unit tests for the DB-backed Skill service (V2 "workflows as data").

Covers the CRUD + draft/publish lifecycle and the idempotent filesystem seed
that backfills the legacy instruction markdown into the ``skills`` table.
"""
import pytest

from app.services.skill_service import SkillService


def test_create_and_get_by_key(db_session):
    skill = SkillService.create(
        db_session,
        created_by="admin@example.com",
        key="demo_skill",
        name="Demo Skill",
        goal="Do a demo thing",
        instructions_markdown="**Goal**: Do a demo thing",
        status="draft",
    )
    assert skill.id
    assert skill.status == "draft"
    assert skill.version == 1  # fresh skills start at v1; publish bumps it

    fetched = SkillService.get_by_key(db_session, "demo_skill")
    assert fetched is not None
    assert fetched.id == skill.id


def test_duplicate_key_rejected(db_session):
    SkillService.create(db_session, key="dupe", name="A")
    with pytest.raises(ValueError):
        SkillService.create(db_session, key="dupe", name="B")


def test_publish_unpublish_lifecycle(db_session):
    skill = SkillService.create(db_session, key="lifecycle", name="L", status="draft")

    # Drafts are excluded from the published view the agent reads.
    assert skill.key not in {s.key for s in SkillService.list_published(db_session)}

    published = SkillService.publish(db_session, skill.id)
    assert published.status == "published"
    assert published.version == 2  # bumped from the default v1 on publish
    assert skill.key in {s.key for s in SkillService.list_published(db_session)}

    # Re-publishing bumps the version again (audit trail of edits).
    SkillService.update(db_session, skill.id, goal="updated goal")
    bumped = SkillService.publish(db_session, skill.id)
    assert bumped.version == 3

    drafted = SkillService.unpublish(db_session, skill.id)
    assert drafted.status == "draft"


def test_delete(db_session):
    skill = SkillService.create(db_session, key="to_delete", name="X")
    SkillService.delete(db_session, skill.id)
    assert SkillService.get(db_session, skill.id) is None


def test_seed_specs_from_catalog_backfills_and_is_idempotent(db_session):
    """The code workflow catalog is attached to skills as editable graph_spec."""
    n = SkillService.seed_specs_from_catalog(db_session)
    assert n >= 20  # ~22 workflows in the catalog

    ws = SkillService.get_by_key(db_session, "workspace_access")
    assert ws is not None
    assert ws.graph_spec and ws.graph_spec["name"] == "workspace_access"
    assert ws.request_type == "workspace_access"

    # Re-running is a no-op (never clobbers).
    assert SkillService.seed_specs_from_catalog(db_session) == 0


def test_seed_specs_does_not_clobber_existing_graph(db_session):
    custom = {"name": "workspace_access", "stages": []}
    SkillService.create(db_session, key="workspace_access", name="WS",
                        request_type="workspace_access", graph_spec=custom, status="published")
    SkillService.seed_specs_from_catalog(db_session)
    ws = SkillService.get_by_key(db_session, "workspace_access")
    assert ws.graph_spec == custom  # author's graph preserved


def test_seed_from_filesystem_is_idempotent(db_session):
    """Seeding imports legacy instruction files once; re-running inserts none."""
    first = SkillService.seed_from_filesystem(db_session)
    # There are legacy instruction markdown files in app/agents/instructions.
    assert first >= 1
    published_keys = {s.key for s in SkillService.list_published(db_session)}
    assert published_keys, "seeded skills should be published"

    # Second run must be a no-op (never clobbers admin edits).
    second = SkillService.seed_from_filesystem(db_session)
    assert second == 0
