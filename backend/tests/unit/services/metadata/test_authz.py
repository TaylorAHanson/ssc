"""Who may edit what (ADR-0006 D4): actors, owner-tag inheritance, decisions, principals and editable scope."""
import uuid

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.db.metadata import MetadataObjectModel, MetadataObservedModel, MetadataTagModel
from app.models.user import User
from app.services.metadata import authz, environments, observed, principals
from app.services.metadata.types import (
    SYSTEM_ACTOR,
    Actor,
    ChangeItem,
    ObjectKey,
    ObservedState,
    PrincipalIn,
    PrincipalScope,
)

EMAIL = "jane@example.com"
POLICIES = [
    {"key": "dataset", "applies_to": "table", "pattern": "^[a-z_]+$"},
    {"key": "data_owner", "applies_to": "catalog, schema, table", "sensitive": True},
    {"key": "approver_group", "applies_to": "table", "sensitive": True},
    {"key": "classification", "allowed_values": "public, internal, confidential, restricted"},
    {"key": "steward", "editor_groups": "stewards"},
]
_LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}


@pytest.fixture(autouse=True)
def baseline(monkeypatch):
    s = monkeypatch.setattr
    s(settings, "METADATA_OWNER_TAG_KEY", "data_owner")
    s(settings, "METADATA_EDITOR_ROLES", ["Governance Admin"])
    s(settings, "METADATA_BROWSE_ROLES", [])
    s(settings, "METADATA_KEY_MODE", "open")
    s(settings, "METADATA_KEY_POLICIES", [dict(p) for p in POLICIES])
    s(settings, "METADATA_EXEMPT_KEYS", ["system.certification_status"])
    s(settings, "METADATA_CLASSIFICATION_KEY", "classification")
    s(settings, "METADATA_CLASSIFICATION_LEVELS", ["public", "internal", "confidential", "restricted"])
    s(settings, "METADATA_PII_KEYS", ["pii"])
    s(settings, "METADATA_ENVIRONMENTS", [
        {"key": "dev", "catalog_pattern": "{base}_dev", "approval": "sensitive"},
        {"key": "prd", "catalog_pattern": "{base}_prd", "approval": "all"},
    ])
    s(settings, "METADATA_GOVERNANCE_APPROVER_GROUP", "governance")
    s(settings, "METADATA_UC_OWNER_IS_AUTHORITY", False)
    s(observed, "governed_tags", lambda db: {})


# --- helpers --------------------------------------------------------------------------


def add(db, name, column=None, tags=None, *, status="active", securable_type=None):
    key = ObjectKey.parse(name, column)
    parent = key.parent()
    row = MetadataObjectModel(
        id=uuid.uuid4().hex, fqn_key=key.fqn_key, securable_type=securable_type or _LEVEL_TYPES[key.level],
        catalog=key.catalog, schema_name=key.schema_name, object_name=key.object_name,
        column_name=key.column_name, parent_key=parent.fqn_key if parent else None, display_name=key.display,
        environment=environments.environment_of(key.catalog), status=status, object_state="exists",
    )
    db.add(row)
    db.flush()
    for k, v in (tags or {}).items():
        db.add(MetadataTagModel(object_id=row.id, key=k, value=v))
    db.flush()
    return row


def observe(db, name, column=None, *, owner=None, securable_type="table", present=True):
    key = ObjectKey.parse(name, column)
    db.add(MetadataObservedModel(
        fqn_key=key.fqn_key, securable_type=securable_type, catalog=key.catalog, schema_name=key.schema_name,
        object_name=key.object_name, column_name=key.column_name, display_name=key.display, owner=owner,
        tags={}, present=present,
    ))
    db.flush()


def tag(name, key="dataset", after="orders", *, column=None, action="set", before=None, before_exists=None,
        securable_type=None, exists=True):
    k = ObjectKey.parse(name, column)
    return ChangeItem(
        fqn_key=k.fqn_key, name=k.name, column=k.column_name, securable_type=securable_type or _LEVEL_TYPES[k.level],
        environment=environments.environment_of(k.catalog), field="tag", key=key, action=action,
        before=before, before_exists=before is not None if before_exists is None else before_exists,
        after=after if action == "set" else None, object_exists=exists,
    )


