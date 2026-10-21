"""Search, facets and key listing over desired + observed state (ADR-0006 D10).

Other workstreams' modules (authz, environments, policy) are replaced with fakes.
"""
import itertools
import time
from datetime import datetime
from typing import Dict, List, Optional

import pytest
from sqlalchemy import insert

from app.core.config import settings
from app.db.metadata import (
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataSyncStateModel,
    MetadataTagModel,
)
from app.services.metadata import authz, environments, observed, policy, search as search_mod
from app.services.metadata.authz import EditableScope
from app.services.metadata.search import editable_predicate, list_keys, search
from app.services.metadata.types import Actor, KeyPolicy, ObjectKey, SearchQuery, TagFilter

ALICE = Actor(id="alice@example.com", email="alice@example.com", groups=["team_a"])
_ids = itertools.count(1)
_LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}


def _env_of(catalog: Optional[str]) -> Optional[str]:
    name = (catalog or "").lower()
    for suffix in ("dev", "prd"):
        if name.endswith("_" + suffix):
            return suffix
    return None


class Fakes:
    def __init__(self):
        self.scope = EditableScope()
        self.policies: List[KeyPolicy] = []
        self.exempt: set = set()


@pytest.fixture
def fakes(monkeypatch) -> Fakes:
    f = Fakes()
    monkeypatch.setattr(authz, "editable_scope", lambda db, actor: f.scope)
    monkeypatch.setattr(environments, "environment_of", _env_of)
    monkeypatch.setattr(policy, "key_policies", lambda: list(f.policies))
    monkeypatch.setattr(policy, "is_exempt", lambda key: key in f.exempt)
    return f


def _obj(db, name, column=None, *, stype=None, env="__auto__", state="exists", status="active",
         comment=None, tags: Optional[Dict[str, Optional[str]]] = None, drift=None,
         updated_at=datetime(2026, 1, 1), last_applied_at=None, apply_status=None) -> str:
    key = ObjectKey.parse(name, column)
    parent = key.parent()
    oid = f"obj-{next(_ids)}"
    db.add(MetadataObjectModel(
        id=oid, fqn_key=key.fqn_key, securable_type=stype or _LEVEL_TYPES[key.level],
        catalog=key.catalog, schema_name=key.schema_name, object_name=key.object_name,
        column_name=key.column_name, parent_key=parent.fqn_key if parent else None,
        display_name=key.display, environment=_env_of(key.catalog) if env == "__auto__" else env,
        comment=comment, status=status, object_state=state, drift_detected_at=drift,
        updated_at=updated_at, last_applied_at=last_applied_at, last_apply_status=apply_status,
    ))
    for k, v in (tags or {}).items():
        db.add(MetadataTagModel(object_id=oid, key=k, value=v))
    db.flush()
    return oid


def _snap(db, name, column=None, *, stype=None, tags=None, comment=None, present=True, data_type=None):
    key = ObjectKey.parse(name, column)
    parent = key.parent()
    db.add(MetadataObservedModel(
        fqn_key=key.fqn_key, securable_type=stype or _LEVEL_TYPES[key.level], catalog=key.catalog.lower(),
        schema_name=key.schema_name, object_name=key.object_name, column_name=key.column_name,
        parent_key=parent.fqn_key if parent else None, display_name=key.display,
        tags=dict(tags or {}), comment=comment, present=present, data_type=data_type,
        observed_at=datetime(2026, 1, 1),
    ))
    db.flush()


