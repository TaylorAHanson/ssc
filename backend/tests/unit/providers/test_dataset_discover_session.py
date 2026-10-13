"""Dataset discovery must not hold its DB session across the warehouse work."""
import asyncio
from types import SimpleNamespace

from app.providers.databricks.handlers.dataset_handler import DatasetResourceHandler

CONTRACT_YAML = "servers: []\nschema: []\n"


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class _DeadOnCloseSession:
    """Lakebase drops an idle connection; closing it then raises."""

    def __init__(self, events):
        self.events = events

    def query(self, _model):
        self.events.append("query")
        return _Query([SimpleNamespace(dataset_id="sales-orders", yaml_content=CONTRACT_YAML)])

    def close(self):
        self.events.append("close")
        raise RuntimeError("SSL connection has been closed unexpectedly")


def test_session_is_closed_before_discovery_and_a_dead_close_does_not_fail_it(monkeypatch):
    events = []

    def _get_db():
        yield _DeadOnCloseSession(events)

    monkeypatch.setattr("app.db.session.get_db", _get_db)
    monkeypatch.setattr(
        DatasetResourceHandler, "_apply_uc_metadata",
        lambda self, pending: events.append("uc_metadata"),
    )

    resources = asyncio.run(DatasetResourceHandler(workspace_client=None).discover())

    assert [r["dataset_id"] for r in resources] == ["sales-orders"]
    assert events == ["query", "close", "uc_metadata"]
