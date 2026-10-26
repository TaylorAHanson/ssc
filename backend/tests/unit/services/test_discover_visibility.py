"""Assets tagged with a DISCOVER_HIDDEN_TAG_KEYS key, at any level, stay out of every Discover surface."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1 import data_assets as api
from app.api.v1 import pins as pin_routes
from app.api.v1.legacy_dashboards import list_legacy_dashboards
from app.core import settings_store
from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.db.legacy_dashboard import LegacyDashboardMappingModel
from app.models.user import User
from app.services import discover_visibility as dv
from app.tools.self_service import get_table_list as gtl
from app.tools.self_service.search_data_assets import search_data_assets
from app.workers.tasks import sync_data_assets as sync

ADMIN = User(id="admin", email="admin@x.com", full_name="Admin", entitlements=[], roles=["Platform Admin"])
ALICE = User(id="alice", email="alice@x.com", full_name="Alice", entitlements=[], roles=[])


@pytest.fixture(autouse=True)
def hide_keys(monkeypatch):
    monkeypatch.setattr(settings, "DISCOVER_HIDDEN_TAG_KEYS", ["Hide_From_Discover"])
    dv.clear_entity_cache()
    yield
    dv.clear_entity_cache()


def _asset(asset_id, type_="MANAGED", tags=None, scope=None, **extra):
    catalog, schema, table = asset_id.split(".")
    return DataAssetModel(id=asset_id, catalog=catalog, schema=schema, table_name=table, type=type_,
                          domain="Finance", subdomain="Revenue & Margin", tags=tags or [],
                          scope_tag_keys=scope, **extra)


@pytest.fixture
def db(db_session):
    db_session.add_all([
        _asset("main.fin.visible"),
        _asset("main.fin.own_tag", tags=["HIDE_FROM_DISCOVER=true"]),
        _asset("main.fin.bare_key", tags=["hide_from_discover"]),
        _asset("main.fin.other_tag", tags=["hide_from_discover_not=1", "pii=yes"]),
        _asset("main.fin.column_tag", scope={"catalog": [], "schema": [], "column": ["Hide_From_Discover"]}),
        # A schema-level key stamped on one row hides every asset in that schema.
        _asset("main.secret.a", scope={"catalog": [], "schema": ["hide_from_discover"], "column": []}),
        _asset("main.secret.b"),
        _asset("hidden_cat.x.a", scope={"catalog": ["hide_from_discover"], "schema": [], "column": []}),
        _asset("hidden_cat.y.b"),
        _asset("main.fin.mv_visible", type_="METRIC_VIEW",
               upstream_tables=[{"fqn": "main.fin.visible"}, {"fqn": "main.fin.own_tag"},
                                {"fqn": "hidden_cat.z.not_cached"}],
               downstream_dashboards=[{"id": "d-ok", "name": "OK"}, {"id": "d-hidden", "name": "Secret"}]),
        _asset("main.fin.mv_hidden", type_="METRIC_VIEW", tags=["hide_from_discover"]),
    ])
    db_session.flush()
    return db_session


VISIBLE = {"main.fin.visible", "main.fin.other_tag", "main.fin.mv_visible"}


@pytest.fixture
def dashboard_tags():
    """Workspace tags per dashboard / app / Genie space, without hitting Databricks."""
    tags = {("dashboards", "d-hidden"): {"hide_from_discover"}}
    calls = []

    def fetch(_client, entity_type, entity_id):
        calls.append((entity_type, entity_id))
        return frozenset(dv.tag_keys(list(tags.get((entity_type, entity_id), set()))))

    with patch.object(dv, "_fetch_entity_keys", side_effect=fetch), \
            patch.object(dv, "_default_client", return_value=MagicMock()):
        yield tags, calls


# --- matching ---------------------------------------------------------------

def test_tag_keys_parse_every_shape_case_insensitively():
    assert dv.tag_keys(["A=1", "b", " C = 2 "]) == {"a", "b", "c"}
    assert dv.tag_keys({"Owner": "x"}) == {"owner"}
    assert dv.tag_keys('["Hide=1"]') == {"hide"}
    assert dv.tag_keys([SimpleNamespace(tag_key="K", tag_value=None)]) == {"k"}
    assert dv.has_hidden_tag(["HIDE_FROM_DISCOVER=no"])  # the value never matters
    assert not dv.has_hidden_tag(["hide_from_discover_not"])


def test_empty_setting_is_off_and_never_queries(monkeypatch):
    monkeypatch.setattr(settings, "DISCOVER_HIDDEN_TAG_KEYS", [])
    db = MagicMock()
    vis = dv.load_visibility(db)
    assert not vis.active and not vis.is_hidden_id("main.fin.own_tag")
    db.query.assert_not_called()


def test_visibility_covers_every_level(db):
    vis = dv.load_visibility(db)
    all_ids = {a.id for a in db.query(DataAssetModel).all()}
    assert all_ids - vis.hidden_asset_ids == VISIBLE
    # Catalog/schema keys also hide names that aren't cached.
    assert vis.is_hidden_id("hidden_cat.anything.t") and vis.is_hidden_id("`main`.`secret`.`new`")
    assert not vis.is_hidden_id("main.fin.brand_new")


def test_changing_the_setting_applies_without_a_resync(db, monkeypatch):
    monkeypatch.setattr(settings, "DISCOVER_HIDDEN_TAG_KEYS", ["pii"])
    assert dv.load_visibility(db).hidden_asset_ids == {"main.fin.other_tag"}


def test_setting_is_a_data_catalog_string_list():
    field = next(f for f in settings_store.EDITABLE_FIELDS if f["key"] == "DISCOVER_HIDDEN_TAG_KEYS")
    assert field["group"] == "Data Catalog" and field["type"] == "string_list"
    assert settings_store._coerce_string_list(field, [" hide ", "hide", ""]) == ["hide"]


# --- Discover API -------------------------------------------------------------

def test_list_and_metric_views_drop_hidden_assets_and_links(db, dashboard_tags):
    listed = {a["id"]: a for a in api.list_data_assets(db=db)}
    assert set(listed) == VISIBLE
    mv = listed["main.fin.mv_visible"]
    assert [u["fqn"] for u in mv["upstream_tables"]] == ["main.fin.visible"]
    assert [d["id"] for d in mv["downstream_dashboards"]] == ["d-ok"]

    views = api.list_metric_views(db=db)
    assert [v["id"] for v in views] == ["main.fin.mv_visible"]
    assert [d["id"] for d in views[0]["downstream_dashboards"]] == ["d-ok"]


def test_domain_counts_skip_hidden_assets(db, dashboard_tags):
    fin = next(d for d in api.get_domains_hierarchy(db=db) if d["domain"] == "Finance")
    assert fin["metric_view_count"] == 1
    assert fin["table_count"] == 2
    assert fin["dashboard_count"] == 1


def test_metric_view_detail_of_a_hidden_view_is_not_found(db):
    req = SimpleNamespace(state=SimpleNamespace(token="t", user={"email": "a@x.com"}))
    out = asyncio.run(api.get_metric_view_detail("main.fin.mv_hidden", req, db=db))
    assert out["available"] is False and out["reason"] == "not_found"

    defs = asyncio.run(api.get_metric_view_definitions(
        api.MetricViewDefinitionsRequest(asset_ids=["main.fin.mv_hidden"]), req, db=db))
    assert defs["definitions"]["main.fin.mv_hidden"]["reason"] == "not_found"


def test_live_table_endpoints_refuse_hidden_tables(db):
    req = SimpleNamespace(state=SimpleNamespace(token="t"))
    with patch("app.core.workspaces.uc_client_for", side_effect=AssertionError("must not read UC")):
        details = api.get_databricks_table_details("main.secret.b", req, db=db)
        lineage = api.get_databricks_table_lineage("hidden_cat.x.a", req, db=db)
    assert details["error_kind"] == "not_found" and details["columns"] == []
    assert lineage["error_kind"] == "not_found" and lineage["upstreams"] == []

    with patch.object(settings, "DATABRICKS_WAREHOUSE_ID", "wh"), \
            patch("app.core.workspaces.get_uc_provider", side_effect=AssertionError("must not query")):
        preview = asyncio.run(api.get_databricks_table_preview("main.fin.own_tag", req, db=db))
    assert preview["error_kind"] == "not_found" and preview["rows"] == []


def test_table_details_hide_on_a_live_tag_newer_than_the_sync(db):
    client = MagicMock()
    client.tables.get.return_value = SimpleNamespace(columns=[], table_type=None, owner="o", comment=None)
    client.entity_tag_assignments.list.return_value = [SimpleNamespace(tag_key="hide_from_discover", tag_value="")]
    with patch("app.core.workspaces.uc_client_for", return_value=(MagicMock(), client)):
        out = api.get_databricks_table_details("main.fin.visible", SimpleNamespace(state=SimpleNamespace(token="t")), db=db)
    assert out["error_kind"] == "not_found"


def test_lineage_drops_hidden_neighbours(db):
    client = MagicMock()
    client.api_client.do.return_value = {
        "upstreams": [
            {"tableInfo": {"name": "main.fin.visible"}},
            {"tableInfo": {"name": "main.fin.own_tag"}},
            {"tableInfo": {"name": "t", "catalog_name": "hidden_cat", "schema_name": "q", "table_name": "t"}},
        ],
        "downstreams": [{"tableInfo": {"name": "main.secret.new"}}],
    }
    with patch("app.core.workspaces.uc_client_for", return_value=(MagicMock(), client)):
        out = api.get_databricks_table_lineage("main.fin.visible", SimpleNamespace(state=SimpleNamespace(token="t")), db=db)
    assert [u["name"] for u in out["upstreams"]] == ["main.fin.visible"]
    assert out["downstreams"] == []


def test_accessible_ids_skip_hidden_assets(db):
    req = SimpleNamespace(state=SimpleNamespace(token="t"))
    provider = MagicMock()
    provider.execute_sql = AsyncMock(side_effect=lambda q, **_k: {"rows": [
        {"table_schema": s, "table_name": t} for s, t in (("fin", "visible"), ("fin", "own_tag"), ("secret", "b"))
    ]} if "`main`" in q else {"rows": []})
    with patch.object(settings, "DATABRICKS_WAREHOUSE_ID", "wh"), \
            patch.object(settings, "DATABRICKS_HOST", "https://x"), \
            patch("app.providers.databricks.DatabricksProvider", return_value=provider):
        out = asyncio.run(api.get_accessible_assets(req, current_user=ALICE, db=db))
    assert out.accessible_ids == ["main.fin.visible"]


def _workspace_provider():
    provider = MagicMock()
    provider.client.lakeview.list.return_value = [
        SimpleNamespace(dashboard_id="d-ok", display_name="OK", parent_path="/"),
        SimpleNamespace(dashboard_id="d-hidden", display_name="Secret", parent_path="/"),
    ]
    provider.client.jobs.list.return_value = [
        SimpleNamespace(job_id=1, creator_user_name="a", settings=SimpleNamespace(name="ok", tags={"team": "x"})),
        SimpleNamespace(job_id=2, creator_user_name="a", settings=SimpleNamespace(name="no", tags={"Hide_From_Discover": ""})),
    ]
    provider.client.api_client.do.return_value = {"apps": [{"name": "app-ok"}, {"name": "app-hidden"}]}
    provider.client.genie.list_spaces.return_value = SimpleNamespace(
        spaces=[SimpleNamespace(space_id="g-ok", title="OK", description=None),
                SimpleNamespace(space_id="g-hidden", title="No", description=None)],
        next_page_token=None,
    )
    return provider


def test_workspace_listings_drop_tagged_dashboards_apps_genie_and_jobs(dashboard_tags):
    tags, calls = dashboard_tags
    tags[("apps", "app-hidden")] = {"hide_from_discover"}
    tags[("geniespaces", "g-hidden")] = {"HIDE_FROM_DISCOVER"}
    with patch("app.providers.databricks.DatabricksProvider", return_value=_workspace_provider()):
        assert [d["id"] for d in api.get_databricks_dashboards()] == ["d-ok"]
        assert [j["id"] for j in api.get_databricks_jobs()] == ["1"]
        assert [a["id"] for a in api.get_databricks_apps()] == ["app-ok"]
        assert [g["id"] for g in api.get_databricks_genie_spaces()] == ["g-ok"]
        api.get_databricks_dashboards()
    # Tag reads are cached: the second dashboards listing re-read nothing.
    assert calls.count(("dashboards", "d-ok")) == 1


def test_workspace_listings_skip_tag_reads_when_off(monkeypatch, dashboard_tags):
    monkeypatch.setattr(settings, "DISCOVER_HIDDEN_TAG_KEYS", [])
    _, calls = dashboard_tags
    with patch("app.providers.databricks.DatabricksProvider", return_value=_workspace_provider()):
        assert len(api.get_databricks_dashboards()) == 2
    assert calls == []


def test_unreadable_workspace_tags_fail_open():
    client = MagicMock()
    client.workspace_entity_tag_assignments.list_tag_assignments.side_effect = RuntimeError("not enabled")
    assert dv.hidden_workspace_entities("dashboard", ["d1"], client=client) == set()


# --- pins and legacy mappings ---------------------------------------------------

def test_pins_of_hidden_assets_are_left_out(db, dashboard_tags):
    db.add(LegacyDashboardMappingModel(id="lm-hidden", dashboard="Old", metric_view_id="main.fin.mv_hidden"))
    db.flush()
    for asset_id, asset_type in (("main.fin.visible", "table"), ("main.secret.b", "table"),
                                 ("lm-hidden", "legacy_dashboard"), ("d-hidden", "dashboard"),
                                 ("d-ok", "dashboard")):
        pin_routes.pin_asset(body=pin_routes.PinCreate(asset_id=asset_id, asset_type=asset_type, title=asset_id),
                             db=db, current_user=ALICE)
    listed = pin_routes.list_pins(db=db, current_user=ALICE)
    assert sorted(p["asset_id"] for p in listed["pins"]) == ["d-ok", "main.fin.visible"]
    assert listed["count"] == 2


def test_legacy_mappings_of_hidden_views_are_left_out_except_for_the_admin_editor(db):
    db.add_all([
        LegacyDashboardMappingModel(id="lm-ok", dashboard="A", metric_view_id="main.fin.mv_visible"),
        LegacyDashboardMappingModel(id="lm-hidden", dashboard="B", metric_view_id="main.fin.mv_hidden"),
    ])
    db.flush()
    assert [m["id"] for m in list_legacy_dashboards(db=db, current_user=ALICE)] == ["lm-ok"]
    assert [m["id"] for m in list_legacy_dashboards(include_hidden=True, db=db, current_user=ALICE)] == ["lm-ok"]
    admin = list_legacy_dashboards(include_hidden=True, db=db, current_user=ADMIN)
    assert {m["id"]: m["hidden_from_discover"] for m in admin} == {"lm-ok": False, "lm-hidden": True}


# --- agent tools ----------------------------------------------------------------

def test_search_data_assets_never_returns_hidden_assets(db, dashboard_tags):
    db.add(LegacyDashboardMappingModel(id="lm-hidden", dashboard="Finance board", metric_view_id="main.fin.mv_hidden"))
    db.flush()
    with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([db])), \
            patch.object(db, "close"):
        res = search_data_assets._func(query="main fin secret board", limit=50)
    ids = {a["id"] for a in res["assets"]}
    assert ids == VISIBLE
    mv = next(a for a in res["assets"] if a["id"] == "main.fin.mv_visible")
    assert [d["id"] for d in mv["downstream_dashboards"]] == ["d-ok"]


def _table_list_provider(tag_rows, scope_rows):
    provider = MagicMock()

    async def execute_sql(query, **_kwargs):
        return {"rows": scope_rows if "column_tags" in query else tag_rows}

    provider.execute_sql = AsyncMock(side_effect=execute_sql)
    client = MagicMock()
    client.tables.list.return_value = [
        SimpleNamespace(name=n, catalog_name="live", schema_name="s", table_type="MANAGED",
                        comment=None, owner="o", properties={})
        for n in ("plain", "valueless", "col_tagged")
    ]
    return provider, client


def _run_table_list(provider, client, db):
    with patch("app.core.workspaces.uc_client_for", return_value=(provider, client)), \
            patch.object(gtl, "_cached_visibility", side_effect=lambda: dv.load_visibility(db)):
        return asyncio.run(gtl.get_table_list._func(
            target_host="h", catalog_name="live", schema_name="s", _obo_token="tok"))


def test_get_table_list_hides_table_and_column_tags_read_live(db):
    provider, client = _table_list_provider(
        tag_rows=[{"table_name": "valueless", "tag_name": "Hide_From_Discover", "tag_value": None}],
        scope_rows=[{"level": "column", "table_name": "col_tagged", "tag_name": "hide_from_discover"}],
    )
    out = _run_table_list(provider, client, db)
    assert [t["name"] for t in out["tables"]] == ["plain"]


def test_get_table_list_hides_everything_under_a_tagged_schema(db):
    provider, client = _table_list_provider(
        tag_rows=[], scope_rows=[{"level": "schema", "table_name": None, "tag_name": "HIDE_FROM_DISCOVER"}])
    assert _run_table_list(provider, client, db)["tables"] == []


# --- sync ---------------------------------------------------------------------

def test_sync_reads_catalog_schema_and_column_tag_keys():
    async def execute_sql(query, **_kwargs):
        if "catalog_tags" in query:
            return {"rows": [{"catalog_name": "main", "tag_names": '["hide_from_discover"]'}]}
        if "schema_tags" in query:
            raise RuntimeError("no access")
        return {"rows": [{"catalog_name": "main", "schema_name": "fin", "table_name": "t", "tag_names": ["pii"]}]}

    provider = MagicMock()
    provider.execute_sql = AsyncMock(side_effect=execute_sql)
    scope = asyncio.run(sync._fetch_scope_tag_keys(provider, ["main"]))
    assert scope["catalog"] == {"main": ["hide_from_discover"]}
    assert scope["schema"] is None
    assert scope["column"] == {("main", "fin", "t"): ["pii"]}
    assert all("IN ('main')" in c.args[0] for c in provider.execute_sql.await_args_list)

    # A level whose read failed keeps what the previous sync stored.
    merged = sync.merge_scope_tag_keys({"schema": ["hide_from_discover"], "catalog": ["old"]},
                                       scope, "main", "fin", "t")
    assert merged == {"catalog": ["hide_from_discover"], "schema": ["hide_from_discover"], "column": ["pii"]}
