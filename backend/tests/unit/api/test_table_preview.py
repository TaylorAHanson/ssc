"""Discover's Data preview reads sample rows On-Behalf-Of the user, never as the app SP."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.v1.data_assets import _preview_row_limit, get_databricks_table_preview


class FakeProvider:
    def __init__(self, outcome=None):
        self.outcome, self.calls = outcome, []

    async def execute_sql(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _req(token="user-token"):
    return SimpleNamespace(state=SimpleNamespace(token=token))


def _run(table_name, provider, token="user-token", limit=None, cap=500, fallback=False, warehouse="wh"):
    with patch("app.core.workspaces.get_uc_provider", return_value=provider), \
            patch("app.providers.databricks_mcp.sp_fallback_allowed", return_value=fallback), \
            patch("app.core.config.settings.DATABRICKS_WAREHOUSE_ID", warehouse), \
            patch("app.core.config.settings.DISCOVER_PREVIEW_ROW_LIMIT", cap):
        return asyncio.run(get_databricks_table_preview(table_name, _req(token), limit))


def _result(rows, names=("id", "name"), types=("BIGINT", "STRING")):
    return {"rows": [dict(zip(names, r)) for r in rows], "schema": list(names), "column_types": list(types)}


def test_rows_are_read_as_the_user():
    provider = FakeProvider(_result([["1", "a"], ["2", None]]))
    out = _run("main.sales.orders", provider, token="tok-1")

    query, kwargs = provider.calls[0]
    assert query == "SELECT * FROM `main`.`sales`.`orders` LIMIT 500"
    assert kwargs["obo_token"] == "tok-1" and kwargs["require_obo"] and kwargs["warehouse"] == "wh"
    assert out["columns"] == [{"name": "id", "type": "BIGINT"}, {"name": "name", "type": "STRING"}]
    assert out["rows"] == [["1", "a"], ["2", None]]
    assert out["row_count"] == 2 and out["limit"] == 500 and not out["truncated"]
    assert out["error"] is None and out["error_kind"] is None


def test_every_identifier_part_is_quoted():
    provider = FakeProvider(_result([]))
    _run("my-cat.sch`ema.t; DROP TABLE x", provider)
    assert provider.calls[0][0] == "SELECT * FROM `my-cat`.`sch``ema`.`t; DROP TABLE x` LIMIT 500"


@pytest.mark.parametrize("name", ["", "orders", "sales.orders", "a.b.c.d", "a..c", "a.b. "])
def test_names_that_are_not_three_parts_are_rejected(name):
    provider = FakeProvider(_result([]))
    with pytest.raises(HTTPException) as exc:
        _run(name, provider)
    assert exc.value.status_code == 400
    assert provider.calls == []


def test_limit_can_lower_the_admin_cap_but_not_raise_it():
    provider = FakeProvider(_result([["1", "a"]] * 10))
    out = _run("c.s.t", provider, limit=10, cap=200)
    assert provider.calls[-1][0].endswith("LIMIT 10")
    assert out["limit"] == 10 and out["truncated"]

    _run("c.s.t", provider, limit=999, cap=200)
    assert provider.calls[-1][0].endswith("LIMIT 200")


def test_row_limit_is_clamped():
    with patch("app.core.config.settings.DISCOVER_PREVIEW_ROW_LIMIT", 100000):
        assert _preview_row_limit(None) == 5000
    with patch("app.core.config.settings.DISCOVER_PREVIEW_ROW_LIMIT", 0):
        assert _preview_row_limit(None) == 1
    with patch("app.core.config.settings.DISCOVER_PREVIEW_ROW_LIMIT", 500):
        assert _preview_row_limit(-3) == 1


def test_no_obo_on_a_deployed_target_reads_nothing():
    provider = FakeProvider(_result([["1", "a"]]))
    out = _run("c.s.t", provider, token=None, fallback=False)
    assert out["error_kind"] == "no_obo" and out["rows"] == []
    assert provider.calls == []


def test_local_dev_without_obo_still_reads():
    out = _run("c.s.t", FakeProvider(_result([["1", "a"]])), token=None, fallback=True)
    assert out["rows"] == [["1", "a"]] and out["error"] is None


def test_missing_warehouse():
    provider = FakeProvider(_result([]))
    out = _run("c.s.t", provider, warehouse="")
    assert out["error_kind"] == "no_warehouse" and provider.calls == []


@pytest.mark.parametrize("message, kind", [
    ("SQL execution failed with state FAILED: [INSUFFICIENT_PERMISSIONS] User does not have SELECT on "
     "Table 'c.s.t'.", "permission_denied"),
    ("SQL execution failed with state FAILED: [TABLE_OR_VIEW_NOT_FOUND] The table or view `c`.`s`.`t` "
     "cannot be found.", "not_found"),
    ("[UC_DEPENDENCY_DOES_NOT_EXIST] Dependency does not exist in Unity Catalog", "broken_dependency"),
    ("SQL execution timed out after 60s", "error"),
])
def test_errors_are_classified_and_returned_with_200(message, kind):
    out = _run("c.s.t", FakeProvider(RuntimeError(message)))
    assert out["error_kind"] == kind and out["error"] == message
    assert out["rows"] == [] and out["columns"] == []


def test_missing_column_types_degrade_to_none():
    out = _run("c.s.t", FakeProvider({"rows": [{"id": "1"}], "schema": ["id"]}))
    assert out["columns"] == [{"name": "id", "type": None}]
