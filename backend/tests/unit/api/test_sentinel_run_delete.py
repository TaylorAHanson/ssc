"""DELETE /governance/sentinel/runs/{id} removes exactly one run, whatever its state."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api.v1.governance import delete_sentinel_run
from app.db import RequestModel
from app.db.sentinel_finding import SentinelFindingModel
from app.models.user import User


def _user(email, roles=()):
    return User(id=email, email=email, full_name=email, roles=list(roles), is_active=True)


_ADMIN = _user("admin@example.com", roles=["Platform Admin"])


def _run(db, run_id, *, status="completed", locked=False, created=None, type_="enforcement_sentinel"):
    db.add(RequestModel(
        id=run_id, type=type_, title="OmniGuard run", status=status, current_state=status,
        locked_by="poll-worker-old-1" if locked else None,
        locked_until=(datetime.utcnow() + timedelta(minutes=30)) if locked else None,
        created_at=created or datetime.utcnow(),
    ))
    db.add(SentinelFindingModel(
        id=f"{run_id}-f1", request_id=run_id, kind="violation", data={"resource_id": "r"},
    ))
    db.commit()


def _ids(db, model):
    return {r.id for r in db.query(model).all()}


def test_deletes_only_the_latest_run_even_while_it_holds_a_live_lock(db_session):
    _run(db_session, "run-old", created=datetime.utcnow() - timedelta(days=1))
    _run(db_session, "run-stuck", status="provisioning", locked=True)

    out = delete_sentinel_run("run-stuck", current_user=_ADMIN, db=db_session)

    assert out == {"deleted": True, "id": "run-stuck", "status": "provisioning"}
    assert _ids(db_session, RequestModel) == {"run-old"}
    assert _ids(db_session, SentinelFindingModel) == {"run-old-f1"}


def test_non_platform_admin_is_forbidden(db_session):
    _run(db_session, "run-1")
    governance_admin = _user("gov@example.com", roles=["Governance Admin"])
    with pytest.raises(HTTPException) as exc_info:
        delete_sentinel_run("run-1", current_user=governance_admin, db=db_session)
    assert exc_info.value.status_code == 403
    assert _ids(db_session, RequestModel) == {"run-1"}


def test_unknown_run_is_404(db_session):
    with pytest.raises(HTTPException) as exc_info:
        delete_sentinel_run("run-missing", current_user=_ADMIN, db=db_session)
    assert exc_info.value.status_code == 404


def test_refuses_to_delete_a_request_that_is_not_a_sentinel_run(db_session):
    _run(db_session, "req-access", type_="data_access")
    with pytest.raises(HTTPException) as exc_info:
        delete_sentinel_run("req-access", current_user=_ADMIN, db=db_session)
    assert exc_info.value.status_code == 404
    assert _ids(db_session, RequestModel) == {"req-access"}