def comment(name, text="Orders placed online.", *, column=None, action="comment_set"):
    k = ObjectKey.parse(name, column)
    return ChangeItem(
        fqn_key=k.fqn_key, name=k.name, column=k.column_name, securable_type=_LEVEL_TYPES[k.level],
        environment=environments.environment_of(k.catalog), field="comment", action=action,
        after=text if action == "comment_set" else None,
    )


def user(*groups, roles=(), admin=False, email=EMAIL):
    return Actor(id=email, email=email, groups=list(groups), roles=list(roles), is_admin=admin)


def principal(*groups, **scope):
    data = {"application_id": "app-1", "display_name": "Ingest", "scopes": ["sales_*.bronze", "*.gold.*"]}
    data.update(scope)
    return Actor(id="app-1", actor_type="service_principal", groups=list(groups),
                 principal=PrincipalScope(**data))


def one(db, actor, item, **kw):
    return authz.decide(db, actor, [item], **kw)[0]


@pytest.fixture
def tree(db_session):
    """sales_dev (sales_admins) > bronze (bronze_team, Ingest Team) > orders (no owner), customers (crm) > email (privacy)."""
    add(db_session, "sales_dev", tags={"data_owner": "sales_admins"})
    add(db_session, "sales_dev.bronze", tags={"data_owner": "bronze_team, Ingest Team", "dataset": None})
    add(db_session, "sales_dev.bronze.orders", tags={"dataset": "orders"})
    add(db_session, "sales_dev.bronze.orders", "id")
    add(db_session, "sales_dev.bronze.customers", tags={"data_owner": "crm"})
    add(db_session, "sales_dev.bronze.customers", "email", tags={"data_owner": "privacy", "pii": None})
    add(db_session, "sales_prd", tags={"data_owner": "sales_admins"})
    add(db_session, "hr_dev.gold.staff")
    return db_session


# --- actors ---------------------------------------------------------------------------


def test_actor_from_a_user(db_session):
    u = User(id=EMAIL, email=EMAIL, full_name="Jane Doe", entitlements=[EMAIL, "bronze_team", "Bronze_Team", "users"],
             roles=["Data Steward"])

    actor = authz.actor_from_user(db_session, u)

    assert actor.id == EMAIL and actor.email == EMAIL and actor.actor_type == "user"
    assert actor.display_name == "Jane Doe"
    assert actor.groups == ["bronze_team", "users"]
    assert actor.roles == ["Data Steward"]
    assert actor.is_admin is False and actor.principal is None


@pytest.mark.parametrize("roles,editor_roles,admin", [
    (["Platform Admin"], ["Governance Admin"], True),
    (["platform_admin"], [], True),
    (["governance_admin"], ["Governance Admin"], True),
    (["Data Steward"], ["Governance Admin"], False),
    (["Data Steward"], ["data steward"], True),
    ([], ["Governance Admin"], False),
])
def test_admins_are_platform_admins_and_editor_roles(db_session, monkeypatch, roles, editor_roles, admin):
    monkeypatch.setattr(settings, "METADATA_EDITOR_ROLES", editor_roles)
    u = User(id=EMAIL, email=EMAIL, full_name="Jane", entitlements=[EMAIL], roles=roles)

    assert authz.actor_from_user(db_session, u).is_admin is admin


def test_actor_from_a_registered_service_principal(db_session):
    principals.upsert_principal(db_session, PrincipalIn(application_id="app-1", display_name="Ingest",
                                                        scopes=["sales_*"], can_skip_approval=True), actor="admin")
    u = User(id="app-1", email="app-1", full_name="Ingest", entitlements=["app-1", "pipelines"],
             roles=["Platform Admin"], actor_type="service_principal", application_id="APP-1")

    actor = authz.actor_from_user(db_session, u)

    assert actor.id == "app-1" and actor.actor_type == "service_principal"
    assert actor.email is None
    assert actor.is_admin is False  # principals are never admins, whatever their groups map to
    assert actor.groups == ["pipelines"]
    assert type(actor.principal) is PrincipalScope
    assert actor.principal.scopes == ["sales_*"] and actor.principal.can_skip_approval is True


def test_an_unregistered_principal_has_no_scope_and_can_edit_nothing(db_session):
    u = User(id="ghost", email="ghost", full_name="ghost", entitlements=["ghost"], actor_type="service_principal",
             application_id="ghost")

    actor = authz.actor_from_user(db_session, u)
    d = one(db_session, actor, tag("sales_dev.bronze.orders")).decision

    assert actor.principal is None
    assert d.allowed is False and d.can_request is False
    assert "isn't a registered system principal" in d.reasons[0]


