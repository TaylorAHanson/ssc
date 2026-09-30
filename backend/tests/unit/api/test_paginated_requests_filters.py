"""GET /requests/paginated: exclude_type and created_after filters."""

from datetime import datetime, timedelta, timezone

import orjson
import pytest

from app.api.v1.requests import get_paginated_requests
from app.db import RequestModel
from app.models.user import User

ADMIN = User(id="admin", email="admin@example.com", full_name="Admin", roles=["Platform Admin"], is_active=True)
NOW = datetime(2026, 9, 30, 12, 0, 0)


@pytest.fixture
def seeded(db_session):
    rows = [
        ("req-scan-new", "enforcement_sentinel", None, NOW - timedelta(hours=1)),
        ("req-report-new", "report_execution", None, NOW - timedelta(hours=2)),
        ("req-person-new", "github_repo_creation", "a@example.com", NOW - timedelta(days=1)),
        ("req-person-old", "data_access_request", "b@example.com", NOW - timedelta(days=60)),
    ]
    for rid, rtype, email, created in rows:
        db_session.add(RequestModel(
            id=rid, type=rtype, title=rid, status="completed", current_state="completed",
            requester_email=email, created_at=created, updated_at=created,
        ))
    db_session.commit()


def _ids(db_session, exclude_type=None, created_after=None):
    resp = get_paginated_requests(
        skip=0, limit=100, type=None, search=None, summary=True,
        exclude_type=exclude_type, created_after=created_after,
        current_user=ADMIN, db=db_session,
    )
    body = orjson.loads(resp.body)
    ids = {item["id"] for item in body["items"]}
    assert body["total"] == len(ids)
    return ids


def test_exclude_type_drops_listed_types(db_session, seeded):
    ids = _ids(db_session, exclude_type=["enforcement_sentinel", "report_execution"])
    assert ids == {"req-person-new", "req-person-old"}


def test_created_after_accepts_aware_datetimes(db_session, seeded):
    cutoff = (NOW - timedelta(days=7)).replace(tzinfo=timezone.utc)
    ids = _ids(db_session, created_after=cutoff)
    assert ids == {"req-scan-new", "req-report-new", "req-person-new"}


def test_filters_combine(db_session, seeded):
    ids = _ids(db_session, exclude_type=["enforcement_sentinel"], created_after=NOW - timedelta(days=7))
    assert ids == {"req-report-new", "req-person-new"}