@pytest.fixture
def world(db_session, fakes):
    db = db_session
    _obj(db, "sales_dev", tags={"data_owner": "team_a"})
    _obj(db, "sales_dev.bronze", comment="Raw landing zone")
    _obj(db, "sales_dev.bronze.orders", comment="All customer orders",
         tags={"classification": "restricted", "pii": None}, drift=datetime(2026, 3, 3),
         updated_at=datetime(2026, 3, 1), last_applied_at=datetime(2026, 3, 2), apply_status="applied")
    _obj(db, "sales_dev.bronze.orders", "Email", comment="Email", tags={"pii": None})
    _obj(db, "sales_dev.bronze.v_orders", stype="view", comment="100% of orders_2026")
    _obj(db, "sales_dev.silver.customers", state="pending", tags={"classification": "public"},
         updated_at=datetime(2026, 2, 1), last_applied_at=datetime(2026, 2, 2))
    _obj(db, "Sales_Prd.Gold.Revenue", state="orphaned", comment="Q2 revenue",
         tags={"classification": "restricted"})
    _obj(db, "sales_dev.bronze.old", status="deleted", tags={"classification": "restricted"})

    _snap(db, "sales_dev.bronze.orders", tags={"classification": "public"}, comment="Orders (UC)")
    _snap(db, "sales_dev.bronze.orders", "Email", data_type="string")
    _snap(db, "sales_dev.bronze.raw_events", comment="Raw events feed",
          tags={"classification": "restricted", "flagged": None, 'we"ird': "x"})
    _snap(db, "sales_dev.bronze.raw_events", "payload", data_type="string", tags={"pii": "true"})
    _snap(db, "sales_prd.gold.revenue", comment="Revenue (UC)")
    _snap(db, "sales_dev.bronze.dropped", present=False)
    _snap(db, "sales_dev.bronze.old")
    _snap(db, "hr.people.staff")
    return db


DEFAULT_KEYS = [
    "hr.people.staff",
    "sales_dev",
    "sales_dev.bronze",
    "sales_dev.bronze.old",
    "sales_dev.bronze.orders",
    "sales_dev.bronze.raw_events",
    "sales_dev.bronze.v_orders",
    "sales_dev.silver.customers",
    "sales_prd.gold.revenue",
]


def _keys(db, **query) -> List[str]:
    result = search(db, SearchQuery(page_size=500, **query), ALICE)
    assert result.total == len(result.items)
    return [i.fqn_key for i in result.items]


def _tags(*texts) -> List[TagFilter]:
    return [TagFilter.parse(t) for t in texts]


# --- the searched set ----------------------------------------------------------------


def test_default_search_lists_managed_and_unmanaged_non_columns_by_name(world):
    result = search(world, SearchQuery(), ALICE)

    assert [i.fqn_key for i in result.items] == DEFAULT_KEYS
    assert result.total == len(DEFAULT_KEYS)
    assert (result.page, result.page_size) == (1, 50)
    assert isinstance(result.took_ms, int) and result.took_ms >= 0


def test_summaries_carry_desired_and_observed_state(world):
    items = {i.fqn_key: i for i in search(world, SearchQuery(include_columns=True, page_size=500), ALICE).items}

    orders = items["sales_dev.bronze.orders"]
    assert orders.id and orders.managed is True
    assert (orders.name, orders.catalog, orders.schema_name, orders.object_name) == (
        "sales_dev.bronze.orders", "sales_dev", "bronze", "orders")
    assert orders.securable_type == "table" and orders.environment == "dev"
    assert orders.tags == {"classification": "restricted", "pii": None}
    assert orders.comment == "All customer orders"
    assert orders.observed_tags == {"classification": "public"}
    assert orders.observed_comment == "Orders (UC)"
    assert (orders.object_state, orders.drift, orders.last_apply_status) == ("exists", True, "applied")
    assert orders.updated_at == datetime(2026, 3, 1) and orders.last_applied_at == datetime(2026, 3, 2)
    assert orders.drift_detected_at == datetime(2026, 3, 3)
    assert set(orders.tag_versions) == {"classification", "pii"}
    assert all(isinstance(v, int) for v in orders.tag_versions.values())
    assert orders.comment_version == 0
    assert orders.editable is False

    email = items["sales_dev.bronze.orders::email"]
    assert (email.column, email.name, email.data_type, email.securable_type) == (
        "Email", "sales_dev.bronze.orders", "string", "column")

    # Desired rows keep their names as written; the key is normalized.
    revenue = items["sales_prd.gold.revenue"]
    assert (revenue.name, revenue.catalog, revenue.environment) == ("Sales_Prd.Gold.Revenue", "Sales_Prd", "prd")

    raw = items["sales_dev.bronze.raw_events"]
    assert raw.id is None and raw.managed is False
    assert raw.tags == raw.observed_tags == {"classification": "restricted", "flagged": None, 'we"ird': "x"}
    assert raw.comment == raw.observed_comment == "Raw events feed"
    assert (raw.environment, raw.object_state, raw.drift, raw.updated_at) == ("dev", None, False, None)
    assert (raw.tag_versions, raw.comment_version, raw.drift_detected_at) == (None, None, None)

    # A managed object with no snapshot yet has no observed state.
    customers = items["sales_dev.silver.customers"]
    assert customers.observed_tags is None and customers.observed_comment is None
    assert items["hr.people.staff"].environment is None


