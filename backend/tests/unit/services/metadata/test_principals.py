"""Admin CRUD over registered machine callers (``metadata_principals``, ADR-0006 D4/D7)."""
import pytest

from app.core.config import settings
from app.db.metadata import MetadataPrincipalModel
from app.services.metadata import principals
from app.services.metadata.errors import MetadataError, NotFound
from app.services.metadata.types import PrincipalIn


@pytest.fixture(autouse=True)
def envs(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "dev", "catalog_pattern": "{base}_dev"},
        {"key": "prd", "catalog_pattern": "{base}_prd", "approval": "all"},
    ])


def principal_in(**kw):
    data = {"application_id": "11111111-2222-3333-4444-555555555555", "display_name": "Ingest pipeline",
            "scopes": ["sales_*"]}
    data.update(kw)
    return PrincipalIn(**data)


def test_upsert_creates_a_normalized_principal(db_session):
    out, created = principals.upsert_principal(db_session, principal_in(
        application_id="  AbC-123  ",
        display_name="  Ingest  ",
        description="  ",
        scopes=["Sales_*.Bronze", "sales_*.bronze", " *.gold.* ", ""],
        allowed_keys=[" pii* ", "pii*", "classification"],
        allowed_operations=["set_tags", "set_tags", "set_comment"],
        environments=["DEV"],
        max_objects_per_change=1000,
        can_skip_approval=True,
    ), actor="admin@example.com")

    assert created is True
    assert out.application_id == "abc-123"
    assert out.display_name == "Ingest"
    assert out.description is None
    assert out.scopes == ["sales_*.bronze", "*.gold.*"]
    assert out.allowed_keys == ["pii*", "classification"]
    assert out.allowed_operations == ["set_tags", "set_comment"]
    assert out.environments == ["dev"]
    assert out.max_objects_per_change == 1000
    assert out.can_skip_approval is True and out.enabled is True
    assert out.created_by == "admin@example.com" and out.updated_by == "admin@example.com"
    assert out.created_at is not None and out.updated_at is not None
    assert db_session.get(MetadataPrincipalModel, "abc-123") is not None


def test_upsert_updates_and_keeps_the_creator(db_session):
    principals.upsert_principal(db_session, principal_in(application_id="app-1"), actor="first@example.com")

    out, created = principals.upsert_principal(
        db_session, principal_in(application_id="APP-1", scopes=["hr_dev"], enabled=False),
        actor="second@example.com",
    )

    assert created is False
    assert out.scopes == ["hr_dev"] and out.enabled is False
    assert out.created_by == "first@example.com"
    assert out.updated_by == "second@example.com"
    assert len(principals.list_principals(db_session)) == 1


@pytest.mark.parametrize("kw,message", [
    ({"application_id": "  "}, "required"),
    ({"application_id": "pipeline@example.com"}, "email address"),
    ({"application_id": "has space"}, "valid application id"),
    ({"application_id": "a,b"}, "valid application id"),
    ({"scopes": ["a.b.c.d"]}, "valid name glob"),
    ({"scopes": ["a..b"]}, "valid name glob"),
    ({"scopes": ["sales_*; drop"]}, "valid name glob"),
    ({"scopes": ["sa`les"]}, "valid name glob"),
    ({"allowed_keys": ["bad`key"]}, "backticks"),
    ({"environments": ["stg"]}, "configured environment"),
    ({"max_objects_per_change": 0}, "at least 1"),
])
def test_upsert_refuses_bad_input(db_session, kw, message):
    with pytest.raises(MetadataError) as exc:
        principals.upsert_principal(db_session, principal_in(**kw), actor="admin@example.com")

    assert message in exc.value.message
    assert exc.value.status_code == 400
    assert principals.list_principals(db_session) == []


def test_upsert_refuses_an_unknown_operation(db_session):
    data = principal_in().model_copy(update={"allowed_operations": ["drop_table"]})

    with pytest.raises(MetadataError, match="isn't an operation"):
        principals.upsert_principal(db_session, data, actor="admin@example.com")


def test_list_is_sorted_by_application_id(db_session):
    for app_id in ("zz-app", "aa-app", "mm-app"):
        principals.upsert_principal(db_session, principal_in(application_id=app_id), actor="admin@example.com")

    assert [p.application_id for p in principals.list_principals(db_session)] == ["aa-app", "mm-app", "zz-app"]


def test_get_principal(db_session):
    principals.upsert_principal(db_session, principal_in(application_id="app-1"), actor="admin@example.com")

    assert principals.get_principal(db_session, " APP-1 ").display_name == "Ingest pipeline"
    with pytest.raises(NotFound):
        principals.get_principal(db_session, "missing")
    with pytest.raises(NotFound):
        principals.get_principal(db_session, "")


def test_delete_principal(db_session):
    principals.upsert_principal(db_session, principal_in(application_id="app-1"), actor="admin@example.com")

    principals.delete_principal(db_session, "APP-1", actor="admin@example.com")

    assert principals.list_principals(db_session) == []
    with pytest.raises(NotFound) as exc:
        principals.delete_principal(db_session, "app-1", actor="admin@example.com")
    assert exc.value.status_code == 404


def test_registered_principals_are_found_for_sign_in(db_session):
    principals.upsert_principal(db_session, principal_in(application_id="app-1"), actor="admin@example.com")
    principals.upsert_principal(db_session, principal_in(application_id="app-2", enabled=False),
                                actor="admin@example.com")

    assert principals.find_principal(db_session, ["someone", "APP-1"]).application_id == "app-1"
    assert principals.find_principal(db_session, ["app-2"]) is None
