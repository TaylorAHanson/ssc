"""Unit tests for Skill version history, rollback, and env export/import."""
import pytest

from app.services.skill_service import BUNDLE_FORMAT, SkillService


def _spec(name: str, stages=None):
    return {"name": name, "stages": stages or []}


def test_publish_snapshots_each_version(db_session):
    s = SkillService.create(db_session, key="vh", name="VH", goal="g1",
                            graph_spec=_spec("vh"), status="draft")
    SkillService.publish(db_session, s.id, published_by="a@b.com")
    SkillService.update(db_session, s.id, goal="g2")
    SkillService.publish(db_session, s.id, published_by="a@b.com")

    versions = SkillService.list_versions(db_session, s.id)
    assert [v.version for v in versions] == [3, 2]  # newest first; v1 default never published
    assert versions[0].goal == "g2"
    assert versions[1].goal == "g1"
    assert versions[0].published_by == "a@b.com"


def test_rollback_restores_body_as_draft(db_session):
    s = SkillService.create(db_session, key="rb", name="RB", goal="original",
                            graph_spec=_spec("rb"), status="draft")
    SkillService.publish(db_session, s.id)              # v2 snapshot: goal=original
    SkillService.update(db_session, s.id, goal="changed")
    SkillService.publish(db_session, s.id)              # v3 snapshot: goal=changed

    rolled = SkillService.rollback(db_session, s.id, 2)
    assert rolled.goal == "original"
    assert rolled.status == "draft"  # restored as draft for review before re-publish


def test_rollback_missing_version_raises(db_session):
    s = SkillService.create(db_session, key="rbx", name="X")
    with pytest.raises(ValueError):
        SkillService.rollback(db_session, s.id, 99)


def test_export_bundle_is_portable(db_session):
    SkillService.create(db_session, key="exp1", name="E1", graph_spec=_spec("exp1"),
                        request_type="exp1", status="published")
    SkillService.create(db_session, key="exp2", name="E2", status="draft")

    bundle = SkillService.export_bundle(db_session)
    assert bundle["format"] == BUNDLE_FORMAT
    keys = {e["key"] for e in bundle["skills"]}
    assert {"exp1", "exp2"} <= keys
    # No env-specific fields leak into the bundle.
    for e in bundle["skills"]:
        assert "id" not in e and "status" not in e and "version" not in e

    pub = SkillService.export_bundle(db_session, published_only=True)
    assert {e["key"] for e in pub["skills"]} == {"exp1"}


def test_import_bundle_creates_drafts_by_default(db_session):
    bundle = {
        "format": BUNDLE_FORMAT,
        "skills": [
            {"key": "imp_new", "name": "New", "graph_spec": _spec("imp_new"),
             "request_type": "imp_new"},
        ],
    }
    report = SkillService.import_bundle(db_session, bundle, created_by="a@b.com")
    assert report["created"] == ["imp_new"]
    created = SkillService.get_by_key(db_session, "imp_new")
    assert created.status == "draft"          # safe: review/test before publish
    assert created.source == "import"


def test_import_overwrite_vs_skip(db_session):
    SkillService.create(db_session, key="imp_up", name="Old", goal="old")
    bundle = {"format": BUNDLE_FORMAT,
              "skills": [{"key": "imp_up", "name": "New", "goal": "new"}]}

    skipped = SkillService.import_bundle(db_session, bundle, overwrite=False)
    assert skipped["skipped"] == ["imp_up"]
    assert SkillService.get_by_key(db_session, "imp_up").goal == "old"

    updated = SkillService.import_bundle(db_session, bundle, overwrite=True)
    assert updated["updated"] == ["imp_up"]
    assert SkillService.get_by_key(db_session, "imp_up").goal == "new"


def test_import_rejects_bad_format(db_session):
    with pytest.raises(ValueError):
        SkillService.import_bundle(db_session, {"format": "nope", "skills": []})


def test_import_reports_invalid_graph_spec(db_session):
    bundle = {
        "format": BUNDLE_FORMAT,
        "skills": [{"key": "bad", "name": "Bad",
                    "graph_spec": {"name": "", "stages": []}}],  # empty name -> invalid
    }
    report = SkillService.import_bundle(db_session, bundle)
    assert report["errors"] and report["errors"][0]["key"] == "bad"
    assert SkillService.get_by_key(db_session, "bad") is None