def test_unmanaged_rows_exclude_missing_and_actively_managed_objects(world):
    keys = _keys(world, include_columns=True)

    assert keys.count("sales_dev.bronze.orders") == 1
    assert "sales_dev.bronze.dropped" not in keys
    # A deleted desired row leaves the object unmanaged.
    old = next(i for i in search(world, SearchQuery(q="old"), ALICE).items)
    assert old.fqn_key == "sales_dev.bronze.old" and old.managed is False and old.tags == {}

    managed_only = _keys(world, include_unmanaged=False)
    assert managed_only == [
        "sales_dev", "sales_dev.bronze", "sales_dev.bronze.orders", "sales_dev.bronze.v_orders",
        "sales_dev.silver.customers", "sales_prd.gold.revenue",
    ]


# --- filters -------------------------------------------------------------------------


def test_text_search_is_case_insensitive_over_names_and_comments(world):
    assert _keys(world, q="ORDERS") == ["sales_dev.bronze.orders", "sales_dev.bronze.v_orders"]
    assert _keys(world, q="customer") == ["sales_dev.bronze.orders", "sales_dev.silver.customers"]
    # Observed comments match too, for managed and unmanaged rows.
    assert _keys(world, q="(uc)") == ["sales_dev.bronze.orders", "sales_prd.gold.revenue"]
    assert _keys(world, q="events FEED") == ["sales_dev.bronze.raw_events"]
    assert _keys(world, q="  ") == DEFAULT_KEYS


def test_text_search_treats_like_wildcards_literally(world):
    assert _keys(world, q="%") == ["sales_dev.bronze.v_orders"]
    assert _keys(world, q="_2") == ["sales_dev.bronze.v_orders"]
    assert _keys(world, q="\\") == []


def test_catalog_and_schema_filters(world):
    assert _keys(world, catalogs=["SALES_PRD"]) == ["sales_prd.gold.revenue"]
    assert _keys(world, catalogs=["hr", "sales_prd"]) == ["hr.people.staff", "sales_prd.gold.revenue"]
    assert _keys(world, schemas=["Bronze"]) == [
        "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.orders",
        "sales_dev.bronze.raw_events", "sales_dev.bronze.v_orders",
    ]
    assert _keys(world, schemas=["sales_prd.gold", "hr.people"]) == ["hr.people.staff", "sales_prd.gold.revenue"]
    assert _keys(world, schemas=["hr.bronze"]) == []


def test_securable_type_filter_and_columns(world):
    assert _keys(world, securable_types=["view"]) == ["sales_dev.bronze.v_orders"]
    assert _keys(world, securable_types=["catalog", "schema"]) == ["sales_dev", "sales_dev.bronze"]
    # Asking for columns includes them without include_columns.
    assert _keys(world, securable_types=["column"]) == [
        "sales_dev.bronze.orders::email", "sales_dev.bronze.raw_events::payload",
    ]
    with_columns = _keys(world, include_columns=True)
    assert set(with_columns) - set(DEFAULT_KEYS) == {
        "sales_dev.bronze.orders::email", "sales_dev.bronze.raw_events::payload",
    }


