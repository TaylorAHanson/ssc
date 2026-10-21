"""Academy catalog: code stems, feed parsing, sync, relinking, suggestions and gates."""

import asyncio
import json

import pytest

from app.db.training import TrainingCatalogCourseModel, TrainingCourseModel
from app.providers.training import academy
from app.services import training_catalog as tc
from app.services.training_service import TrainingService

BASE = "https://customer-academy.databricks.com"


def _item(id_course, code, name, course_type="elearning", price="0", language="english", duration=7200):
    return {
        "id_course": id_course, "code": code, "name": name, "course_type": course_type,
        "price": price, "language": language, "duration": duration,
        "slug_name": name.lower().replace(" ", "-"), "description": "<p>About &amp; more</p>",
        "category": {"name": "Data Engineering"}, "date_last_updated": "2026-09-01 10:00:00",
    }


def _courses(*items):
    return [academy.normalize(i, BASE) for i in items]


@pytest.fixture
def track(db_session):
    return TrainingService.create_track(db_session, name="Data Engineer")


# ------------------------------------------------------------------ stems + parsing

@pytest.mark.parametrize("code, stem", [
    ("ACAD-ALL-SLP-FREE-DINLC-EN-v1", "DINLC"),
    ("ACAD-CUST-SLP-DINLC-EN-v3", "DINLC"),
    ("ACAD-ALL-ILTPUB-ADEWD-JA-v3", "ADEWD"),
    ("ACAD-ALL-SLP-FREE-DMGUC-v2- RET", "DMGUC"),
    ("ACAD-ALL-SLP-FREE-GCPDPAF-v2.0.1", "GCPDPAF"),
    ("ACAD-ALL-SLP-DF-EN-v7", "DF"),
    ("ACAD-ALL-SLP-GSSQLBI-ENG-v4", "GSSQLBI"),
    ("advanced-mlops", None),
    ("", None),
    (None, None),
])
def test_course_stem(code, stem):
    assert academy.course_stem(code) == stem


def test_codes_match_accepts_other_versions_and_deliveries_only():
    assert academy.codes_match("ACAD-ALL-SLP-FREE-DINLC-EN-v1", ["ACAD-CUST-SLP-DINLC-EN-v3"])
    assert academy.codes_match("ACAD-ALL-SLP-DINLC-EN-v3", ["ACAD-ALL-ILTPUB-DINLC-EN-v2"])
    assert not academy.codes_match("ACAD-ALL-SLP-DINLC-EN-v3", ["ACAD-ALL-SLP-DWLJ-EN-v3"])
    assert academy.codes_match("custom-course", ["custom-course"])
    assert not academy.codes_match("custom-course", ["custom-course-2"])
    assert not academy.codes_match(None, ["anything"])


def test_normalize_builds_link_and_plain_text():
    c = academy.normalize(_item(42, "ACAD-ALL-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect"), BASE)
    assert c["url"] == f"{BASE}/learn/course/external/view/elearning/42/data-ingestion-with-lakeflow-connect"
    assert c["description"] == "About & more"
    assert c["is_free"] is True and c["stem"] == "DINLC" and c["category"] == "Data Engineering"
    classroom = academy.normalize(_item(7, "ACAD-ALL-ILTPUB-X-EN-v1", "X course", course_type="classroom"), BASE)
    assert "/view/classroom/7/" in classroom["url"]
    assert academy.normalize({"id_course": "nope", "name": "x"}, BASE) is None


@pytest.mark.parametrize("seconds, text", [(7200, "2 hrs"), (3600, "1 hr"), (5400, "1.5 hrs"), (2700, "45 min"), (0, None)])
def test_format_duration(seconds, text):
    assert academy.format_duration(seconds) == text


