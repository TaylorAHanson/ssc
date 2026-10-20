"""Startup retirement of workflows removed from the product.

The bundled spec and instructions for a retired workflow are deleted, but rows an
earlier boot seeded (or an admin forked) stay in the DB. ``retire_workflows``
must take them out of the agent's reach every boot without losing their history.
"""
import logging

from app.services.workflow_service import RETIRED_WORKFLOW_KEYS, WorkflowService

_SPEC = {"name": "x", "stages": [{"kind": "gate", "name": "mgr", "type": "manager",
                                  "waiting_status": "manager_approval"}]}


def _seed_retired_rows(db):
    WorkflowService.create(db, key="data_access_request", name="Data Access", source="seed",
                           request_type="data_access_request", status="published",
                           graph_spec=_SPEC)
    WorkflowService.create(db, key="request_data_access", name="Legacy", status="published")
    # An admin fork under a different key still serves the retired request type.
    WorkflowService.create(db, key="finance_data_access", name="Finance Data Access",
                           source="user", request_type="batch_data_access",
                           status="published", graph_spec=_SPEC)
    WorkflowService.create(db, key="catalog_schema_table_access", name="CST", status="draft")


def test_retires_published_rows_by_key_or_request_type(db_session):
    _seed_retired_rows(db_session)
    WorkflowService.create(db_session, key="workspace_access", name="Workspace Access",
                           request_type="workspace_access", status="published",
                           graph_spec=_SPEC)

    assert WorkflowService.retire_workflows(db_session) == 4

    for key in ("data_access_request", "request_data_access", "finance_data_access",
                "catalog_schema_table_access"):
        row = WorkflowService.get_by_key(db_session, key)
        assert (row.status, row.disabled) == ("draft", True), key
        assert row.graph_spec == (_SPEC if key in ("data_access_request", "finance_data_access")
                                  else None)  # kept for history, not deleted

    untouched = WorkflowService.get_by_key(db_session, "workspace_access")
    assert (untouched.status, untouched.disabled) == ("published", False)

    published = {w.key for w in WorkflowService.list_published(db_session)}
    assert published.isdisjoint(RETIRED_WORKFLOW_KEYS | {"finance_data_access"})
    assert "workspace_access" in published


def test_is_idempotent(db_session, caplog):
    _seed_retired_rows(db_session)
    with caplog.at_level(logging.INFO, logger="app.services.workflow_service"):
        assert WorkflowService.retire_workflows(db_session) == 4
        caplog.clear()
        assert WorkflowService.retire_workflows(db_session) == 0
    assert "Retired" not in caplog.text


def test_logs_what_it_retired(db_session, caplog):
    _seed_retired_rows(db_session)
    with caplog.at_level(logging.INFO, logger="app.services.workflow_service"):
        WorkflowService.retire_workflows(db_session)
    assert "data_access_request (was published)" in caplog.text
    assert "finance_data_access (was published)" in caplog.text


def test_retires_the_gitops_tag_change_workflow(db_session):
    WorkflowService.create(db_session, key="tag_change", name="Tag change", source="seed",
                           request_type="tag_change", status="published", graph_spec=_SPEC)
    assert WorkflowService.retire_workflows(db_session) == 1
    row = WorkflowService.get_by_key(db_session, "tag_change")
    assert (row.status, row.disabled) == ("draft", True)


def test_no_rows_is_a_no_op(db_session):
    assert WorkflowService.retire_workflows(db_session) == 0


def test_warns_about_workflows_that_compose_a_retired_one(db_session, caplog):
    composite = {"name": "onboarding", "stages": [
        {"kind": "subworkflow", "name": "grant_data", "ref": "data_access_request"},
    ]}
    WorkflowService.create(db_session, key="team_onboarding", name="Onboarding",
                           request_type="team_onboarding", status="published",
                           graph_spec=composite)
    with caplog.at_level(logging.WARNING, logger="app.services.workflow_service"):
        WorkflowService.retire_workflows(db_session)
    assert "team_onboarding" in caplog.text and "data_access_request" in caplog.text
    # Left published: an admin decides how to rework it.
    assert WorkflowService.get_by_key(db_session, "team_onboarding").status == "published"