def test_environment_and_object_state_filters(world):
    assert _keys(world, environments=["prd"]) == ["sales_prd.gold.revenue"]
    dev = _keys(world, environments=["dev"])
    assert "sales_dev.bronze.raw_events" in dev and "hr.people.staff" not in dev
    assert _keys(world, environments=["qa"]) == []
    # Unmanaged objects have no state, so a state filter leaves them out.
    assert _keys(world, object_states=["pending"]) == ["sales_dev.silver.customers"]
    assert _keys(world, object_states=["exists", "orphaned"]) == [
        "sales_dev", "sales_dev.bronze", "sales_dev.bronze.orders", "sales_dev.bronze.v_orders",
        "sales_prd.gold.revenue",
    ]


def test_tag_value_filter_matches_desired_tags_and_observed_tags_of_unmanaged_rows(world):
    # Desired wins for a managed row: orders is restricted in desired state, public in UC.
    assert _keys(world, tags=_tags("classification=restricted")) == [
        "sales_dev.bronze.orders", "sales_dev.bronze.raw_events", "sales_prd.gold.revenue",
    ]
    assert _keys(world, tags=_tags("classification=public")) == ["sales_dev.silver.customers"]
    # Values match exactly.
    assert _keys(world, tags=_tags("classification=Restricted")) == []


def test_key_only_tags_match_an_empty_value(world):
    assert _keys(world, tags=_tags("pii=")) == ["sales_dev.bronze.orders"]
    assert _keys(world, tags=_tags("flagged=")) == ["sales_dev.bronze.raw_events"]
    assert _keys(world, tags=_tags("pii=", "flagged=")) == []
    assert _keys(world, tags=_tags("classification=")) == []
    assert _keys(world, include_columns=True, tags=_tags("pii=true")) == ["sales_dev.bronze.raw_events::payload"]


def test_tag_exists_and_missing_filters(world):
    assert _keys(world, tags=_tags("pii")) == ["sales_dev.bronze.orders"]
    assert _keys(world, include_columns=True, tags=_tags("pii")) == [
        "sales_dev.bronze.orders", "sales_dev.bronze.orders::email", "sales_dev.bronze.raw_events::payload",
    ]
    assert _keys(world, tags=_tags("flagged")) == ["sales_dev.bronze.raw_events"]
    assert _keys(world, tags=_tags("!classification")) == [
        "hr.people.staff", "sales_dev", "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.v_orders",
    ]
    # Filters combine with AND.
    assert _keys(world, tags=_tags("classification=restricted", "!pii")) == [
        "sales_dev.bronze.raw_events", "sales_prd.gold.revenue",
    ]
    # A blank key is ignored.
    assert _keys(world, tags=[TagFilter(key=" ", op="exists")]) == DEFAULT_KEYS


def test_tag_keys_that_a_json_path_cannot_quote(world):
    assert _keys(world, tags=_tags('we"ird')) == ["sales_dev.bronze.raw_events"]
    assert _keys(world, tags=_tags('we"ird=x')) == ["sales_dev.bronze.raw_events"]
    assert _keys(world, tags=_tags('we"ird=y')) == []
    assert "sales_dev.bronze.raw_events" not in _keys(world, tags=_tags('!we"ird'))
    assert len(_keys(world, tags=_tags('!we"ird'))) == len(DEFAULT_KEYS) - 1


def test_drift_filter(world):
    assert _keys(world, drift=True) == ["sales_dev.bronze.orders"]
    not_drifted = _keys(world, drift=False)
    assert "sales_dev.bronze.orders" not in not_drifted
    assert "sales_dev.bronze.raw_events" in not_drifted


# --- editable ------------------------------------------------------------------------


