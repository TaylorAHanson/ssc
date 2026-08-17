"""Route-level guards on workflow writes.

Two writers touch these rows — the admin's browser and the authoring assistant —
and both used to be able to win silently. These cover the guards that stop that,
plus the publish path an editable Status field used to walk straight around.
"""
import pytest
from fastapi import HTTPException

from app.api.v1.workflows import (
    WorkflowCreate,
    WorkflowUpdate,
    create_workflow,
    update_workflow,
)
from app.models.user import User
from app.services.workflow_service import WorkflowService


def _admin() -> User:
    return User(id="a@corp.com", email="a@corp.com", full_name="A",
                roles=["Platform Admin"])


def _update(db, workflow_id, **fields):
    return update_workflow(
        workflow_id=workflow_id, db=db, body=WorkflowUpdate(**fields),
        current_user=_admin(),
    )


def test_saving_with_status_published_is_refused(db_session):
    """`POST /publish` validates the graph, compiles it, requires a request_type,
    bumps the version, and snapshots for rollback. Setting the status on the edit
    form did none of that, so it was a way to make a workflow live untested."""
    wf = WorkflowService.create(db_session, key="bypass", name="B", status="draft")
    with pytest.raises(HTTPException) as exc:
        _update(db_session, wf.id, status="published")
    assert exc.value.status_code == 400
    assert "publish" in str(exc.value.detail).lower()
    assert WorkflowService.get(db_session, wf.id).status == "draft"


def test_already_published_workflow_can_still_be_edited(db_session):
    """The guard is about the draft->live transition, not about editing a live
    workflow's prose."""
    wf = WorkflowService.create(db_session, key="live_edit", name="L",
                                request_type="live_edit", status="published")
    out = _update(db_session, wf.id, status="published", goal="clarified")
    assert out["goal"] == "clarified"


def test_creating_a_workflow_straight_into_published_is_refused(db_session):
    with pytest.raises(HTTPException) as exc:
        create_workflow(
            db=db_session,
            body=WorkflowCreate(key="new_live", name="N", status="published"),
            current_user=_admin(),
        )
    assert exc.value.status_code == 400


def test_stale_save_is_refused_with_409(db_session):
    """The demo bug's cousin: whoever saved second used to overwrite the first with
    no sign anything was lost."""
    wf = WorkflowService.create(db_session, key="concurrent", name="C", goal="first")
    stale = wf.updated_at.isoformat()

    # Somebody else (or the assistant) saves in between.
    WorkflowService.update(db_session, wf.id, goal="second")

    with pytest.raises(HTTPException) as exc:
        _update(db_session, wf.id, goal="third", if_unmodified_since=stale)
    assert exc.value.status_code == 409
    assert "changed since you loaded it" in str(exc.value.detail)
    assert WorkflowService.get(db_session, wf.id).goal == "second"


def test_save_with_the_current_timestamp_succeeds(db_session):
    wf = WorkflowService.create(db_session, key="fresh", name="F", goal="first")
    current = WorkflowService.get(db_session, wf.id).updated_at.isoformat()
    out = _update(db_session, wf.id, goal="second", if_unmodified_since=current)
    assert out["goal"] == "second"


def test_omitting_the_timestamp_keeps_older_clients_working(db_session):
    """The check is opt-in: a client that never sends it isn't locked out."""
    wf = WorkflowService.create(db_session, key="legacy_client", name="L", goal="a")
    assert _update(db_session, wf.id, goal="b")["goal"] == "b"


def _evaluate(db, **body_fields):
    from app.api.v1.workflows import SpecEvaluateRequest, evaluate_spec_endpoint

    return evaluate_spec_endpoint(
        body=SpecEvaluateRequest(graph_spec={"name": "wf", "stages": []},
                                 **body_fields),
        db=db, current_user=_admin(),
    )


def test_evaluate_scores_the_goal_against_the_published_menu(db_session):
    """The studio needs the same routing signal the assistant gets, since an admin
    can type a goal by hand and never see the assistant's warning."""
    WorkflowService.create(
        db_session, key="workspace_provision", name="WP", status="published",
        request_type="wp", goal="Provision a new Databricks workspace.",
    )
    report = _evaluate(
        db_session, key="workspace_access",
        goal="Provision a new Databricks workspace for a team.",
    )
    assert report["goal"]["score"] < 65
    assert [c["key"] for c in report["goal"]["summary"]["collisions"]] == [
        "workspace_provision"
    ]


def test_evaluate_omits_the_goal_score_when_no_goal_is_sent(db_session):
    """Existing callers (the evaluation modal) must be unaffected."""
    assert "goal" not in _evaluate(db_session)