def test_fetch_catalog_pages_and_filters_language(monkeypatch):
    pages = {
        1: {"data": {"items": [_item(1, "ACAD-ALL-SLP-A-EN-v1", "Course A"),
                               _item(2, "ACAD-ALL-SLP-A-JA-v1", "Course A JA", language="japanese")],
                     "has_more_data": True}},
        2: {"data": {"items": [_item(3, "ACAD-ALL-SLP-B-EN-v1", "Course B")], "has_more_data": False}},
    }
    seen = []

    async def fake_fetch(url, **kwargs):
        assert kwargs["allowed_domains"] == ["databricks.com"] and kwargs["extract"] is False
        page = int(url.rsplit("page=", 1)[1])
        seen.append(page)
        return {"ok": True, "raw": json.dumps(pages[page])}

    monkeypatch.setattr(academy, "safe_fetch", fake_fetch)
    res = asyncio.run(academy.fetch_catalog(BASE, ["english"]))
    assert res["ok"] and seen == [1, 2]
    assert [c["id"] for c in res["courses"]] == [1, 3]


def test_fetch_catalog_refuses_other_hosts_and_reports_failures(monkeypatch):
    res = asyncio.run(academy.fetch_catalog("https://academy.example.com", ["english"]))
    assert not res["ok"] and "databricks.com" in res["error"]

    async def failing(url, **kwargs):
        return {"ok": False, "error": "HTTP 503"}

    monkeypatch.setattr(academy, "safe_fetch", failing)
    res = asyncio.run(academy.fetch_catalog(BASE, ["english"]))
    assert not res["ok"] and "HTTP 503" in res["error"] and res["courses"] == []


# ------------------------------------------------------------------ sync

def test_sync_upserts_retires_and_auto_links_exact_codes(db_session, track):
    exact = TrainingService.create_course(db_session, track_id=track.id, title="My ingestion course",
                                          course_code="ACAD-ALL-SLP-DINLC-EN-v3")
    stats = tc.save_catalog(db_session, _courses(
        _item(10, "ACAD-ALL-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect", duration=5400),
        _item(11, "ACAD-ALL-SLP-DWLJ-EN-v3", "Deploy Workloads with Lakeflow Jobs"),
    ))
    assert stats["added"] == 2 and stats["retired"] == 0
    db_session.refresh(exact)
    assert exact.academy_course_id == 10
    assert exact.title == "My ingestion course"  # auto-link keeps the admin's title
    assert exact.duration == "1.5 hrs" and exact.course_type == "Self-paced"
    assert exact.external_url.endswith("/10/data-ingestion-with-lakeflow-connect")

    stats = tc.save_catalog(db_session, _courses(_item(10, "ACAD-ALL-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect")))
    assert stats["retired"] == 1
    assert db_session.get(TrainingCatalogCourseModel, 11).active is False


def test_sync_relinks_to_the_new_version_and_follows_renames(db_session, track):
    tc.save_catalog(db_session, _courses(_item(20, "ACAD-ALL-SLP-DWLJ-EN-v2", "Deploy Workloads w/ Lakeflow Jobs")))
    course = TrainingService.create_course(db_session, track_id=track.id, title="placeholder")
    tc.link_course(db_session, course, 20)
    assert course.title == "Deploy Workloads w/ Lakeflow Jobs"

    stats = tc.save_catalog(db_session, _courses(
        _item(21, "ACAD-CUST-SLP-DWLJ-EN-v3", "Deploy Workloads with Lakeflow Jobs", price="1500"),
        _item(22, "ACAD-ALL-SLP-DWLJ-EN-v3", "Deploy Workloads with Lakeflow Jobs"),
        _item(23, "ACAD-ALL-ILTPUB-DWLJ-EN-v3", "Deploy Workloads with Lakeflow Jobs", course_type="classroom"),
    ))
    db_session.refresh(course)
    assert stats["relinked"] == 1
    assert course.academy_course_id == 22  # same delivery, free, open to everyone
    assert course.course_code == "ACAD-ALL-SLP-DWLJ-EN-v3"
    assert course.title == "Deploy Workloads with Lakeflow Jobs"


def test_sync_keeps_an_admin_edited_title(db_session, track):
    tc.save_catalog(db_session, _courses(_item(30, "ACAD-ALL-SLP-DMS-EN-v2", "Data Modeling Strategies")))
    course = TrainingService.create_course(db_session, track_id=track.id, title="x")
    tc.link_course(db_session, course, 30)
    TrainingService.update_course(db_session, course.id, title="Modeling (required for stewards)")

    tc.save_catalog(db_session, _courses(_item(30, "ACAD-ALL-SLP-DMS-EN-v2", "Data Modeling Strategies on Databricks")))
    db_session.refresh(course)
    assert course.title == "Modeling (required for stewards)"


