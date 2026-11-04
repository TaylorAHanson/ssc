"""Discover domains come from Databricks: membership is exact key-only tags, direct or from the catalog/schema."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.db.data_asset import DataAssetModel
from app.db.discover_domain import DiscoverDomainModel
from app.services import discover_domains as dd
from app.services.discover_domains import EMPTY, OTHER_DOMAIN, Domain, DomainIndex, Membership
from app.tools.self_service.search_data_assets import search_data_assets
from tests.factories.discover_domain_factory import add_domain


@pytest.fixture
def index():
    return DomainIndex([
        Domain(id="sc", tag_key="supplychain", parent_id=None, name="supplychain"),
        Domain(id="sc-proc", tag_key="supplychain/procurement", parent_id="sc", name="procurement"),
        Domain(id="fin", tag_key="Finance", parent_id=None, name="Finance"),
        # Nowhere to show: its parent isn't a published domain, or is itself a subdomain.
        Domain(id="orphan", tag_key="gone/child", parent_id="gone", name="child"),
        Domain(id="deep", tag_key="supplychain/procurement/x", parent_id="sc-proc", name="x"),
    ])


def test_own_and_inherited_tags_count_but_column_tags_do_not(index):
    assert index.for_tags(["SupplyChain"], None) == Membership(("supplychain",), ())
    assert index.for_tags([], {"catalog": ["finance"], "schema": [], "column": []}) == Membership(("Finance",), ())
    assert index.for_tags([], {"schema": ["supplychain"]}).domains == ("supplychain",)
    assert index.for_tags([], {"catalog": [], "schema": [], "column": ["finance"]}) == EMPTY


def test_a_subdomain_tag_also_puts_the_asset_in_its_parent(index):
    m = index.for_tags(["supplychain/procurement", "finance"], None)
    assert m == Membership(("Finance", "supplychain"), ("supplychain/procurement",))


def test_values_and_lookalike_keys_are_not_domains(index):
    # key=value tags match on the key only, and a "/" inside a value means nothing.
    assert index.for_tags(["uc_volume=/Volumes/supplychain/procurement", "supplychain_raw"], None) == EMPTY
    assert index.for_tags(["supplychain=yes"], None).domains == ("supplychain",)
    # Unplaceable subdomains are dropped.
    assert index.for_tags(["gone/child", "supplychain/procurement/x"], None) == EMPTY


def test_matches_by_tag_key_id_or_subdomain_name(index):
    m = index.for_tags(["supplychain/procurement"], None)
    assert index.matches(m, domain="SUPPLYCHAIN") and index.matches(m, domain="sc")
    assert index.matches(m, domain="supplychain", subdomain="Procurement")
    assert index.matches(m, subdomain="supplychain/procurement")
    assert not index.matches(m, domain="finance")
    assert not index.matches(m, domain="supplychain/procurement")  # a subdomain isn't a domain filter
    assert not index.matches(m, domain="nope")


def test_other_matches_only_assets_in_no_domain(index):
    assert index.matches(EMPTY, domain=OTHER_DOMAIN)
    assert not index.matches(index.for_tags(["finance"], None), domain=OTHER_DOMAIN)
    assert not index.matches(EMPTY, domain=OTHER_DOMAIN, subdomain="anything")
    # With no domains cached at all, everything is Other.
    assert DomainIndex().matches(DomainIndex().for_tags(["finance"], None), domain=OTHER_DOMAIN)


def test_search_ignores_case_spaces_and_punctuation(index):
    assert [d.id for d in index.search("Supply Chain") if not d.is_subdomain] == ["sc"]
    assert [d.id for d in index.search("procurement")] == ["sc-proc"]
    assert index.search("   ") == []


def test_drafts_are_not_loaded(db_session):
    add_domain(db_session, "published")
    add_domain(db_session, "secret", draft=True)
    assert [d.tag_key for d in dd.load_domain_index(db_session).top_level] == ["published"]


# --- sync -------------------------------------------------------------------

def _client(pages):
    client = MagicMock()
    client.api_client.do.side_effect = pages
    client.users.get.side_effect = lambda i: SimpleNamespace(display_name=f"User {i}", user_name=None)
    return client


@pytest.fixture(autouse=True)
def _fresh_principal_names():
    dd._principal_names.clear()
    yield
    dd._principal_names.clear()


def test_refresh_pages_through_domains_and_names_owners_only_where_used(db_session):
    db_session.add(DataAssetModel(id="c.s.t", catalog="c", schema="s", table_name="t", type="MANAGED",
                                  tags=["SupplyChain/Procurement"]))
    db_session.flush()
    client = _client([
        {"domains": [{"domain_id": "sc", "tag_key": "supplychain", "business_owner_ids": ["7"],
                      "icon": {"name": "TRUCK_TRAILER", "color": "#137DAE"}, "subtitle": "Plan and deliver"}],
         "next_page_token": "p2"},
        {"domains": [{"domain_id": "sc-proc", "tag_key": "supplychain/procurement", "parent_domain_id": "sc",
                      "technical_owner_ids": ["8"]},
                     {"domain_id": "unused", "tag_key": "unused", "business_owner_ids": ["9"]},
                     {"domain_id": "draft", "tag_key": "draft", "effective_draft": True},
                     {"tag_key": "no id"}]},
    ])

    assert dd.refresh_domain_cache(client, db_session) == 4
    assert client.api_client.do.call_args_list[1].kwargs["query"] == {"page_size": 100, "page_token": "p2"}
    rows = {r.id: r for r in db_session.query(DiscoverDomainModel).all()}
    assert rows["sc"].business_owners == [{"id": 7, "name": "User 7"}]
    assert rows["sc"].icon == {"name": "TRUCK_TRAILER", "color": "#137DAE"}
    assert rows["sc-proc"].technical_owners == [{"id": 8, "name": "User 8"}]
    assert rows["unused"].business_owners is None
    assert rows["draft"].draft
    assert {c.args[0] for c in client.users.get.call_args_list} == {"7", "8"}

    index = dd.load_domain_index(db_session)
    assert [(d.name, d.subtitle) for d in index.children("sc")] == [("procurement", None)]
    assert index.get("supplychain").business_owners == ("User 7",)


def test_refresh_keeps_the_last_list_when_databricks_says_no(db_session):
    add_domain(db_session, "kept")
    client = MagicMock()
    client.api_client.do.side_effect = PermissionError("MANAGE DISCOVER required")
    assert dd.refresh_domain_cache(client, db_session) is None
    assert [r.tag_key for r in db_session.query(DiscoverDomainModel).all()] == ["kept"]


# --- agent search tool ------------------------------------------------------

def test_search_tool_filters_by_domain_and_finds_other(db_session):
    sc = add_domain(db_session, "supplychain")
    add_domain(db_session, "supplychain/procurement", parent=sc)
    db_session.add_all([
        DataAssetModel(id="c.sc.suppliers", catalog="c", schema="sc", table_name="suppliers", type="MANAGED",
                       tags=["supplychain/procurement"]),
        DataAssetModel(id="c.sc.orders", catalog="c", schema="sc", table_name="orders", type="MANAGED",
                       scope_tag_keys={"catalog": [], "schema": ["supplychain"], "column": []}),
        DataAssetModel(id="c.raw.orders_raw", catalog="c", schema="raw", table_name="orders_raw", type="MANAGED"),
    ])
    db_session.flush()

    def run(**kw):
        with patch("app.tools.self_service.search_data_assets.get_db", return_value=iter([db_session])):
            return sorted(a["id"] for a in search_data_assets._func(**kw)["assets"])

    assert run(query="orders", domain="Supply Chain") == ["c.sc.orders"]
    assert run(query="suppliers", subdomain="procurement") == ["c.sc.suppliers"]
    assert run(query="orders", domain="other") == ["c.raw.orders_raw"]
    # The domain's name is searchable too.
    assert run(query="procurement") == ["c.sc.suppliers"]
