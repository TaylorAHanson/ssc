"""Which database the app connects to: Lakebase when configured, never a silent SQLite file instead."""
import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db import session


@pytest.fixture
def lakebase_env(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "DATABASE_PASSWORD", None)
    monkeypatch.setenv("PGHOST", "instance.database.cloud.databricks.com")
    monkeypatch.setenv("PGUSER", "app-sp")
    monkeypatch.setenv("PGDATABASE", "databricks_postgres")


def test_lakebase_without_a_token_raises_instead_of_using_sqlite(lakebase_env, monkeypatch):
    monkeypatch.setattr(session, "get_lakebase_token", lambda: None)

    with pytest.raises(session.LakebaseUnavailable, match="Not falling back to SQLite"):
        session.get_database_url()


def test_lakebase_missing_its_database_name_raises(lakebase_env, monkeypatch):
    monkeypatch.delenv("PGDATABASE")
    monkeypatch.setattr(settings, "DATABASE_NAME", None)

    with pytest.raises(session.LakebaseUnavailable):
        session.get_database_url()


def test_lakebase_with_a_token_connects_to_postgres(lakebase_env, monkeypatch):
    monkeypatch.setattr(session, "get_lakebase_token", lambda: "tok")

    url = make_url(session.get_database_url())
    assert (url.drivername, url.username, url.password, url.host) == (
        "postgresql", "app-sp", "tok", "instance.database.cloud.databricks.com",
    )


def test_without_lakebase_local_dev_uses_sqlite(monkeypatch):
    for name in ("PGHOST", "PGUSER", "PGDATABASE", "DATABRICKS_RUNTIME_VERSION", "DATABRICKS_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "DATABASE_HOST", None)

    assert session.get_database_url().startswith("sqlite:///")