def test_system_actor():
    actor = authz.system_actor()

    assert actor.id == SYSTEM_ACTOR and actor.actor_type == "system" and actor.is_admin is True


def test_can_browse(monkeypatch):
    assert authz.can_browse(user()) is True

    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])

    assert authz.can_browse(user()) is False
    assert authz.can_browse(user(roles=["data_steward"])) is True
    assert authz.can_browse(user(admin=True)) is True
    assert authz.can_browse(principal()) is True
    assert authz.can_browse(authz.system_actor()) is True


# --- owner groups ---------------------------------------------------------------------


@pytest.mark.parametrize("name,column,groups,source", [
    ("sales_dev.bronze.customers", "email", ["privacy"], "sales_dev.bronze.customers::email"),
    ("sales_dev.bronze.customers", "phone", ["crm"], "sales_dev.bronze.customers"),
    ("sales_dev.bronze.customers", None, ["crm"], "sales_dev.bronze.customers"),
    ("sales_dev.bronze.orders", "id", ["bronze_team", "Ingest Team"], "sales_dev.bronze"),
    ("Sales_Dev.Bronze.New_Table", None, ["bronze_team", "Ingest Team"], "sales_dev.bronze"),
    ("sales_dev.silver", None, ["sales_admins"], "sales_dev"),
    ("sales_dev", None, ["sales_admins"], "sales_dev"),
    ("hr_dev.gold.staff", None, [], None),
])
def test_owner_tags_are_inherited_nearest_first(tree, name, column, groups, source):
    assert authz.owner_groups(tree, ObjectKey.parse(name, column)) == (groups, source)


def test_owner_groups_reads_all_levels_in_one_query(tree):
    statements = []
    conn = tree.connection()
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(conn, "before_cursor_execute", listener)
    try:
        authz.owner_groups(tree, ObjectKey.parse("sales_dev.bronze.orders", "id"))
    finally:
        event.remove(conn, "before_cursor_execute", listener)

    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


def test_key_only_owner_tags_deleted_rows_and_key_spellings(db_session):
    add(db_session, "c", tags={"DATA_OWNER": "cat_team"})
    add(db_session, "c.s", tags={"data_owner": None})
    add(db_session, "c.s.t", tags={"data_owner": "gone"}, status="deleted")
    add(db_session, "c.s.u", tags={"data_owner": " , "})

    assert authz.owner_groups(db_session, ObjectKey.parse("c.s.t")) == (["cat_team"], "c")
    assert authz.owner_groups(db_session, ObjectKey.parse("c.s.u")) == (["cat_team"], "c")


def test_multi_group_owner_values_are_split_and_deduplicated(db_session):
    add(db_session, "c", tags={"data_owner": "team_a, Team_A ,team_b,,"})

    assert authz.owner_groups(db_session, ObjectKey.parse("c.s")) == (["team_a", "team_b"], "c")


def test_the_owner_key_follows_the_setting(tree, monkeypatch):
    add(tree, "sales_dev.bronze.events", tags={"steward_group": "events_team"})
    monkeypatch.setattr(settings, "METADATA_OWNER_TAG_KEY", "steward_group")

    assert authz.owner_groups(tree, ObjectKey.parse("sales_dev.bronze.events")) == (
        ["events_team"], "sales_dev.bronze.events")
    assert authz.owner_groups(tree, ObjectKey.parse("sales_dev.bronze.customers")) == ([], None)


def test_pending_tags_overlay_stored_owners(tree):
    new = ObjectKey.parse("sales_dev.bronze.new_table")

    assert authz.owner_groups(tree, new, pending_tags={new.fqn_key: {"data_owner": "new_team"}}) == (
        ["new_team"], new.fqn_key)
    # None = the owner tag is being removed there, so the next level up applies.
    customers = ObjectKey.parse("sales_dev.bronze.customers")
    assert authz.owner_groups(tree, customers, pending_tags={customers.fqn_key: {"data_owner": None}}) == (
        ["bronze_team", "Ingest Team"], "sales_dev.bronze")
    # An overlay entry without the owner key leaves the stored value alone.
    assert authz.owner_groups(tree, customers, pending_tags={customers.fqn_key: {"pii": None}}) == (
        ["crm"], customers.fqn_key)
    # An ancestor's pending owner applies below it.
    assert authz.owner_groups(tree, new, pending_tags={"sales_dev.bronze": {"data_owner": "x"}}) == (
        ["x"], "sales_dev.bronze")