def test_editable_only_with_full_access_filters_nothing(world, fakes):
    fakes.scope = EditableScope(all=True)

    result = search(world, SearchQuery(editable_only=True, page_size=500), ALICE)

    assert [i.fqn_key for i in result.items] == DEFAULT_KEYS
    assert all(i.editable for i in result.items)


def test_catalogs_this_deployment_doesnt_manage_are_never_editable(world, fakes, monkeypatch):
    fakes.scope = EditableScope(all=True)
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*_dev"])

    result = search(world, SearchQuery(page_size=500), ALICE)
    editable = {i.fqn_key: i.editable for i in result.items}
    assert editable["sales_dev.bronze.orders"] is True
    assert any(not k.startswith("sales_dev") for k in editable)
    assert all(v is False for k, v in editable.items() if not k.startswith("sales_dev"))
    assert all(k.startswith("sales_dev") for k in _keys(world, editable_only=True))


def test_editable_only_follows_the_nearest_owner(world, fakes):
    fakes.scope = EditableScope(owned=["sales_dev"], not_owned=["sales_dev.silver", "sales_prd"])
    assert _keys(world, editable_only=True) == [
        "sales_dev", "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.orders",
        "sales_dev.bronze.raw_events", "sales_dev.bronze.v_orders",
    ]

    # An owned object below a not-owned one is editable again.
    fakes.scope = EditableScope(owned=["sales_dev", "sales_dev.silver.customers"], not_owned=["sales_dev.silver"])
    assert "sales_dev.silver.customers" in _keys(world, editable_only=True)

    # Columns inherit from their relation; an owner tag on a column overrides it.
    fakes.scope = EditableScope(owned=["sales_dev.bronze.orders"], not_owned=["sales_dev.bronze.orders::email"])
    assert _keys(world, editable_only=True, include_columns=True) == ["sales_dev.bronze.orders"]
    fakes.scope = EditableScope(owned=["sales_dev.bronze.orders"])
    assert _keys(world, editable_only=True, include_columns=True) == [
        "sales_dev.bronze.orders", "sales_dev.bronze.orders::email",
    ]

    # A key prefix that isn't an ancestor doesn't count.
    fakes.scope = EditableScope(owned=["sales_dev.bronze.order"])
    assert _keys(world, editable_only=True) == []

    fakes.scope = EditableScope()
    assert _keys(world, editable_only=True) == []


def test_editable_only_matches_principal_globs_part_by_part(world, fakes):
    fakes.scope = EditableScope(globs=["sales_*.bronze"])
    assert _keys(world, editable_only=True, include_columns=True) == [
        "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.orders", "sales_dev.bronze.orders::email",
        "sales_dev.bronze.raw_events", "sales_dev.bronze.raw_events::payload", "sales_dev.bronze.v_orders",
    ]

    # ``*`` never spans a dot.
    fakes.scope = EditableScope(globs=["*orders"])
    assert _keys(world, editable_only=True) == []
    fakes.scope = EditableScope(globs=["*.*.*orders"])
    assert _keys(world, editable_only=True) == ["sales_dev.bronze.orders", "sales_dev.bronze.v_orders"]
    fakes.scope = EditableScope(globs=["*.gold"])
    assert _keys(world, editable_only=True) == ["sales_prd.gold.revenue"]

    # ``?`` is one character; LIKE wildcards in a glob are literal; a glob can't name a column.
    fakes.scope = EditableScope(globs=["sales?dev"])
    assert "sales_dev" in _keys(world, editable_only=True)
    fakes.scope = EditableScope(globs=["sales%", "sales_dev.bronze.orders.email"])
    assert _keys(world, editable_only=True) == []

    fakes.scope = EditableScope(globs=["*"])
    assert _keys(world, editable_only=True) == DEFAULT_KEYS