def test_retired_course_without_replacement_keeps_its_details(db_session, track):
    tc.save_catalog(db_session, _courses(_item(40, "ACAD-ALL-SLP-OLD-EN-v1", "Old Course"),
                                         _item(41, "ACAD-ALL-SLP-KEEP-EN-v1", "Keep")))
    course = TrainingService.create_course(db_session, track_id=track.id, title="x")
    tc.link_course(db_session, course, 40)
    tc.save_catalog(db_session, _courses(_item(41, "ACAD-ALL-SLP-KEEP-EN-v1", "Keep")))
    db_session.refresh(course)
    assert course.academy_course_id == 40 and course.course_code == "ACAD-ALL-SLP-OLD-EN-v1"
    status = tc.status(db_session)
    assert status["courses_retired"] == 1 and status["courses_linked"] == 1
    data = TrainingService.course_to_dict(db_session, course)
    assert data["academy"]["active"] is False


def test_suggestions_by_version_title_and_none(db_session, track):
    tc.save_catalog(db_session, _courses(
        _item(50, "ACAD-ALL-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect"),
        _item(51, "ACAD-CUST-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect", price="1500"),
        _item(52, "ACAD-ALL-SLP-GSDDE-EN-v4", "Get Started with Databricks for Data Engineering"),
        _item(53, "ACAD-ALL-SLP-DAWD-EN-v1", "Data Analysis with Databricks"),
    ))
    old = TrainingService.create_course(db_session, track_id=track.id, title="Ingestion",
                                        course_code="ACAD-ALL-SLP-FREE-DINLC-EN-v1")
    renamed = TrainingService.create_course(db_session, track_id=track.id,
                                            title="Get Started with Databricks for Data Engineering",
                                            course_code="ACAD-ALL-SLP-FREE-GSWDDE-V3")
    TrainingService.create_course(db_session, track_id=track.id, title="Data Warehousing with Databricks")

    out = tc.suggestions(db_session)
    by_id = {s["course_id"]: s for s in out["suggestions"]}
    assert by_id[old.id]["reason"] == "newer_version" and by_id[old.id]["match"]["id"] == 50
    assert by_id[renamed.id]["reason"] == "similar_title" and by_id[renamed.id]["match"]["id"] == 52
    assert [u["course_title"] for u in out["unmatched"]] == ["Data Warehousing with Databricks"]


def test_search_matches_every_word(db_session):
    tc.save_catalog(db_session, _courses(
        _item(60, "ACAD-ALL-SLP-DINLC-EN-v3", "Data Ingestion with Lakeflow Connect"),
        _item(61, "ACAD-ALL-SLP-DWLJ-EN-v3", "Deploy Workloads with Lakeflow Jobs"),
    ))
    assert [c["id"] for c in tc.search(db_session, "lakeflow jobs")] == [61]
    assert [c["id"] for c in tc.search(db_session, "DINLC")] == [60]
    assert len(tc.search(db_session, "")) == 2


def test_link_rejects_unknown_catalog_course(db_session, track):
    course = TrainingService.create_course(db_session, track_id=track.id, title="x")
    with pytest.raises(ValueError):
        tc.link_course(db_session, course, 999)
    tc.link_course(db_session, course, None)
    assert db_session.get(TrainingCourseModel, course.id).academy_course_id is None


# ------------------------------------------------------------------ gate

def test_training_gate_accepts_a_newer_version(db_session, monkeypatch):
    from app.workers import poller

    class FakeProvider:
        def __init__(self, db):
            pass

        def get_user_training_status(self, email):
            return ["ACAD-CUST-SLP-DINLC-EN-v3"]

    monkeypatch.setattr("app.providers.training.client.TrainingProvider", FakeProvider)

    class Req:
        id = "r1"
        requester_email = "a@example.com"

    assert poller._training_course_completed(db_session, Req(), "ACAD-ALL-SLP-FREE-DINLC-EN-v1")
    assert not poller._training_course_completed(db_session, Req(), "ACAD-ALL-SLP-DWLJ-EN-v3")