# --- decisions: users -----------------------------------------------------------------


def test_an_owner_writes_directly(tree):
    d = one(tree, user("Ingest team"), tag("sales_dev.bronze.orders", column="id", key="note", after="pk")).decision

    assert (d.allowed, d.needs_approval, d.via) == (True, False, "owner")
    assert d.owner_source == "sales_dev.bronze"
    assert d.approver_groups == [] and d.sensitive_reasons == []
    assert "Ingest Team" in d.reasons[0]


def test_comments_follow_the_same_authority(tree):
    d = one(tree, user("crm"), comment("sales_dev.bronze.customers")).decision

    assert d.allowed is True and d.via == "owner"


def test_a_nearer_owner_tag_overrides_an_owned_ancestor(tree):
    d = one(tree, user("bronze_team"), tag("sales_dev.bronze.customers", key="note", after="x")).decision

    assert (d.allowed, d.needs_approval, d.can_request, d.via) == (False, True, True, "none")
    assert d.approver_groups == ["crm"]
    assert d.owner_source == "sales_dev.bronze.customers"
    assert "crm" in d.reasons[0]


def test_no_owner_anywhere_routes_to_governance(tree, monkeypatch):
    d = one(tree, user("x"), tag("hr_dev.gold.staff", key="note", after="x")).decision

    assert (d.allowed, d.needs_approval, d.can_request) == (False, True, True)
    assert d.approver_groups == ["governance"]
    assert d.owner_source is None
    assert "only admins" in d.reasons[0]

    monkeypatch.setattr(settings, "METADATA_GOVERNANCE_APPROVER_GROUP", "")
    # Blank = Platform and Governance Admins.
    assert one(tree, user("x"), tag("hr_dev.gold.staff", key="note", after="x")).decision.approver_groups == []


def test_an_owner_changing_the_owner_tag_needs_owner_and_governance_approval(tree):
    d = one(tree, user("crm"), tag("sales_dev.bronze.customers", key="data_owner", after="crm, sales",
                                   before="crm")).decision

    assert (d.allowed, d.needs_approval, d.via) == (False, True, "owner")
    assert d.approver_groups == ["crm", "governance"]
    assert "owner tag" in d.sensitive_reasons[0]


def test_a_non_owner_changing_the_owner_tag_routes_to_governance(tree):
    d = one(tree, user("bronze_team"), tag("sales_dev.bronze.customers", key="data_owner", after="bronze_team",
                                           before="crm")).decision

    assert (d.allowed, d.needs_approval, d.can_request) == (False, True, True)
    assert d.approver_groups == ["governance"]


def test_a_sensitive_change_by_an_owner_needs_approval(tree):
    d = one(tree, user("crm"), tag("sales_dev.bronze.customers", key="approver_group", after="g")).decision

    assert (d.allowed, d.needs_approval, d.via) == (False, True, "owner")
    assert d.sensitive_reasons == ["'approver_group' is a sensitive key."]
    assert d.approver_groups == ["crm", "governance"]


def test_a_sensitive_change_by_a_non_owner_adds_governance(tree):
    d = one(tree, user("x"), tag("sales_dev.bronze.customers", key="pii", column="email", action="unset",
                                 before=None, before_exists=True)).decision

    assert d.needs_approval is True and d.allowed is False
    assert d.approver_groups == ["privacy", "governance"]
    assert d.sensitive_reasons == ["Removes the PII flag 'pii'."]


def test_admins_write_directly_but_sensitive_changes_still_need_approval(tree):
    plain = one(tree, user(admin=True), tag("hr_dev.gold.staff", key="note", after="x")).decision
    sensitive = one(tree, user(admin=True), tag("sales_dev.bronze.customers", key="classification",
                                                after="public", before="restricted")).decision

    assert (plain.allowed, plain.via) == (True, "admin")
    assert (sensitive.allowed, sensitive.needs_approval, sensitive.via) == (False, True, "admin")
    assert sensitive.approver_groups == ["crm", "governance"]