def test_editable_only_respects_a_principals_environments(world, fakes):
    fakes.scope = EditableScope(globs=["sales_*"], environments=["dev"])
    keys = _keys(world, editable_only=True)
    assert keys and all(k.startswith("sales_dev") for k in keys)
    assert "sales_prd.gold.revenue" not in keys

    items = {i.fqn_key: i.editable for i in search(world, SearchQuery(page_size=500), ALICE).items}
    assert items["sales_dev.bronze.orders"] is True
    assert items["sales_prd.gold.revenue"] is False


def test_editable_flag_is_reported_without_filtering(world, fakes):
    fakes.scope = EditableScope(owned=["sales_dev.bronze"], globs=["hr.people"])

    items = {i.fqn_key: i.editable for i in search(world, SearchQuery(page_size=500), ALICE).items}

    assert [k for k, editable in items.items() if editable] == [
        "hr.people.staff", "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.orders",
        "sales_dev.bronze.raw_events", "sales_dev.bronze.v_orders",
    ]


@pytest.mark.parametrize("scope, key, expected", [
    (EditableScope(owned=["a.b"]), "a.b.c::d", True),
    (EditableScope(owned=["a.b"]), "a.bc", False),
    (EditableScope(owned=["A.B"]), "a.b.c", True),
    (EditableScope(owned=["a"], not_owned=["a.b"]), "a.b.c", False),
    (EditableScope(owned=["a", "a.b.c"], not_owned=["a.b"]), "a.b.c::x", True),
    (EditableScope(owned=["a"], not_owned=["b"]), "a.x", True),
    (EditableScope(globs=["a_*.raw"]), "a_dev.raw.t::c", True),
    (EditableScope(globs=["a_*.raw"]), "a_dev.rawer", False),
    (EditableScope(globs=["*.raw.*"]), "x.raw", False),
    (EditableScope(globs=["*"]), "x", True),
    (EditableScope(globs=["a.b.c.d"]), "a.b.c::d", False),
    (EditableScope(all=True), "anything", True),
])
def test_editable_predicate(scope, key, expected):
    assert editable_predicate(scope)(key) is expected


# --- sort, paging, facets ------------------------------------------------------------


def test_sort_by_name_descending(world):
    assert _keys(world, order="desc") == list(reversed(DEFAULT_KEYS))


def test_date_sorts_put_nulls_last_and_break_ties_by_key(world):
    unmanaged = ["hr.people.staff", "sales_dev.bronze.old", "sales_dev.bronze.raw_events"]
    default_dated = ["sales_dev", "sales_dev.bronze", "sales_dev.bronze.v_orders", "sales_prd.gold.revenue"]

    assert _keys(world, sort="updated_at", order="desc") == [
        "sales_dev.bronze.orders", "sales_dev.silver.customers", *default_dated, *unmanaged,
    ]
    assert _keys(world, sort="updated_at") == [
        *default_dated, "sales_dev.silver.customers", "sales_dev.bronze.orders", *unmanaged,
    ]
    assert _keys(world, sort="last_applied_at")[:2] == ["sales_dev.silver.customers", "sales_dev.bronze.orders"]
    assert _keys(world, sort="drift_detected_at")[0] == "sales_dev.bronze.orders"


def test_sort_by_catalog_and_type(world):
    assert _keys(world, sort="catalog", order="desc") == [
        "sales_prd.gold.revenue",
        "sales_dev", "sales_dev.bronze", "sales_dev.bronze.old", "sales_dev.bronze.orders",
        "sales_dev.bronze.raw_events", "sales_dev.bronze.v_orders", "sales_dev.silver.customers",
        "hr.people.staff",
    ]
    by_type = _keys(world, sort="securable_type")
    assert by_type[:2] == ["sales_dev", "sales_dev.bronze"]
    assert by_type[-1] == "sales_dev.bronze.v_orders"


