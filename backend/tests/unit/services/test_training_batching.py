"""Batched training loaders match the per-track / per-course queries they replace,
and the media upload rejects oversized files before reading them."""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.api.v1 import training as training_api
from app.core.config import settings
from app.services.training_service import TrainingService


@pytest.fixture
def catalog(db_session):
    t1 = TrainingService.create_track(db_session, name="Track One")
    t2 = TrainingService.create_track(db_session, name="Track Two")
    empty = TrainingService.create_track(db_session, name="Empty Track")
    c1 = TrainingService.create_course(db_session, track_id=t1.id, title="B course")
    c2 = TrainingService.create_course(db_session, track_id=t1.id, title="A course", status="draft")
    c3 = TrainingService.create_course(db_session, track_id=t2.id, title="C course")
    for title in ("m2", "m1"):
        TrainingService.create_media(
            db_session, course_id=c1.id, title=title, kind="video",
            source_filename=None, storage_path=None,
        )
    return {"tracks": [t1, t2, empty], "courses": [c1, c2, c3]}


def test_courses_by_track_matches_list_courses(db_session, catalog):
    track_ids = [t.id for t in catalog["tracks"]]

    batched = TrainingService.courses_by_track(db_session, track_ids)

    for tid in track_ids:
        assert [c.id for c in batched[tid]] == [c.id for c in TrainingService.list_courses(db_session, tid)]
    assert batched[catalog["tracks"][2].id] == []
    assert TrainingService.courses_by_track(db_session, []) == {}


def test_media_by_course_matches_list_media(db_session, catalog):
    course_ids = [c.id for c in catalog["courses"]]

    batched = TrainingService.media_by_course(db_session, course_ids)

    for cid in course_ids:
        assert [m.id for m in batched[cid]] == [m.id for m in TrainingService.list_media(db_session, cid)]
    assert len(batched[catalog["courses"][0].id]) == 2
    assert TrainingService.media_by_course(db_session, []) == {}


def test_track_to_dict_course_count_override_matches_query(db_session, catalog):
    track = catalog["tracks"][0]
    queried = TrainingService.track_to_dict(db_session, track)["course_count"]
    passed = TrainingService.track_to_dict(db_session, track, course_count=queried)["course_count"]
    assert queried == passed == 2  # counts drafts too, as before


def test_upload_rejects_declared_oversize_before_reading(db_session, catalog, monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_MAX_UPLOAD_MB", 1)

    class NeverRead(io.BytesIO):
        def read(self, *a, **k):
            raise AssertionError("oversized upload must be rejected before it is read")

    upload = UploadFile(NeverRead(), size=2 * 1024 * 1024, filename="big.mp4")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(training_api.admin_upload_media(
            course_id=catalog["courses"][0].id, file=upload, title=None, kind="video",
            current_user=None, db=db_session,
        ))
    assert exc_info.value.status_code == 413


def test_upload_stores_off_the_event_loop(db_session, catalog, monkeypatch):
    calls = {}

    class FakeStorage:
        def store_media(self, media_id, filename, content):
            import threading
            calls["thread"] = threading.current_thread().name
            return f"/local/{media_id}", len(content)

    monkeypatch.setattr(training_api, "TrainingMediaStorage", FakeStorage)
    user = type("U", (), {"email": "admin@example.com"})()
    upload = UploadFile(io.BytesIO(b"abc"), size=3, filename="clip.mp4")

    out = asyncio.run(training_api.admin_upload_media(
        course_id=catalog["courses"][0].id, file=upload, title="Clip", kind="video",
        current_user=user, db=db_session,
    ))

    assert out["title"] == "Clip" and out["size_bytes"] == 3
    assert calls["thread"] != "MainThread"
