"""Metric view detail is read On-Behalf-Of the user, and degrades cleanly without OBO."""

import asyncio
import json
from unittest.mock import patch

from app.api.v1.data_assets import _classify_uc_error, load_metric_view_detail
from app.db.data_asset import DataAssetModel

YAML = """
version: 1.1
source: enterprise_dev.gold.sales_orders
measures:
  - name: order_count
    display_name: Orders
    expr: COUNT(1)
  - name: revenue
    expr: SUM(amount)
"""


def _asset(type_="METRIC_VIEW"):
    return DataAssetModel(id="c.s.mv", catalog="c", schema="s", table_name="mv", type=type_)


class FakeProvider:
    def __init__(self, describe=None, values=None):
        self.describe, self.values, self.calls = describe, values, []

    async def execute_sql(self, query, **kwargs):
        self.calls.append((query, kwargs))
        outcome = self.describe if query.startswith("DESCRIBE") else self.values
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(asset, provider, token="user-token", fallback=False, warehouse="wh"):
    with patch("app.core.workspaces.get_uc_provider", return_value=provider), \
         patch("app.providers.databricks_mcp.sp_fallback_allowed", return_value=fallback), \
         patch("app.core.config.settings.DATABRICKS_WAREHOUSE_ID", warehouse):
        return asyncio.run(load_metric_view_detail(asset, token))


def _describe(view_text=YAML):
    return {"rows": [{"json_metadata": json.dumps({"type": "METRIC_VIEW", "view_text": view_text})}]}


def test_definition_and_values_are_read_as_the_user():
    provider = FakeProvider(_describe(), {"rows": [{"order_count": 12, "revenue": 3400.5}]})
    out = _run(_asset(), provider)

    assert out["available"] and out["reason"] is None
    assert [k["measure"] for k in out["kpis"]] == ["order_count", "revenue"]
    assert [t["fqn"] for t in out["upstream_tables"]] == ["enterprise_dev.gold.sales_orders"]
    assert out["values"] == {"order_count": 12, "revenue": 3400.5}
    assert provider.calls[0][0] == "DESCRIBE TABLE EXTENDED `c`.`s`.`mv` AS JSON"
    assert all(kw["obo_token"] == "user-token" and kw["require_obo"] for _, kw in provider.calls)


def test_no_obo_on_a_deployed_target_reads_nothing():
    provider = FakeProvider(_describe())
    out = _run(_asset(), provider, token=None, fallback=False)

    assert out == {"available": False, "reason": "no_obo", "kpis": [], "upstream_tables": [],
                   "values": {}, "error": None, "error_kind": None}
    assert provider.calls == []


def test_local_dev_without_obo_still_reads():
    out = _run(_asset(), FakeProvider(_describe(), {"rows": [{}]}), token=None, fallback=True)
    assert out["available"] and len(out["kpis"]) == 2


def test_user_without_select_gets_permission_denied():
    err = RuntimeError("User does not have SELECT on Table 'c.s.mv'.")
    out = _run(_asset(), FakeProvider(describe=err))

    assert not out["available"] and out["reason"] == "permission_denied"
    assert out["kpis"] == []


def test_values_failure_keeps_the_definition():
    out = _run(_asset(), FakeProvider(_describe(), RuntimeError("warehouse timed out")))

    assert out["available"] and len(out["kpis"]) == 2
    assert out["values"] == {} and out["error"] == "warehouse timed out"
    assert out["error_kind"] == "error"


def test_view_without_measures_skips_the_values_query():
    provider = FakeProvider(_describe("source: c.s.t\nmeasures: []\n"))
    out = _run(_asset(), provider)

    assert out["available"] and out["kpis"] == []
    assert len(provider.calls) == 1


def test_unknown_asset_and_missing_warehouse():
    assert _run(None, FakeProvider())["reason"] == "not_found"
    assert _run(_asset("TABLE"), FakeProvider())["reason"] == "not_found"
    assert _run(_asset(), FakeProvider(), warehouse="")["reason"] == "no_warehouse"


def test_uc_missing_privilege_is_classified_as_permission_denied():
    assert _classify_uc_error("User does not have SELECT on Table 'a.b.c'") == "permission_denied"
    assert _classify_uc_error("Table 'a.b.c' does not exist") == "not_found"


def test_view_with_a_dropped_source_is_a_broken_dependency():
    msg = ("[UC_DEPENDENCY_DOES_NOT_EXIST] Dependency does not exist in Unity Catalog: Table "
           "'c.s.mv' is invalid because one of the underlying resources does not exist.")
    assert _classify_uc_error(msg) == "broken_dependency"
    out = _run(_asset(), FakeProvider(_describe(), RuntimeError(msg)))
    assert out["available"] and len(out["kpis"]) == 2
    assert out["error_kind"] == "broken_dependency"