def test_paging_is_server_side_with_a_stable_total(world):
    page2 = search(world, SearchQuery(page=2, page_size=4), ALICE)
    assert [i.fqn_key for i in page2.items] == DEFAULT_KEYS[4:8]
    assert (page2.total, page2.page, page2.page_size) == (len(DEFAULT_KEYS), 2, 4)

    past_end = search(world, SearchQuery(page=10, page_size=4), ALICE)
    assert past_end.items == [] and past_end.total == len(DEFAULT_KEYS)
    assert past_end.facets["catalog"]


def _facet(result, name) -> List[tuple]:
    return [(f.value, f.count) for f in result.facets[name]]


def test_facets_cover_the_whole_filtered_set(world):
    result = search(world, SearchQuery(page_size=1), ALICE)

    assert len(result.items) == 1
    assert _facet(result, "catalog") == [("sales_dev", 7), ("hr", 1), ("sales_prd", 1)]
    assert _facet(result, "securable_type") == [("table", 6), ("catalog", 1), ("schema", 1), ("view", 1)]
    assert _facet(result, "environment") == [("dev", 7), ("prd", 1)]
    assert _facet(result, "object_state") == [("exists", 4), ("orphaned", 1), ("pending", 1)]
    assert _facet(result, "tag_key") == [("classification", 3), ("data_owner", 1), ("pii", 1)]

    filtered = search(world, SearchQuery(catalogs=["sales_prd"]), ALICE)
    assert _facet(filtered, "catalog") == [("sales_prd", 1)]
    assert _facet(filtered, "tag_key") == [("classification", 1)]
    assert "schema" not in result.facets
    assert _facet(filtered, "schema") == [("gold", 1)]

    with_columns = search(world, SearchQuery(include_columns=True, q="orders"), ALICE)
    assert ("pii", 2) in _facet(with_columns, "tag_key")


def test_no_matches_returns_empty_facets(world):
    result = search(world, SearchQuery(q="nothing matches this"), ALICE)

    assert result.total == 0 and result.items == []
    assert result.facets == {k: [] for k in ("catalog", "securable_type", "environment", "object_state", "tag_key")}


def test_tag_key_facet_is_capped(db_session, fakes, monkeypatch):
    monkeypatch.setattr(search_mod, "TAG_KEY_FACET_LIMIT", 2)
    _obj(db_session, "c.s.t", tags={"a": "1", "b": "1", "c": "1"})
    _obj(db_session, "c.s.u", tags={"c": "2", "b": "2"})

    result = search(db_session, SearchQuery(), ALICE)

    assert _facet(result, "tag_key") == [("b", 2), ("c", 2)]


# --- list_keys -----------------------------------------------------------------------


def test_list_keys_merges_usage_policies_exemptions_and_governance(world, fakes):
    fakes.policies = [
        KeyPolicy(key="classification", allowed_values=["public", "restricted"]),
        KeyPolicy(key="data_steward", description="Who answers questions"),
    ]
    fakes.exempt = {"pii"}
    world.add(MetadataSyncStateModel(key=observed.GOVERNED_TAGS_KEY, value={"tags": {"Classification": []}}))
    world.flush()

    keys = {k.key: k for k in list_keys(world)}

    assert list(keys) == ["classification", "pii", "data_owner", "data_steward"]
    classification = keys["classification"]
    # The deleted object's tag isn't counted.
    assert classification.count == 3
    assert [(v.value, v.count) for v in classification.values] == [("restricted", 2), ("public", 1)]
    assert classification.policy.allowed_values == ["public", "restricted"]
    assert classification.governed is True and classification.exempt is False
    pii = keys["pii"]
    assert (pii.count, pii.exempt, pii.governed) == (2, True, False)
    assert [(v.value, v.count) for v in pii.values] == [("", 2)]
    steward = keys["data_steward"]
    assert (steward.count, steward.values, steward.policy.description) == (0, [], "Who answers questions")