def test_an_environment_requiring_approval_for_everything(tree):
    owner = one(tree, user("sales_admins"), tag("sales_prd.bronze.orders", key="note", after="x")).decision
    admin = one(tree, user(admin=True), comment("sales_prd.bronze.orders")).decision
    dev = one(tree, user("sales_admins"), tag("sales_dev.silver.orders", key="note", after="x")).decision

    assert (owner.allowed, owner.needs_approval, owner.via) == (False, True, "owner")
    assert owner.sensitive_reasons == ["Every change in the prd environment needs approval."]
    assert owner.approver_groups == ["sales_admins", "governance"]
    assert (admin.allowed, admin.needs_approval) == (False, True)
    assert dev.allowed is True


def test_policy_denials_block_the_item_outright(tree):
    d = one(tree, user(admin=True), tag("sales_dev.bronze.orders", key="dataset", after="Not Valid!")).decision

    assert (d.allowed, d.needs_approval, d.can_request) == (False, False, False)
    assert "must match" in d.reasons[0]


def test_findings_are_attached_to_each_decision(tree):
    result = one(tree, user(admin=True), tag("sales_dev.bronze.orders", key="system.certification_status",
                                             after="certified"))

    assert [f.code for f in result.findings] == ["exempt_key"]
    assert result.decision.can_request is False


def test_governed_values_are_loaded_once_and_checked(tree, monkeypatch):
    calls = []

    def governed(db):
        calls.append(db)
        return {"region": ["emea", "amer"]}

    monkeypatch.setattr(observed, "governed_tags", governed)
    items = [tag("sales_dev.bronze.orders", key="region", after="apac"),
             tag("sales_dev.bronze.orders", key="region", after="emea", column="id")]

    decisions = authz.decide(tree, user(admin=True), items)

    assert len(calls) == 1
    assert [f.code for f in decisions[0].findings] == ["governed_value"]
    assert decisions[0].decision.can_request is False
    assert decisions[1].findings == [] and decisions[1].decision.allowed is True


def test_governed_values_are_not_loaded_without_tag_sets(tree, monkeypatch):
    monkeypatch.setattr(observed, "governed_tags", lambda db: pytest.fail("not needed"))

    authz.decide(tree, user(admin=True), [comment("sales_dev.bronze.orders"),
                                          tag("sales_dev.bronze.orders", key="note", action="unset", before="x")])


def test_unreadable_governed_values_are_skipped(tree, monkeypatch):
    def broken(db):
        raise RuntimeError("warehouse down")

    monkeypatch.setattr(observed, "governed_tags", broken)

    assert one(tree, user(admin=True), tag("sales_dev.bronze.orders", key="region", after="x")).decision.allowed


def test_decisions_keep_item_order(tree):
    items = [tag("hr_dev.gold.staff", key="a", after="1"), comment("sales_dev.bronze.customers"),
             tag("sales_dev.bronze.customers", key="b", after="2")]

    decisions = authz.decide(tree, user("crm"), items)

    assert [d.item for d in decisions] == items
    assert [d.decision.allowed for d in decisions] == [False, True, True]
    assert authz.decide(tree, user("crm"), []) == []


def test_the_uc_owner_counts_only_when_turned_on(tree, monkeypatch):
    key = ObjectKey.parse("sales_dev.bronze.orders")
    snapshot = {key.fqn_key: ObservedState(fqn_key=key.fqn_key, key=key, securable_type="table",
                                           owner=" Jane@Example.com ")}
    item = tag("sales_dev.bronze.orders", key="note", after="x")

    assert one(tree, user("x"), item, observed=snapshot).decision.allowed is False

    monkeypatch.setattr(settings, "METADATA_UC_OWNER_IS_AUTHORITY", True)
    d = one(tree, user("x"), item, observed=snapshot).decision

    assert (d.allowed, d.via) == (True, "uc_owner")


