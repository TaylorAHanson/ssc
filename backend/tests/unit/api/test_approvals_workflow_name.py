"""The inbox labels each approval with its workflow's display name.

The Pending Approvals page filters by workflow, so each item carries the name an
admin gave the workflow (Admin -> Workflows) rather than only its request type.
"""
from app.api.v1.approvals import get_approval, get_approvals
from app.db import ApprovalModel, RequestModel, WorkflowModel
from app.models.user import User

ADMIN = User(id="admin@example.com", email="admin@example.com", full_name="Admin",
             roles=["Platform Admin"], entitlements=["admin@example.com"], is_active=True)


def _add_request(db, request_id, request_type):
    db.add(RequestModel(id=request_id, type=request_type, title=f"Request {request_id}",
                        status="pending", requester_email="requester@example.com"))
    db.add(ApprovalModel(id=f"appr-{request_id}", request_id=request_id, approval_type="platform_admin",
                         status="pending", requested_by="requester@example.com",
                         requested_by_email="requester@example.com"))


def _add_workflow(db, key, name, request_type=None, status="published"):
    db.add(WorkflowModel(id=f"wf-{key}", key=key, name=name, request_type=request_type, status=status))


def _names(db):
    return {a.requestId: a.workflowName for a in get_approvals(current_user=ADMIN, db=db)}


def test_inbox_carries_the_workflow_display_name(db_session):
    _add_workflow(db_session, "rbac_access_change", "Access change", request_type="rbac_access_change")
    _add_request(db_session, "req-1", "rbac_access_change")
    _add_request(db_session, "req-legacy", "data_access_request")
    db_session.commit()

    assert _names(db_session) == {"req-1": "Access change", "req-legacy": None}
    assert get_approval("appr-req-1", current_user=ADMIN, db=db_session).workflowName == "Access change"


def test_published_request_type_match_wins(db_session):
    _add_workflow(db_session, "tag_change_fork", "Tag change (draft fork)", request_type="tag_change",
                  status="draft")
    _add_workflow(db_session, "tag_change", "Tag Change", request_type="tag_change")
    _add_workflow(db_session, "legacy_tag_change", "Old tag change", request_type="tag_change_v0")
    _add_workflow(db_session, "keyed_only", "Keyed only", request_type=None)
    _add_request(db_session, "req-tag", "tag_change")
    _add_request(db_session, "req-key", "keyed_only")
    db_session.commit()

    assert _names(db_session) == {"req-tag": "Tag Change", "req-key": "Keyed only"}