def test_list_keys_filters_by_substring_and_limits(world, fakes):
    fakes.policies = [KeyPolicy(key="data_steward")]

    assert [k.key for k in list_keys(world, q="CLASS")] == ["classification"]
    assert [k.key for k in list_keys(world, q="data")] == ["data_owner", "data_steward"]
    assert [k.key for k in list_keys(world, q="steward")] == ["data_steward"]
    assert [k.key for k in list_keys(world, q="_")] == ["data_owner", "data_steward"]
    assert [k.key for k in list_keys(world, limit=1)] == ["classification"]
    assert list_keys(world, q="nope") == []


def test_list_keys_keeps_the_ten_most_used_values(db_session, fakes):
    for i in range(12):
        _obj(db_session, f"c.s.t{i}", tags={"team": f"team_{i % 11:02d}"})

    (team,) = list_keys(db_session)

    assert team.count == 12
    assert len(team.values) == 10
    assert (team.values[0].value, team.values[0].count) == ("team_00", 2)


# --- performance ---------------------------------------------------------------------


def test_search_stays_fast_at_twenty_thousand_rows(db_session, fakes):
    db = db_session
    objects, tags, snapshot = [], [], []
    for i in range(10_000):
        catalog, schema, table = f"cat_{i % 20}_dev", f"schema_{i % 50}", f"table_{i}"
        fqn = f"{catalog}.{schema}.{table}"
        oid = f"perf-{i}"
        objects.append(dict(
            id=oid, fqn_key=fqn, securable_type="table", catalog=catalog, schema_name=schema, object_name=table,
            parent_key=f"{catalog}.{schema}", display_name=fqn, environment="dev", status="active",
            object_state="exists", comment=f"Table number {i}", updated_at=datetime(2026, 1, 1 + i % 28),
            comment_version=0,
        ))
        tags.append(dict(object_id=oid, key="classification", value=("restricted", "public", "internal")[i % 3],
                         version=1, set_at=datetime(2026, 1, 1)))
        tags.append(dict(object_id=oid, key="owner", value=f"team_{i % 7}", version=1, set_at=datetime(2026, 1, 1)))
        snapshot.append(dict(
            fqn_key=fqn, securable_type="table", catalog=catalog, schema_name=schema, object_name=table,
            parent_key=f"{catalog}.{schema}", display_name=fqn, tags={"classification": "public"}, present=True,
            observed_at=datetime(2026, 1, 1),
        ))
    for i in range(5_000):
        catalog, schema, table = f"cat_{i % 20}_dev", f"schema_{i % 50}", f"raw_{i}"
        fqn = f"{catalog}.{schema}.{table}"
        snapshot.append(dict(
            fqn_key=fqn, securable_type="table", catalog=catalog, schema_name=schema, object_name=table,
            parent_key=f"{catalog}.{schema}", display_name=fqn, tags={"classification": "restricted"},
            comment=f"Raw feed {i}", present=True, observed_at=datetime(2026, 1, 1),
        ))
    db.execute(insert(MetadataObjectModel.__table__), objects)
    db.execute(insert(MetadataTagModel.__table__), tags)
    db.execute(insert(MetadataObservedModel.__table__), snapshot)
    db.flush()
    fakes.scope = EditableScope(owned=[f"cat_{i}_dev" for i in range(5)], not_owned=["cat_1_dev.schema_1"])

    queries = [
        SearchQuery(),
        SearchQuery(q="number 99", sort="updated_at", order="desc"),
        SearchQuery(tags=_tags("classification=restricted"), page=3),
        SearchQuery(tags=_tags("!owner"), catalogs=["cat_3_dev"]),
        SearchQuery(editable_only=True, environments=["dev"], sort="catalog"),
    ]
    totals = []
    for query in queries:
        started = time.perf_counter()
        result = search(db, query, ALICE)
        elapsed = time.perf_counter() - started
        totals.append(result.total)
        assert elapsed < 1.0, f"{query!r} took {elapsed:.3f}s"

    assert totals[0] == 15_000
    assert totals[2] == 3_334 + 5_000
    assert totals[3] == 250