def test_the_uc_owner_is_read_from_the_snapshot_and_columns_use_their_relation(tree, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_UC_OWNER_IS_AUTHORITY", True)
    observe(tree, "sales_dev.bronze.orders", owner="Orders_Admins")
    observe(tree, "sales_dev.bronze.customers", owner="someone_else@example.com")

    col = one(tree, user("orders_admins"), tag("sales_dev.bronze.orders", column="id", key="n", after="x")).decision
    other = one(tree, user("orders_admins"), tag("sales_dev.bronze.customers", key="n", after="x")).decision
    owner_first = one(tree, user("crm"), tag("sales_dev.bronze.customers", key="n", after="x")).decision

    assert (col.allowed, col.via) == (True, "uc_owner")
    assert other.allowed is False
    assert owner_first.via == "owner"


def test_editor_groups_limit_who_changes_a_key(tree):
    owner = one(tree, user("crm"), tag("sales_dev.bronze.customers", key="steward", after="x")).decision
    steward = one(tree, user("crm", "Stewards"), tag("sales_dev.bronze.customers", key="steward", after="x")).decision
    outsider = one(tree, user("stewards"), tag("sales_dev.bronze.customers", key="steward", after="x")).decision
    admin = one(tree, user(admin=True), tag("sales_dev.bronze.customers", key="steward", after="x")).decision

    assert (owner.allowed, owner.needs_approval, owner.can_request) == (False, True, True)
    assert owner.approver_groups == ["stewards"]
    assert "Only members of stewards" in owner.reasons[-1]
    assert steward.allowed is True
    # Editor groups narrow authority; they don't grant it.
    assert outsider.allowed is False and outsider.approver_groups == ["crm"]
    assert admin.allowed is True


def test_an_owner_set_in_the_same_change_does_not_grant_authority_by_itself(tree):
    """Declaring a new table with your own group as owner: the owner change needs approval, so the rest waits too."""
    new = "sales_dev.bronze.new_table"
    items = [tag(new, key="data_owner", after="new_team", exists=False),
             tag(new, key="dataset", after="new_table", exists=False)]

    owner_item, dataset_item = [d.decision for d in authz.decide(tree, user("new_team"), items)]

    assert (owner_item.allowed, owner_item.needs_approval) == (False, True)
    assert owner_item.approver_groups == ["governance"]
    assert (dataset_item.allowed, dataset_item.needs_approval, dataset_item.can_request) == (False, True, True)
    assert dataset_item.via == "owner"
    assert dataset_item.reasons[0].startswith("Relies on setting 'data_owner'")
    assert dataset_item.approver_groups == ["bronze_team", "Ingest Team"]
    assert dataset_item.owner_source == "sales_dev.bronze"


def test_a_current_owner_handing_a_new_table_over_keeps_direct_authority_for_the_rest(tree):
    new = "sales_dev.bronze.new_table"
    items = [tag(new, key="data_owner", after="other_team", exists=False),
             tag(new, key="dataset", after="new_table", exists=False)]

    owner_item, dataset_item = [d.decision for d in authz.decide(tree, user("bronze_team"), items)]

    assert (owner_item.allowed, owner_item.needs_approval, owner_item.via) == (False, True, "owner")
    assert owner_item.approver_groups == ["bronze_team", "Ingest Team", "governance"]
    assert (dataset_item.allowed, dataset_item.via) == (True, "owner")


# --- decisions: principals and the system ---------------------------------------------


@pytest.mark.parametrize("name,in_scope", [
    ("sales_dev.bronze.orders", True),
    ("Sales_Dev.Bronze", True),            # the glob names the schema itself
    ("sales_dev", False),                  # a glob doesn't cover what's above it
    ("sales_dev.silver.orders", False),
    ("hr_dev.gold.staff", True),
    ("hr_dev.gold", False),                # *.gold.* covers relations, not the schema
])
def test_principal_name_globs(tree, name, in_scope):
    d = one(tree, principal(), tag(name, key="note", after="x")).decision

    assert d.allowed is in_scope
    assert d.via == ("principal" if in_scope else "none")
    if not in_scope:
        assert d.can_request is False and "outside" in d.reasons[0]


def test_glob_wildcards_do_not_span_name_parts(db_session):
    actor = principal(scopes=["*_dev.bronze"])

    assert one(db_session, actor, tag("x_dev.bronze.t", key="n", after="v")).decision.allowed is True
    assert one(db_session, actor, tag("x.y_dev.bronze", key="n", after="v")).decision.allowed is False


def test_principal_allowed_keys(tree):
    actor = principal(allowed_keys=["pii*", "Classification"])

    assert one(tree, actor, tag("sales_dev.bronze.orders", key="pii_level", after="x")).decision.allowed
    assert one(tree, actor, tag("sales_dev.bronze.orders", key="CLASSIFICATION", after="public")).decision.allowed
    assert one(tree, actor, comment("sales_dev.bronze.orders")).decision.allowed
    d = one(tree, actor, tag("sales_dev.bronze.orders", key="dataset", after="orders")).decision
    assert d.allowed is False and d.can_request is False and "allowed keys" in d.reasons[0]


@pytest.mark.parametrize("ops,item,ok", [
    (["set_tags"], tag("sales_dev.bronze.orders", key="n", after="x"), True),
    (["set_tags"], tag("sales_dev.bronze.orders", key="n", action="unset", before="x"), False),
    (["set_tags"], comment("sales_dev.bronze.orders"), False),
    (["replace_tags"], tag("sales_dev.bronze.orders", key="n", after="x"), True),
    (["replace_tags"], tag("sales_dev.bronze.orders", key="n", action="unset", before="x"), True),
    (["unset_tags"], tag("sales_dev.bronze.orders", key="n", action="unset", before="x"), True),
    (["set_comment"], comment("sales_dev.bronze.orders"), True),
    (["set_comment"], comment("sales_dev.bronze.orders", action="comment_clear"), False),
    (["clear_comment"], comment("sales_dev.bronze.orders", action="comment_clear"), True),
    ([], comment("sales_dev.bronze.orders", action="comment_clear"), True),
])
def test_principal_allowed_operations(tree, ops, item, ok):
    d = one(tree, principal(allowed_operations=ops), item).decision

    assert d.allowed is ok
    if not ok:
        assert "may not" in d.reasons[0]


def test_principal_environments(tree):
    actor = principal(scopes=["sales_*", "misc"], environments=["DEV"])

    assert one(tree, actor, tag("sales_dev.bronze.orders", key="n", after="x")).decision.allowed is True
    prd = one(tree, actor, tag("sales_prd.bronze.orders", key="n", after="x")).decision
    none = one(tree, actor, tag("misc.s.t", key="n", after="x")).decision
    assert prd.allowed is False and "prd environment" in prd.reasons[0]
    assert none.allowed is False and "no environment" in none.reasons[0]


def test_disabled_or_scopeless_principals_can_edit_nothing(tree):
    disabled = one(tree, principal(enabled=False), tag("sales_dev.bronze.orders", key="n", after="x")).decision
    scopeless = one(tree, principal(scopes=[]), tag("sales_dev.bronze.orders", key="n", after="x")).decision

    assert disabled.allowed is False and "disabled" in disabled.reasons[0]
    assert scopeless.allowed is False and "no scopes" in scopeless.reasons[0]


def test_owner_tags_never_widen_a_principals_scope(tree):
    d = one(tree, principal("crm", scopes=["hr_*"]), tag("sales_dev.bronze.customers", key="n", after="x")).decision

    assert (d.allowed, d.can_request, d.via) == (False, False, "none")


def test_principal_sensitive_changes_and_skipping_approval(tree):
    item = tag("sales_dev.bronze.orders", key="approver_group", after="g")

    gated = one(tree, principal(), item).decision
    skipping = one(tree, principal(can_skip_approval=True), item).decision

    assert (gated.allowed, gated.needs_approval, gated.via) == (False, True, "principal")
    assert gated.approver_groups == ["bronze_team", "Ingest Team", "governance"]
    assert (skipping.allowed, skipping.needs_approval) == (True, False)
    assert skipping.sensitive_reasons and "Approval skipped" in skipping.reasons[-1]


def test_principal_in_an_all_approval_environment(tree):
    item = tag("sales_prd.bronze.orders", key="n", after="x")

    assert one(tree, principal(scopes=["sales_*"]), item).decision.needs_approval is True
    assert one(tree, principal(scopes=["sales_*"], can_skip_approval=True), item).decision.allowed is True


def test_principal_editor_groups(tree):
    item = tag("sales_dev.bronze.orders", key="steward", after="x")

    d = one(tree, principal(), item).decision
    assert (d.allowed, d.needs_approval, d.can_request) == (False, True, True)
    assert one(tree, principal("stewards"), item).decision.allowed is True


def test_policy_denials_come_before_scope(tree):
    d = one(tree, principal(scopes=["hr_*"]), tag("sales_dev.bronze.orders", key="system.certification_status",
                                                 after="x")).decision

    assert "managed outside" in d.reasons[0]


def test_the_system_actor_writes_everything_without_approval(tree):
    d = one(tree, authz.system_actor(), tag("sales_prd.bronze.orders", key="classification", after="public",
                                            before="restricted")).decision

    assert (d.allowed, d.needs_approval, d.via) == (True, False, "admin")


# --- editable scope -------------------------------------------------------------------


def test_editable_scope_for_admins_and_the_system(tree):
    assert authz.editable_scope(tree, user(admin=True)).all is True
    assert authz.editable_scope(tree, authz.system_actor()).all is True


def test_editable_scope_for_principals(tree):
    assert authz.editable_scope(tree, principal(scopes=["Sales_*.Bronze", " "])).globs == ["sales_*.bronze"]
    assert authz.editable_scope(tree, principal(environments=["Dev", ""])).environments == ["dev"]
    assert authz.editable_scope(tree, principal(enabled=False)) == authz.EditableScope()
    unregistered = Actor(id="ghost", actor_type="service_principal")
    assert authz.editable_scope(tree, unregistered) == authz.EditableScope()


def test_editable_scope_lists_owned_and_not_owned_owner_tags(tree):
    add(tree, "sales_dev.bronze.archive", tags={"data_owner": None})
    add(tree, "sales_dev.bronze.old", tags={"data_owner": "crm"}, status="deleted")

    scope = authz.editable_scope(tree, user("Sales_Admins", "crm"))

    assert scope.all is False
    assert scope.owned == ["sales_dev", "sales_dev.bronze.customers", "sales_prd"]
    assert scope.not_owned == ["sales_dev.bronze", "sales_dev.bronze.customers::email"]
    assert scope.globs == []


def test_editable_scope_adds_uc_owned_relations_when_they_count(tree, monkeypatch):
    observe(tree, "sales_dev.bronze.customers", owner="JANE@example.com")
    observe(tree, "sales_dev.bronze", owner=EMAIL, securable_type="schema")
    observe(tree, "sales_dev.bronze.orders", owner=EMAIL, present=False)

    assert authz.editable_scope(tree, user()).owned == []

    monkeypatch.setattr(settings, "METADATA_UC_OWNER_IS_AUTHORITY", True)
    scope = authz.editable_scope(tree, user())

    assert scope.owned == ["sales_dev.bronze.customers"]
    # The relation's columns follow its UC owner, so their own owner tag no longer excludes them.
    assert "sales_dev.bronze.customers::email" not in scope.not_owned
    assert "sales_dev.bronze" in scope.not_owned


# --- is_editable ----------------------------------------------------------------------


def test_is_editable_for_users(tree):
    ok, reasons = authz.is_editable(tree, user("bronze_team"), ObjectKey.parse("sales_dev.bronze.orders", "id"))
    assert ok is True and "bronze_team" in reasons[0] and "sales_dev.bronze" in reasons[0]

    ok, reasons = authz.is_editable(tree, user("x"), ObjectKey.parse("sales_dev.bronze.customers"))
    assert ok is False and "crm" in reasons[0] and "request" in reasons[0]

    ok, reasons = authz.is_editable(tree, user("x"), ObjectKey.parse("hr_dev.gold.staff"))
    assert ok is False and "only admins" in reasons[0]


def test_is_editable_for_admins_and_all_approval_environments(tree):
    assert authz.is_editable(tree, user(admin=True), ObjectKey.parse("hr_dev"))[0] is True

    ok, reasons = authz.is_editable(tree, user(admin=True), ObjectKey.parse("sales_prd.bronze.orders"))
    assert ok is False and "prd environment" in reasons[-1]
    assert authz.is_editable(tree, user("sales_admins"), ObjectKey.parse("sales_prd"))[0] is False


def test_is_editable_for_principals_and_the_system(tree):
    assert authz.is_editable(tree, principal(), ObjectKey.parse("sales_dev.bronze.orders"))[0] is True
    ok, reasons = authz.is_editable(tree, principal(), ObjectKey.parse("sales_dev.silver.t"))
    assert ok is False and "outside" in reasons[0]
    prd = ObjectKey.parse("sales_prd.bronze.orders")
    assert authz.is_editable(tree, principal(scopes=["sales_*"]), prd)[0] is False
    assert authz.is_editable(tree, principal(scopes=["sales_*"], can_skip_approval=True), prd)[0] is True
    assert authz.is_editable(tree, authz.system_actor(), prd)[0] is True


def test_is_editable_for_the_uc_owner(tree, monkeypatch):
    observe(tree, "sales_dev.bronze.customers", owner="crm_admins")
    key = ObjectKey.parse("sales_dev.bronze.customers", "phone")

    assert authz.is_editable(tree, user("crm_admins"), key)[0] is False
    monkeypatch.setattr(settings, "METADATA_UC_OWNER_IS_AUTHORITY", True)
    ok, reasons = authz.is_editable(tree, user("crm_admins"), key)
    assert ok is True and "Unity Catalog" in reasons[0]
