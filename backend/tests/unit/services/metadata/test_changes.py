"""Change service: op semantics, cross-environment copies, routing, promote, revert, adopt, import (Workstream A).

Other workstreams' modules are replaced with small fakes (``Fakes``) so these tests
only exercise the change service and the store underneath it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.metadata import (
    MetadataChangeSetModel,
    MetadataEventModel,
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataReconcileQueueModel,
    MetadataSyncStateModel,
    MetadataTagModel,
)
from app.services.metadata import (
    authz,
    changes,
    environments,
    errors,
    observed,
    policy,
    reconciler,
    requests,
    store,
)
from app.services.metadata.types import (
    Actor,
    AdoptRequest,
    AuthzDecision,
    ChangeItem,
    ChangeOp,
    ChangeRequest,
    Finding,
    ImportRequest,
    ImportRow,
    ItemDecision,
    ObjectKey,
    ObservedState,
    PrincipalScope,
    PromoteRequest,
    RevertRequest,
)

METADATA_TABLES = [
    MetadataObjectModel.__table__,
    MetadataTagModel.__table__,
    MetadataChangeSetModel.__table__,
    MetadataEventModel.__table__,
    MetadataObservedModel.__table__,
    MetadataReconcileQueueModel.__table__,
    MetadataSyncStateModel.__table__,
]

JANE = Actor(id="jane@example.com")
BOB = Actor(id="bob@example.com")
ADMIN = Actor(id="admin@example.com", is_admin=True)
SYSTEM = Actor(id="system", actor_type="system", display_name="System", is_admin=True)

EXEMPT = {"system.certification_status", "metadata_manager_instance"}
SUFFIXES = {"dev": "_dev", "prd": "_prd"}
LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}

DEV = "Sales_Dev.Bronze.Orders"
DEV_KEY = "sales_dev.bronze.orders"
PRD_KEY = "sales_prd.bronze.orders"


def environment_of(catalog):
    c = (catalog or "").lower()
    return next((env for env, suffix in SUFFIXES.items() if c.endswith(suffix)), None)


def counterpart(key, env):
    own = environment_of(key.catalog)
    if own is None or env == own or env not in SUFFIXES:
        return None
    return key.model_copy(update={"catalog": key.catalog[: -len(SUFFIXES[own])] + SUFFIXES[env]})


class Fakes:
    """Stand-ins for authz, observed and requests. Authorization is decided per tag key."""

    def __init__(self, db):
        self.db = db
        self.observed = {}
        self.sensitive = set()   # keys ("description" for the comment) that need approval
        self.denied = set()      # keys the actor can't even request
        self.flagged = {}        # key -> a Finding decide attaches
        self.decided = []
        self.seeded = set()
        self.filed = []
        self.refreshed = []
        self.refresh_fails = False
        self.unreadable = set()  # fqn_keys refresh can't read

    def observe(self, name, column=None, *, tags=None, comment=None, securable_type=None, present=True):
        key = ObjectKey.parse(name, column)
        stype = securable_type or LEVEL_TYPES[key.level]
        self.observed[key.fqn_key] = ObservedState(fqn_key=key.fqn_key, key=key, securable_type=stype,
                                                   tags=tags or {}, comment=comment, present=present)
        parent = key.parent()
        self.db.add(MetadataObservedModel(
            fqn_key=key.fqn_key, securable_type=stype, catalog=key.catalog, schema_name=key.schema_name,
            object_name=key.object_name, column_name=key.column_name,
            parent_key=parent.fqn_key if parent else None, display_name=key.display, tags=tags or {},
            comment=comment, present=present,
        ))
        self.db.commit()

    def get_observed(self, db, fqn_keys):
        return {k: self.observed[k] for k in fqn_keys if k in self.observed}

    def get_columns(self, db, relation_fqn_key, *, include_missing=False):
        return [o for k, o in sorted(self.observed.items())
                if k.startswith(relation_fqn_key + "::") and (o.present or include_missing)]

    def refresh(self, db, keys, *, provider=None):
        keys = list(keys)
        self.refreshed.append(sorted(k.fqn_key for k in keys))
        if self.refresh_fails:
            raise RuntimeError("warehouse unavailable")
        return {k.fqn_key: self.observed.get(k.fqn_key) or ObservedState(
            fqn_key=k.fqn_key, key=k, securable_type=LEVEL_TYPES[k.level], present=False)
            for k in keys if k.fqn_key not in self.unreadable}

    def decide(self, db, actor, items, *, observed=None, seeded=()):
        self.decided.append(list(items))
        self.seeded = set(seeded)
        out = []
        for item in items:
            key = item.key if item.field == "tag" else "description"
            if key in self.denied:
                d = AuthzDecision(allowed=False, can_request=False, reasons=[f"You can't edit {key} on {item.name}."])
            elif key in self.sensitive:
                d = AuthzDecision(allowed=True, needs_approval=True, via="owner",
                                  sensitive_reasons=[f"{key} is sensitive."], approver_groups=["data_owners"])
            else:
                d = AuthzDecision(allowed=True, via="owner")
            out.append(ItemDecision(item=item, decision=d, findings=[self.flagged[key]] if key in self.flagged else []))
        return out

    def file(self, db, *, request, preview, items, actor, source=None, reverts_change_set_id=None):
        self.filed.append({"request": request, "items": [d.item for d in items], "actor": actor, "source": source,
                           "reverts": reverts_change_set_id})
        return f"req-{len(self.filed)}"


@pytest.fixture
def db():
    # Own engine: the shared db_session joins an outer transaction that rollback() would wipe.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=METADATA_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def fakes(db, monkeypatch):
    f = Fakes(db)
    monkeypatch.setattr(environments, "environment_of", environment_of)
    monkeypatch.setattr(environments, "counterpart", counterpart)
    monkeypatch.setattr(environments, "environment_keys", lambda: list(SUFFIXES))
    monkeypatch.setattr(policy, "is_exempt", lambda key: (key or "").strip().lower() in EXEMPT)
    monkeypatch.setattr(authz, "decide", f.decide)
    monkeypatch.setattr(authz, "system_actor", lambda: SYSTEM)
    monkeypatch.setattr(observed, "get_observed", f.get_observed)
    monkeypatch.setattr(observed, "get_columns", f.get_columns)
    monkeypatch.setattr(observed, "refresh", f.refresh)
    monkeypatch.setattr(requests, "file_change_request", f.file)
    monkeypatch.setattr(settings, "METADATA_MAX_OBJECTS_PER_CHANGE", 500)
    monkeypatch.setattr(settings, "METADATA_MIN_REASON_LENGTH", 0)
    monkeypatch.setattr(settings, "METADATA_ADOPT_MAX_READ_AGE_MINUTES", 0)
    return f


def op(kind, name=DEV, column=None, **kwargs):
    return ChangeOp(op=kind, name=name, column=column, **kwargs)


def ask(*ops, **kwargs):
    return ChangeRequest(ops=list(ops), **kwargs)


def submit(db, *ops, actor=JANE, source="ui", **kwargs):
    return changes.submit(db, ask(*ops, **kwargs), actor, source=source)


def preview(db, *ops, actor=JANE, **kwargs):
    return changes.preview(db, ask(*ops, **kwargs), actor)


def desired(db, fqn_key=DEV_KEY):
    return store.get_object(db, fqn_key)


def summary(pv):
    return [(d.item.fqn_key, d.item.key, d.item.action, d.item.after) for d in pv.items]


def seed_exempt(db, key, value, name=DEV):
    k = ObjectKey.parse(name)
    store.write_items(db, [ChangeItem(fqn_key=k.fqn_key, name=k.name, securable_type="table", field="tag", key=key,
                                      action="set", after=value)], actor=SYSTEM, source="system")


def change_set_count(db):
    return db.query(MetadataChangeSetModel).count()


# --- Op semantics ----------------------------------------------------------------


def test_set_tags_merges_and_unchanged_values_are_noops(db, fakes):
    submit(db, op("set_tags", tags={"a": "1", "b": "2"}))

    pv = preview(db, op("set_tags", tags={"a": "1", "c": "3"}))
    assert summary(pv) == [(DEV_KEY, "c", "set", "3")]
    assert pv.noop_count == 0

    pv = preview(db, op("set_tags", tags={"a": "1"}))
    assert pv.items == [] and pv.noop_count == 1
    assert "1 already true" in pv.report_markdown


def test_changing_a_value_expects_the_current_version(db, fakes):
    submit(db, op("set_tags", tags={"a": "1"}))
    item = preview(db, op("set_tags", tags={"a": "2"})).items[0].item
    assert (item.before, item.before_exists, item.before_version, item.after) == ("1", True, 1, "2")
    assert (item.name, item.environment, item.object_exists, item.object_state) == (DEV, "dev", True, "pending")


def test_key_only_tags(db, fakes):
    submit(db, op("set_tags", tags={"pii": ""}))
    assert desired(db).tags == {"pii": None}
    assert preview(db, op("set_tags", tags={"pii": None})).items == []


def test_unset_tags_removes_present_keys_only(db, fakes):
    submit(db, op("set_tags", tags={"a": "1", "b": "2"}))
    pv = preview(db, op("unset_tags", keys=["a", "missing", ""]))
    assert summary(pv) == [(DEV_KEY, "a", "unset", None)]
    assert pv.items[0].item.before_version == 1
    assert preview(db, op("unset_tags", keys=["missing"])).noop_count == 1


def test_replace_tags_sets_unsets_and_leaves_exempt_keys_alone(db, fakes):
    submit(db, op("set_tags", tags={"a": "1", "b": "2"}))
    seed_exempt(db, "system.certification_status", "certified")

    pv = preview(db, op("replace_tags", tags={"a": "1", "c": "3", "System.Certification_Status": "bogus"}))

    assert summary(pv) == [(DEV_KEY, "b", "unset", None), (DEV_KEY, "c", "set", "3")]
    assert pv.findings == [] and not pv.blocked


def test_naming_an_exempt_key_explicitly_is_denied(db, fakes):
    pv = preview(db, op("set_tags", tags={"system.certification_status": "x", "a": "1"}))
    assert pv.blocked
    assert [(f.code, f.key, f.fqn_key) for f in pv.findings] == [("exempt_key", "system.certification_status", DEV_KEY)]
    assert summary(pv) == [(DEV_KEY, "a", "set", "1")]

    with pytest.raises(errors.ChangeBlocked, match="managed outside"):
        submit(db, op("set_tags", tags={"system.certification_status": "x", "a": "1"}))
    assert desired(db) is None
    assert preview(db, op("unset_tags", keys=["METADATA_MANAGER_INSTANCE"])).blocked


def test_comment_ops(db, fakes):
    submit(db, op("set_comment", comment="Orders."))
    assert preview(db, op("set_comment", comment="Orders.")).noop_count == 1

    for cleared in (op("set_comment", comment=""), op("clear_comment")):
        item = preview(db, cleared).items[0].item
        assert (item.field, item.action, item.before, item.before_version, item.after) == (
            "comment", "comment_clear", "Orders.", 1, None)

    submit(db, op("clear_comment"))
    assert desired(db).comment.value is None
    assert preview(db, op("clear_comment")).noop_count == 1


def test_ops_on_one_object_apply_in_order(db, fakes):
    items, findings, noop = changes.resolve(db, ask(
        op("set_tags", tags={"a": "1"}),
        op("unset_tags", keys=["a"]),
        op("set_tags", tags={"b": "2"}),
        op("set_tags", tags={"b": "2"}),
    ), JANE)
    assert [(i.key, i.action) for i in items] == [("b", "set")]
    assert (findings, noop) == ([], 1)


def test_expected_versions_flow_to_items_and_stale_ones_conflict(db, fakes):
    submit(db, op("set_tags", tags={"a": "1"}), op("set_comment", comment="x"))

    with pytest.raises(errors.VersionConflict) as exc:
        submit(db, op("set_tags", tags={"a": "2"}, expected_versions={"a": 0}))
    assert [(c.key, c.expected_version, c.current_version) for c in exc.value.conflicts] == [("a", 0, 1)]
    with pytest.raises(errors.VersionConflict):
        submit(db, op("set_comment", comment="y", expected_comment_version=0))
    assert desired(db).tags == {"a": "1"}

    submit(db, op("set_tags", tags={"a": "2"}, expected_versions={"a": 1}))
    assert desired(db).tag_details[0].version == 2


def test_bad_names_are_input_errors(db, fakes):
    with pytest.raises(errors.MetadataError, match="Invalid name"):
        preview(db, op("set_tags", name="a.b.c.d", tags={"x": "1"}))


# --- Cross-environment copies ------------------------------------------------------


def test_copying_to_another_environment_creates_a_pending_counterpart(db, fakes):
    fakes.observe(DEV, securable_type="view")
    result = submit(db, op("set_tags", tags={"tier": "gold"}), environments=["PRD"])

    assert [(i.fqn_key, i.environment, i.copied_from, i.object_exists, i.object_state, i.securable_type)
            for i in (d.item for d in result.preview.items)] == [
        (DEV_KEY, "dev", None, False, "exists", "view"),
        (PRD_KEY, "prd", "dev", False, "pending", "view"),
    ]
    assert result.preview.environments == ["dev", "prd"]
    prd = desired(db, PRD_KEY)
    assert (prd.display_name, prd.object_state, prd.securable_type, prd.environment, prd.tags) == (
        "Sales_prd.Bronze.Orders", "pending", "view", "prd", {"tier": "gold"})
    assert desired(db).object_state == "exists"
    assert sorted(result.change_set.summary["environments"]) == ["dev", "prd"]


def test_a_copy_resolves_against_the_counterparts_own_versions(db, fakes):
    submit(db, op("set_tags", tags={"tier": "gold"}))
    submit(db, op("set_tags", name="Sales_Prd.Bronze.Orders", tags={"tier": "a"}))
    submit(db, op("set_tags", name="Sales_Prd.Bronze.Orders", tags={"tier": "b"}))

    result = submit(db, op("set_tags", tags={"tier": "platinum"}, expected_versions={"tier": 1}), environments=["prd"])

    assert {d.item.fqn_key: d.item.before_version for d in result.preview.items} == {DEV_KEY: 1, PRD_KEY: 2}
    assert desired(db, PRD_KEY).tags == {"tier": "platinum"}


def test_the_objects_own_environment_is_skipped(db, fakes):
    pv = preview(db, op("set_tags", tags={"a": "1"}), environments=["dev"])
    assert [d.item.fqn_key for d in pv.items] == [DEV_KEY]


def test_unknown_environments_are_denied(db, fakes):
    pv = preview(db, op("set_tags", tags={"a": "1"}), environments=["qa"])
    assert pv.blocked and [f.code for f in pv.findings] == ["unknown_environment"]
    assert "dev, prd" in pv.findings[0].message


def test_a_missing_counterpart_skips_that_object_with_a_warning(db, fakes):
    pv = preview(db, op("set_tags", name="main.s.t", tags={"a": "1"}), op("set_tags", tags={"a": "1"}),
                 environments=["prd"])
    assert not pv.blocked
    assert sorted(d.item.fqn_key for d in pv.items) == sorted(["main.s.t", DEV_KEY, PRD_KEY])
    assert [(f.code, f.severity, f.fqn_key) for f in pv.findings] == [("no_counterpart", "warn", "main.s.t")]
    assert "prd is skipped for it" in pv.findings[0].message


def test_missing_counterparts_in_one_catalog_collapse_into_one_warning(db, fakes):
    pv = preview(db, op("set_tags", name="main.s.t", tags={"a": "1"}), op("set_comment", name="main.s.t", comment="x"),
                 op("set_tags", name="main.s.u", tags={"a": "1"}), environments=["prd"])
    assert [(f.code, f.fqn_key) for f in pv.findings] == [("no_counterpart", None)]
    assert pv.findings[0].message.startswith("2 objects in main have no counterpart in prd")


# --- Types, states and warnings ----------------------------------------------------


def test_type_and_state_of_objects_without_a_row(db, fakes):
    fakes.observe("Sales_Dev.Bronze.Live", securable_type="streaming_table")
    pv = preview(
        db,
        op("set_tags", name="Sales_Dev.Bronze", tags={"a": "1"}),
        op("set_tags", name="Sales_Dev.Bronze.Orders", column="Email", tags={"a": "1"}),
        op("set_tags", name="Sales_Dev.Bronze.Mv", tags={"a": "1"}, securable_type="materialized_view"),
        op("set_tags", name="Sales_Dev.Bronze.Live", tags={"a": "1"}, securable_type="table"),
    )
    got = {d.item.fqn_key: (d.item.securable_type, d.item.object_state, d.item.column) for d in pv.items}
    assert got == {
        "sales_dev.bronze": ("schema", "pending", None),
        "sales_dev.bronze.orders::email": ("column", "pending", "Email"),
        "sales_dev.bronze.mv": ("materialized_view", "pending", None),
        "sales_dev.bronze.live": ("streaming_table", "exists", None),
    }


# --- First edits of objects UC has -------------------------------------------------


def test_a_first_edit_keeps_the_tags_and_description_uc_has(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x", "system.certification_status": "c"}, comment="From UC")

    pv = preview(db, op("set_tags", tags={"tier": "gold"}))

    assert {(d.item.field, d.item.key): (d.item.action, d.item.unchanged_in_uc) for d in pv.items} == {
        ("tag", "legacy"): ("set", True), ("tag", "tier"): ("set", False), ("comment", None): ("comment_set", True)}
    assert fakes.seeded == {(DEV_KEY, "tag", "legacy"), (DEV_KEY, "comment", None)}
    assert (pv.direct_count, pv.kept_count, pv.findings) == (3, 2, [])

    result = submit(db, op("set_tags", tags={"tier": "gold"}))

    d = desired(db)
    assert (d.tags, d.comment.value) == ({"legacy": "x", "tier": "gold"}, "From UC")
    assert result.change_set.summary["tags_set"] == 2


def test_the_full_set_the_ui_used_to_send_still_works(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x"}, comment="From UC")

    submit(db, op("set_tags", tags={"legacy": "x", "tier": "gold"}, expected_versions={"legacy": 0, "tier": 0}),
           op("set_comment", comment="From UC", expected_comment_version=0))

    assert fakes.seeded == set()
    assert (desired(db).tags, desired(db).comment.value) == ({"legacy": "x", "tier": "gold"}, "From UC")


def test_restating_what_uc_has_doesnt_start_managing_the_object(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x"}, comment="From UC")

    result = submit(db, op("set_tags", tags={"legacy": "x"}), op("set_comment", comment="From UC"))

    assert result.status == "noop" and desired(db) is None


def test_removing_a_tag_only_uc_has_is_a_real_removal(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x", "keep": "y"})

    pv = preview(db, op("unset_tags", keys=["legacy"]))
    assert [(d.item.key, d.item.action, d.item.before, d.item.before_exists) for d in pv.items] == [
        ("keep", "set", "y", True), ("legacy", "unset", "x", True)]

    result = submit(db, op("unset_tags", keys=["legacy"]))

    assert desired(db).tags == {"keep": "y"}
    removed = next(e for e in result.change_set.events if e.action == "tag_unset")
    assert (removed.key, removed.before) == ("legacy", {"value": "x", "version": 0})


def test_replace_on_an_unmanaged_object_removes_its_uc_tags_and_a_sensitive_one_needs_approval(db, fakes):
    fakes.sensitive = {"pii"}
    fakes.observe(DEV, tags={"legacy": "x", "pii": "email", "system.certification_status": "c"})

    pv = preview(db, op("replace_tags", tags={"tier": "gold"}))

    assert {d.item.key: (d.item.action, d.item.before, d.item.before_exists) for d in pv.items} == {
        "legacy": ("unset", "x", True), "pii": ("unset", "email", True), "tier": ("set", None, False)}
    assert (pv.direct_count, pv.approval_count, pv.kept_count) == (2, 1, 0)

    result = submit(db, op("replace_tags", tags={"tier": "gold"}), on_approval_needed="split")

    assert result.status == "pending_approval" and desired(db) is None
    assert sorted(i.key for i in fakes.filed[0]["items"]) == ["legacy", "pii", "tier"]


def test_a_copy_repeats_only_the_edit_not_the_sources_uc_values(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x"}, comment="Dev description")
    fakes.observe("Sales_Prd.Bronze.Orders", tags={"prd_only": "1", "legacy": "x"}, comment="Prd description")

    submit(db, op("set_tags", tags={"tier": "gold"}), op("unset_tags", keys=["legacy"]), environments=["prd"])

    assert desired(db).tags == {"tier": "gold"}
    prd = desired(db, PRD_KEY)
    assert (prd.tags, prd.comment.value) == ({"prd_only": "1", "tier": "gold"}, "Prd description")


def test_a_copied_replace_removes_only_what_it_removed_on_the_source(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x", "old": "1"})
    fakes.observe("Sales_Prd.Bronze.Orders", tags={"legacy": "x", "prd_only": "1"})

    pv = preview(db, op("replace_tags", tags={"tier": "gold"}), environments=["prd"])

    assert {d.item.key: (d.item.action, d.item.unchanged_in_uc) for d in pv.items if d.item.fqn_key == PRD_KEY} == {
        "legacy": ("unset", False), "prd_only": ("set", True), "tier": ("set", False)}
    assert (PRD_KEY, "tag", "prd_only") in fakes.seeded


def test_values_an_unmanaged_object_already_has_are_flagged_unchanged_in_uc(db, fakes):
    """The first desired row restates the object's UC tags and description; only real edits count as edits."""
    fakes.observe(DEV, tags={"legacy": "x"}, comment="From UC")
    pv = preview(db, op("set_tags", tags={"legacy": "x", "tier": "gold"}), op("set_comment", comment="From UC"))

    kept = {(d.item.field, d.item.key): d.item.unchanged_in_uc for d in pv.items}
    assert kept == {("tag", "legacy"): True, ("tag", "tier"): False, ("comment", None): True}
    assert pv.direct_count == 3 and pv.kept_count == 2

    changed = preview(db, op("set_tags", tags={"legacy": "y"}), op("set_comment", comment="New"))
    assert not any(d.item.unchanged_in_uc for d in changed.items) and changed.kept_count == 0

    a = changes.items_digest([d.item for d in pv.items])
    for d in pv.items:
        d.item.unchanged_in_uc = not d.item.unchanged_in_uc
    assert changes.items_digest([d.item for d in pv.items]) == a


# --- Limits and reasons ------------------------------------------------------------


def test_object_limit_and_a_principals_higher_cap(db, fakes, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_MAX_OBJECTS_PER_CHANGE", 1)
    two = (op("set_tags", name="sales_dev.s.t1", tags={"a": "1"}),
           op("set_tags", name="sales_dev.s.t2", tags={"a": "1"}))

    pv = preview(db, *two)
    assert pv.blocked and [f.code for f in pv.findings] == ["too_many_objects"]

    pipeline = Actor(id="app-1", actor_type="service_principal",
                     principal=PrincipalScope(application_id="app-1", max_objects_per_change=5))
    assert not preview(db, *two, actor=pipeline).blocked


def test_minimum_reason_length_applies_only_to_real_changes(db, fakes, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_MIN_REASON_LENGTH", 10)
    pv = preview(db, op("set_tags", tags={"a": "1"}), reason="short")
    assert pv.blocked and [f.code for f in pv.findings] == ["reason_too_short"]
    assert not preview(db, op("set_tags", tags={"a": "1"}), reason="Tagging the orders table").blocked

    submit(db, op("set_tags", tags={"a": "1"}), reason="Tagging the orders table")
    assert not preview(db, op("set_tags", tags={"a": "1"}), reason="").blocked


# --- Preview ---------------------------------------------------------------------


def test_preview_counts_buckets_and_builds_the_report(db, fakes):
    fakes.sensitive = {"pii"}
    fakes.denied = {"owner"}
    pv = preview(db, op("set_tags", tags={"tier": "gold", "pii": None, "owner": "x"}), reason="Tag it")

    assert (pv.direct_count, pv.approval_count, pv.denied_count) == (1, 1, 1)
    assert pv.needs_approval and pv.approver_groups == ["data_owners"]
    assert (pv.object_count, pv.environments, pv.blocked) == (1, ["dev"], False)
    assert pv.digest == changes.items_digest([d.item for d in pv.items])
    assert fakes.decided[-1] == [d.item for d in pv.items]
    assert "| Object | Change |" in pv.report_markdown and "Tag it" in pv.report_markdown
    assert pv.risk and pv.risk.get("band")


def test_a_preview_warns_about_a_near_miss_of_a_key_already_in_use(db, fakes):
    submit(db, op("set_tags", name="Sales_Dev.Bronze.Customers", tags={"classification": "internal"}))
    submit(db, op("set_tags", name="Sales_Dev.Bronze.Payments", tags={"classification": "internal"}))

    pv = preview(db, op("set_tags", tags={"clasification": "internal"}))

    assert not pv.blocked and pv.direct_count == 1
    typos = [f for f in pv.findings if f.code == "near_miss_key"]
    assert len(typos) == 1 and typos[0].severity == "warn" and "classification" in typos[0].message
    assert "classification" in pv.report_markdown

    assert not [f for f in preview(db, op("set_tags", tags={"classification": "internal"})).findings
                if f.code == "near_miss_key"]


def test_the_typo_check_can_be_turned_off(db, fakes, monkeypatch):
    submit(db, op("set_tags", name="Sales_Dev.Bronze.Customers", tags={"classification": "internal"}))
    monkeypatch.setattr(settings, "METADATA_LINT_CHECK", False)

    assert not [f for f in preview(db, op("set_tags", tags={"clasification": "internal"})).findings
                if f.code == "near_miss_key"]


def test_a_deny_finding_from_authz_blocks_the_change(db, fakes):
    fakes.flagged["tier"] = Finding(code="value_not_allowed", severity="deny", message="tier must be gold or silver.")
    pv = preview(db, op("set_tags", tags={"tier": "bronze"}))
    assert pv.blocked and pv.denied_count == 1
    with pytest.raises(errors.ChangeBlocked, match="gold or silver"):
        submit(db, op("set_tags", tags={"tier": "bronze"}))


def test_a_preview_with_nothing_to_change_skips_authz(db, fakes):
    submit(db, op("set_tags", tags={"a": "1"}))
    fakes.decided.clear()
    pv = preview(db, op("set_tags", tags={"a": "1"}))
    assert fakes.decided == [] and pv.digest == changes.items_digest([]) and pv.risk is None


# --- Submit and routing ----------------------------------------------------------


def test_submit_commits_direct_changes(db, fakes):
    result = submit(db, op("set_tags", tags={"tier": "gold"}), op("set_comment", comment="Orders."),
                    reason="Initial", source="mcp")

    assert result.status == "committed" and not result.replayed
    assert (result.change_set.source, result.change_set.reason, result.change_set.actor) == ("mcp", "Initial", JANE.id)
    assert result.message.startswith("Saved 2 changes on 1 object")
    d = desired(db)
    assert d.tags == {"tier": "gold"} and d.comment.value == "Orders."
    assert (d.tag_details[0].set_by, d.tag_details[0].source) == (JANE.id, "mcp")
    assert fakes.filed == []


def test_submitting_nothing_new_is_a_noop(db, fakes):
    submit(db, op("set_tags", tags={"a": "1"}))
    before = change_set_count(db)
    result = submit(db, op("set_tags", tags={"a": "1"}))
    assert (result.status, result.change_set) == ("noop", None)
    assert change_set_count(db) == before


def test_request_routing_files_the_whole_change_and_writes_nothing(db, fakes):
    fakes.sensitive = {"pii"}
    result = submit(db, op("set_tags", tags={"tier": "gold", "pii": None}), source="ui")

    assert (result.status, result.request_id, result.change_set) == ("pending_approval", "req-1", None)
    assert sorted(i.key for i in fakes.filed[0]["items"]) == ["pii", "tier"]
    assert (fakes.filed[0]["source"], fakes.filed[0]["actor"]) == ("ui", JANE)
    assert desired(db) is None and change_set_count(db) == 0


def test_split_routing_writes_direct_items_and_files_the_rest(db, fakes):
    fakes.sensitive = {"pii"}
    result = submit(db, op("set_tags", tags={"tier": "gold", "pii": None}), on_approval_needed="split",
                    idempotency_key="k1")

    assert (result.status, result.request_id) == ("partial", "req-1")
    assert "req-1" in result.message
    assert desired(db).tags == {"tier": "gold"}
    assert [i.key for i in fakes.filed[0]["items"]] == ["pii"]
    assert result.change_set.request_id is None

    again = submit(db, op("set_tags", tags={"tier": "gold", "pii": None}), on_approval_needed="split",
                   idempotency_key="k1")
    assert (again.replayed, again.status, again.request_id) == (True, "partial", "req-1")
    assert again.change_set.id == result.change_set.id
    assert len(fakes.filed) == 1


def test_split_files_an_objects_kept_values_with_its_edits_that_need_approval(db, fakes):
    fakes.sensitive = {"pii"}
    fakes.observe(DEV, tags={"legacy": "x"})
    fakes.observe("sales_dev.bronze.events", tags={"legacy": "y"})

    result = submit(db, op("set_tags", tags={"legacy": "x", "pii": None}),
                    op("set_tags", name="sales_dev.bronze.events", tags={"legacy": "y", "tier": "gold"}),
                    on_approval_needed="split")

    assert result.status == "partial"
    assert desired(db) is None
    assert desired(db, "sales_dev.bronze.events").tags == {"legacy": "y", "tier": "gold"}
    assert sorted(i.key for i in fakes.filed[0]["items"]) == ["legacy", "pii"]


def test_split_with_nothing_direct_is_just_a_request(db, fakes):
    fakes.sensitive = {"pii"}
    result = submit(db, op("set_tags", tags={"pii": None}), on_approval_needed="split")
    assert (result.status, result.change_set) == ("pending_approval", None)
    assert [i.key for i in fakes.filed[0]["items"]] == ["pii"]


def test_reject_routing_refuses_with_the_reasons(db, fakes):
    fakes.sensitive = {"pii"}
    with pytest.raises(errors.Forbidden, match="pii is sensitive"):
        submit(db, op("set_tags", tags={"tier": "gold", "pii": None}), on_approval_needed="reject")
    assert fakes.filed == [] and desired(db) is None


def test_reject_routing_still_commits_a_change_that_needs_no_approval(db, fakes):
    result = submit(db, op("set_tags", tags={"tier": "gold"}), on_approval_needed="reject")
    assert result.status == "committed"


def test_denied_items_are_forbidden_whatever_the_routing(db, fakes):
    fakes.denied = {"owner"}
    for routing in ("request", "split", "reject"):
        with pytest.raises(errors.Forbidden, match="can't edit owner"):
            submit(db, op("set_tags", tags={"tier": "gold", "owner": "x"}), on_approval_needed=routing)
    assert fakes.filed == [] and desired(db) is None


def test_a_repeated_idempotency_key_replays_the_committed_result(db, fakes):
    first = submit(db, op("set_tags", tags={"tier": "gold"}), idempotency_key="k1")
    again = submit(db, op("set_tags", tags={"tier": "silver"}), idempotency_key=" k1 ")

    assert (again.replayed, again.status, again.change_set.id) == (True, "committed", first.change_set.id)
    assert again.preview.digest == first.preview.digest
    assert desired(db).tags == {"tier": "gold"} and change_set_count(db) == 1

    other = submit(db, op("set_tags", tags={"tier": "silver"}), idempotency_key="k1", actor=BOB)
    assert not other.replayed and desired(db).tags == {"tier": "silver"}


def test_a_repeated_idempotency_key_replays_a_filed_request(db, fakes):
    fakes.sensitive = {"pii"}
    first = submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k2")
    again = submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k2")
    assert (again.replayed, again.status, again.request_id, again.change_set) == (
        True, "pending_approval", first.request_id, None)
    assert len(fakes.filed) == 1 and desired(db) is None


def test_a_submit_that_loses_the_idempotency_race_files_nothing_and_replays(db, fakes, monkeypatch):
    fakes.sensitive = {"pii"}
    first = submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k3")
    real_replay = changes._replay
    calls = []

    def replay_misses_once(db_, actor, key):
        calls.append(key)
        return None if len(calls) == 1 else real_replay(db_, actor, key)

    monkeypatch.setattr(changes, "_replay", replay_misses_once)
    again = submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k3")

    assert (again.replayed, again.request_id) == (True, first.request_id)
    assert len(fakes.filed) == 1 and change_set_count(db) == 1


def test_a_failed_filing_releases_the_idempotency_key(db, fakes, monkeypatch):
    fakes.sensitive = {"pii"}

    def boom(*args, **kwargs):
        raise errors.MetadataError("filing failed")

    monkeypatch.setattr(requests, "file_change_request", boom)
    with pytest.raises(errors.MetadataError, match="filing failed"):
        submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k4")
    monkeypatch.setattr(requests, "file_change_request", fakes.file)

    result = submit(db, op("set_tags", tags={"pii": None}), idempotency_key="k4")

    assert (result.replayed, result.status) == (False, "pending_approval")
    assert store.find_idempotent(db, JANE.id, "k4")["request_id"] == result.request_id


# --- Promote ---------------------------------------------------------------------


def _seed_dev_with_column(db):
    submit(db, op("set_tags", tags={"tier": "gold"}), op("set_comment", comment="Orders."),
           op("set_tags", column="Email", tags={"pii": "email"}))


def test_promote_copies_tags_description_and_columns(db, fakes):
    _seed_dev_with_column(db)
    submit(db, op("set_tags", name="Sales_Prd.Bronze.Orders", tags={"stale": "x"}))
    request = PromoteRequest(name=DEV, to_environments=["prd", "dev"], reason="Ship it")

    pv = changes.preview_promote(db, request, JANE)
    assert {(d.item.fqn_key, d.item.key, d.item.action) for d in pv.items} == {
        (PRD_KEY, "stale", "unset"), (PRD_KEY, "tier", "set"), (PRD_KEY, None, "comment_set"),
        (f"{PRD_KEY}::email", "pii", "set"),
    }
    assert all(d.item.copied_from == "dev" for d in pv.items)
    column = next(d.item for d in pv.items if d.item.column)
    assert (column.securable_type, column.object_state, column.column) == ("column", "pending", "Email")
    assert pv.noop_count == 1
    assert "Promote metadata" in pv.report_markdown

    result = changes.promote(db, request, JANE, source="ui")
    assert result.status == "committed" and result.change_set.reason == "Ship it"
    assert desired(db, PRD_KEY).tags == {"tier": "gold"}
    assert desired(db, PRD_KEY).comment.value == "Orders."
    assert desired(db, f"{PRD_KEY}::email").tags == {"pii": "email"}

    again = changes.preview_promote(db, request, JANE)
    assert again.items == [] and again.noop_count == 4


def test_promote_can_leave_out_columns_tags_or_the_description(db, fakes):
    _seed_dev_with_column(db)
    pv = changes.preview_promote(db, PromoteRequest(name=DEV, to_environments=["prd"], include_columns=False,
                                                    include_comment=False), JANE)
    assert [(d.item.fqn_key, d.item.key) for d in pv.items] == [(PRD_KEY, "tier")]
    pv = changes.preview_promote(db, PromoteRequest(name=DEV, to_environments=["prd"], include_tags=False), JANE)
    assert [(d.item.fqn_key, d.item.field) for d in pv.items] == [(PRD_KEY, "comment")]


def test_promote_errors(db, fakes):
    with pytest.raises(errors.NotFound):
        changes.preview_promote(db, PromoteRequest(name=DEV, to_environments=["prd"]), JANE)
    submit(db, op("set_tags", name="main.s.t", tags={"a": "1"}))
    pv = changes.preview_promote(db, PromoteRequest(name="main.s.t", to_environments=["prd"]), JANE)
    assert pv.blocked and [f.code for f in pv.findings] == ["no_counterpart"]


def test_promote_needing_approval_files_the_counterpart_ops(db, fakes):
    _seed_dev_with_column(db)
    fakes.sensitive = {"pii"}
    result = changes.promote(db, PromoteRequest(name=DEV, to_environments=["prd"], idempotency_key="p1"), JANE,
                             source="api")
    assert result.status == "pending_approval"
    filed = fakes.filed[0]
    assert {o.name for o in filed["request"].ops} == {"Sales_prd.Bronze.Orders"}
    assert {i.fqn_key for i in filed["items"]} == {PRD_KEY, f"{PRD_KEY}::email"}
    assert changes.promote(db, PromoteRequest(name=DEV, to_environments=["prd"], idempotency_key="p1"), JANE,
                           source="api").replayed


# --- Revert ----------------------------------------------------------------------


def test_revert_restores_previous_values_and_marks_the_change_set(db, fakes):
    submit(db, op("set_tags", tags={"tier": "gold"}), op("set_comment", comment="Old."))
    change = submit(db, op("set_tags", tags={"tier": "silver", "new": "1"}), op("set_comment", comment="New."))
    cs_id = change.change_set.id

    pv = changes.preview_revert(db, cs_id, RevertRequest(), JANE)
    assert sorted((d.item.key or "", d.item.action) for d in pv.items) == [
        ("", "comment_set"), ("new", "unset"), ("tier", "set")]
    assert f"Revert of change set {cs_id}" in pv.report_markdown

    result = changes.revert(db, cs_id, RevertRequest(), JANE, source="ui")
    assert result.status == "committed"
    cs = result.change_set
    assert (cs.source, cs.reverts_change_set_id, cs.reason) == ("revert", cs_id, f"Revert change set {cs_id}")
    assert desired(db).tags == {"tier": "gold"} and desired(db).comment.value == "Old."
    assert store.get_change_set(db, cs_id).reverted_by == cs.id

    with pytest.raises(errors.MetadataError, match="already reverted"):
        changes.revert(db, cs_id, RevertRequest(), JANE, source="ui")
    with pytest.raises(errors.NotFound):
        changes.preview_revert(db, "nope", RevertRequest(), JANE)


def test_a_revert_needing_approval_is_filed_whole_even_when_split(db, fakes):
    submit(db, op("set_comment", comment="Orders."))
    change = submit(db, op("set_tags", tags={"tier": "gold", "pii": "x"}))
    fakes.sensitive = {"pii"}

    result = changes.revert(db, change.change_set.id, RevertRequest(on_approval_needed="split", reason="Undo"), JANE,
                            source="ui")

    assert result.status == "pending_approval"
    filed = fakes.filed[0]
    assert sorted(i.key for i in filed["items"]) == ["pii", "tier"]
    assert filed["source"] == "revert"
    assert filed["reverts"] == change.change_set.id
    assert [(o.op, o.keys, o.expected_versions) for o in filed["request"].ops] == [
        ("unset_tags", ["pii"], {"pii": 1}), ("unset_tags", ["tier"], {"tier": 1})]
    assert desired(db).tags == {"pii": "x", "tier": "gold"}
    assert store.get_change_set(db, change.change_set.id).reverted_by is None


def test_undoing_start_managing_stops_managing_the_object(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x"}, comment="Orders.")
    adopted = changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN).change_set

    pv = changes.preview_revert(db, adopted.id, RevertRequest(), JANE)
    assert [f.code for f in pv.findings] == ["stops_managing"]
    assert all(d.item.stops_managing for d in pv.items) and not pv.blocked

    result = changes.revert(db, adopted.id, RevertRequest(), JANE, source="ui")

    assert result.status == "committed" and result.change_set.summary["unmanaged"] == 1
    assert desired(db) is None
    row = db.query(MetadataObjectModel).filter_by(fqn_key=DEV_KEY).one()
    assert row.status == "deleted"
    dropped = next(e for e in result.change_set.events if e.action == "object_delete")
    assert dropped.before["tags"] == {"legacy": "x"} and dropped.before["comment"] == "Orders."
    with pytest.raises(errors.MetadataError, match="Start managing"):
        changes.preview_revert(db, result.change_set.id, RevertRequest(), JANE)


def test_undoing_a_first_edit_warns_about_changes_made_since(db, fakes):
    fakes.observe(DEV, tags={"legacy": "x"})
    first = submit(db, op("set_tags", tags={"tier": "gold"}))
    submit(db, op("set_tags", tags={"owner": "team_a"}))

    pv = changes.preview_revert(db, first.change_set.id, RevertRequest(), JANE)

    assert [(f.code, f.key) for f in pv.findings] == [("stops_managing", None), ("changed_since", "owner")]
    changes.revert(db, first.change_set.id, RevertRequest(), JANE, source="ui")
    assert desired(db) is None


def test_a_revert_goes_through_the_object_limit_and_managed_catalog_checks(db, fakes, monkeypatch):
    change = submit(db, op("set_tags", tags={"tier": "gold"}),
                    op("set_tags", name="sales_dev.bronze.events", tags={"tier": "gold"}))

    monkeypatch.setattr(settings, "METADATA_MAX_OBJECTS_PER_CHANGE", 1)
    pv = changes.preview_revert(db, change.change_set.id, RevertRequest(), JANE)
    assert pv.blocked and "too_many_objects" in [f.code for f in pv.findings]

    monkeypatch.setattr(settings, "METADATA_MAX_OBJECTS_PER_CHANGE", 500)
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["other_dev"])
    pv = changes.preview_revert(db, change.change_set.id, RevertRequest(), JANE)
    assert pv.blocked and "outside_managed_catalogs" in [f.code for f in pv.findings]
    with pytest.raises(errors.ChangeBlocked):
        changes.revert(db, change.change_set.id, RevertRequest(), JANE, source="ui")
    assert desired(db).tags == {"tier": "gold"}


# --- Adopt -----------------------------------------------------------------------


def test_adopt_is_for_admins_and_needs_a_target(db, fakes):
    with pytest.raises(errors.Forbidden):
        changes.adopt(db, AdoptRequest(catalogs=["sales_dev"]), JANE)
    with pytest.raises(errors.MetadataError, match="at least one"):
        changes.adopt(db, AdoptRequest(catalogs=[" "]), ADMIN)


def test_adopting_a_catalog_makes_its_observed_state_desired(db, fakes):
    fakes.observe("Sales_Dev")
    fakes.observe("Sales_Dev.Bronze", comment="Bronze layer")
    fakes.observe(DEV, tags={"tier": "gold", "system.certification_status": "certified"}, comment="Orders.")
    fakes.observe(DEV, "Email", tags={"pii": ""})
    fakes.observe("Sales_Dev.Bronze.Dropped", tags={"x": "1"}, present=False)
    fakes.observe("Other_Dev.S.T", tags={"x": "1"})
    submit(db, op("set_tags", name="Sales_Dev.Bronze", tags={"owner": "team_a"}))

    result = changes.adopt(db, AdoptRequest(catalogs=["SALES_DEV"]), ADMIN)

    assert result.status == "committed"
    assert result.message == "Adopted 3 objects with 3 tags or descriptions."
    assert result.preview.noop_count == 1
    cs = result.change_set
    assert (cs.actor, cs.actor_type, cs.source) == ("system", "system", "adopt")
    assert cs.reason == "Adopt current Unity Catalog state (requested by admin@example.com)"

    catalog = desired(db, "sales_dev")
    assert (catalog.securable_type, catalog.object_state, catalog.tags) == ("catalog", "exists", {})
    table = desired(db)
    assert (table.tags, table.comment.value, table.object_state) == ({"tier": "gold"}, "Orders.", "exists")
    assert (table.tag_details[0].set_by, table.tag_details[0].set_by_type, table.tag_details[0].source) == (
        "system", "system", "adopt")
    assert desired(db, f"{DEV_KEY}::email").tags == {"pii": None}
    schema = desired(db, "sales_dev.bronze")
    assert (schema.tags, schema.comment.value) == ({"owner": "team_a"}, "Bronze layer")
    assert desired(db, "sales_dev.bronze.dropped") is None and desired(db, "other_dev.s.t") is None


def test_adopt_with_overwrite_replaces_desired_state_but_keeps_exempt_keys(db, fakes):
    fakes.observe("Sales_Dev.Bronze", tags={"layer": "bronze"}, comment="Bronze layer")
    submit(db, op("set_tags", name="Sales_Dev.Bronze", tags={"owner": "team_a"}))
    seed_exempt(db, "metadata_manager_instance", "prod-app", name="Sales_Dev.Bronze")

    changes.adopt(db, AdoptRequest(names=["Sales_Dev.Bronze"], overwrite=True, reason="Re-sync"), ADMIN)

    schema = desired(db, "sales_dev.bronze")
    assert schema.tags == {"layer": "bronze", "metadata_manager_instance": "prod-app"}
    assert schema.comment.value == "Bronze layer"


def test_adopt_by_name_includes_columns_unless_asked_not_to(db, fakes):
    fakes.observe(DEV, tags={"tier": "gold"})
    fakes.observe(DEV, "Email", tags={"pii": None})
    changes.adopt(db, AdoptRequest(names=[DEV], include_columns=False), ADMIN)
    assert desired(db, f"{DEV_KEY}::email") is None
    changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN)
    assert desired(db, f"{DEV_KEY}::email").tags == {"pii": None}


def test_adopting_a_schema_by_name_includes_everything_under_it(db, fakes):
    fakes.observe("Sales_Dev.Bronze", comment="Bronze layer")
    fakes.observe(DEV, tags={"tier": "gold"})
    fakes.observe(DEV, "Email", tags={"pii": None})
    fakes.observe("Sales_Dev.Bronzer.T", tags={"x": "1"})
    fakes.observe("Sales_Dev.Silver.T", tags={"x": "1"})

    changes.adopt(db, AdoptRequest(names=["sales_dev.bronze"]), ADMIN)

    assert desired(db, "sales_dev.bronze").comment.value == "Bronze layer"
    assert desired(db).tags == {"tier": "gold"}
    assert desired(db, f"{DEV_KEY}::email").tags == {"pii": None}
    assert desired(db, "sales_dev.bronzer.t") is None and desired(db, "sales_dev.silver.t") is None


def test_an_edit_outside_the_managed_catalogs_is_refused(db, fakes, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["*_dev"])

    pv = preview(db, op("set_tags", tags={"a": "1"}), op("set_tags", name="Sales_Prd.Bronze.Orders", tags={"a": "1"}))

    assert pv.blocked
    assert [(f.code, f.severity) for f in pv.findings] == [("outside_managed_catalogs", "deny")]
    assert "sales_prd" in pv.findings[0].message and "*_dev" in pv.findings[0].message
    with pytest.raises(errors.ChangeBlocked):
        submit(db, op("set_tags", name="Sales_Prd.Bronze.Orders", tags={"a": "1"}))
    assert desired(db, PRD_KEY) is None
    assert not preview(db, op("set_tags", tags={"a": "1"})).blocked


def test_adopting_outside_the_managed_catalogs_is_refused(db, fakes, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["sales_dev"])
    fakes.observe("Sales_Prd.Bronze.Orders", tags={"tier": "gold"})

    with pytest.raises(errors.MetadataError, match="manages only catalogs matching sales_dev"):
        changes.adopt(db, AdoptRequest(names=["sales_prd.bronze"]), ADMIN)
    assert change_set_count(db) == 0


def test_adopting_a_catalog_the_last_check_couldnt_read_is_refused(db, fakes, monkeypatch):
    fakes.observe(DEV, tags={"tier": "gold"})
    monkeypatch.setattr(observed, "unread_catalogs", lambda db_: ["sales_dev"])

    for request in (AdoptRequest(catalogs=["Sales_Dev"]), AdoptRequest(names=[DEV])):
        with pytest.raises(errors.MetadataError, match="couldn't read sales_dev"):
            changes.adopt(db, request, ADMIN)
    assert change_set_count(db) == 0

    monkeypatch.setattr(observed, "unread_catalogs", lambda db_: [])
    assert changes.adopt(db, AdoptRequest(catalogs=["sales_dev"]), ADMIN).status == "committed"


def test_large_adopts_commit_in_batches(db, fakes, monkeypatch):
    monkeypatch.setattr(changes, "ADOPT_BATCH_SIZE", 2)
    for name in ("Sales_Dev.S.A", "Sales_Dev.S.B", "Sales_Dev.S.C"):
        fakes.observe(name, tags={"tier": "gold"})
    result = changes.adopt(db, AdoptRequest(catalogs=["sales_dev"]), ADMIN)
    assert result.message == "Adopted 3 objects with 3 tags or descriptions in 2 change sets."
    assert change_set_count(db) == 2


def test_adopting_what_is_already_managed_is_a_noop(db, fakes):
    fakes.observe(DEV, tags={"tier": "gold"})
    changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN)
    result = changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN)
    assert (result.status, result.preview.noop_count) == ("noop", 1)


def test_adopt_observed_is_the_new_object_policy(db, fakes):
    fakes.observe(DEV, tags={"tier": "gold"})
    fakes.observe(DEV, "Email", tags={"pii": None})

    cs = changes.adopt_observed(db, [DEV_KEY, ""], reason="New object seen")
    assert (cs.actor, cs.source, cs.reason) == ("system", "adopt", "New object seen")
    assert desired(db, f"{DEV_KEY}::email").tags == {"pii": None}
    assert changes.adopt_observed(db, [DEV_KEY], reason="again") is None
    assert changes.adopt_observed(db, ["never.seen.object"], reason="x") is None


# --- Import ----------------------------------------------------------------------


def test_import_is_for_admins(db, fakes):
    request = ImportRequest(rows=[ImportRow(name=DEV, tags={"a": "1"})])
    with pytest.raises(errors.Forbidden):
        changes.preview_import(db, request, JANE)
    with pytest.raises(errors.Forbidden):
        changes.import_rows(db, request, JANE)


def test_csv_import_merges_tags_and_a_blank_comment_leaves_it(db, fakes):
    submit(db, op("set_tags", tags={"keep": "1"}), op("set_comment", comment="Old."),
           op("set_comment", column="Email", comment="Email address"))
    text = (
        "Name,Column,Comment,Tags\n"
        'Sales_Dev.Bronze.Orders,,"New, improved.","{""tier"": ""gold""}"\n'
        'Sales_Dev.Bronze.Orders,Email,,"{""pii"": null}"\n'
        "\n"
    )

    result = changes.import_rows(db, ImportRequest(csv=text, reason="Bulk load"), ADMIN)

    assert result.status == "committed"
    assert (result.change_set.source, result.change_set.actor) == ("import", ADMIN.id)
    assert desired(db).tags == {"keep": "1", "tier": "gold"}
    assert desired(db).comment.value == "New, improved."
    email = desired(db, f"{DEV_KEY}::email")
    assert (email.tags, email.comment.value) == ({"pii": None}, "Email address")


def test_replace_import_and_an_empty_json_comment_clears_it(db, fakes):
    submit(db, op("set_tags", tags={"keep": "1"}), op("set_comment", comment="Old."))
    pv = changes.preview_import(db, ImportRequest(rows=[ImportRow(name=DEV, tags={"tier": "gold"}, comment="")],
                                                  mode="replace"), ADMIN)
    assert summary(pv) == [(DEV_KEY, "keep", "unset", None), (DEV_KEY, "tier", "set", "gold"),
                           (DEV_KEY, None, "comment_clear", None)]
    assert "Import metadata" in pv.report_markdown


def test_one_sensitive_row_sends_the_whole_import_for_approval(db, fakes):
    fakes.sensitive = {"pii"}
    rows = [ImportRow(name=DEV, tags={"tier": "gold"}), ImportRow(name=DEV, column="Email", tags={"pii": None})]
    result = changes.import_rows(db, ImportRequest(rows=rows), ADMIN)
    assert result.status == "pending_approval"
    assert sorted(i.key for i in fakes.filed[0]["items"]) == ["pii", "tier"]
    assert fakes.filed[0]["source"] == "import"
    assert desired(db) is None


@pytest.mark.parametrize("text, message", [
    ("", "Nothing to import"),
    ("column,tags\nx,{}\n", "needs a name column"),
    ("name,owner\nx,y\n", "Line 1: unknown column owner"),
    ("name,tags\nSales_Dev.S.T,not json\n", "Line 2: tags must be a JSON object"),
    ("name,tags\nSales_Dev.S.T,[1]\n", "Line 2: tags must be a JSON object"),
    ('name,tags\nSales_Dev.S.T,"{""a"": 1}"\n', r"Line 2: tag values must be text or null \(a\)"),
    ("name,comment\n,hello\n", "Line 2: name is required"),
    ("name,comment\nSales_Dev.S.T,a,b\n", "Line 2: more cells"),
    ("name\na.b.c.d\n", "Line 2: Invalid name"),
    ("name,comment\n\nSales_Dev.S.T,ok\nbad name,x\n", "Line 4: 'bad name' contains"),
])
def test_csv_import_errors_name_the_line(db, fakes, text, message):
    with pytest.raises(errors.MetadataError, match=message):
        changes.preview_import(db, ImportRequest(csv=text), ADMIN)


def test_json_import_errors_name_the_row(db, fakes):
    rows = [ImportRow(name=DEV, tags={"a": "1"}), ImportRow(name="a.b.c.d", tags={"a": "1"})]
    with pytest.raises(errors.MetadataError, match="Row 2: Invalid name"):
        changes.preview_import(db, ImportRequest(rows=rows), ADMIN)


# --- Recording an approved request -----------------------------------------------


def test_record_prepared_writes_the_approved_items_once(db, fakes):
    fakes.sensitive = {"pii"}
    filed = submit(db, op("set_tags", tags={"pii": None}), reason="Mark PII", source="mcp")
    prepared = {"items": [d.model_dump(mode="json") for d in filed.preview.items], "reason": "Mark PII",
                "source": "mcp"}

    cs = changes.record_prepared(db, request_id=filed.request_id, prepared=prepared, requester=JANE,
                                 approver="bob@example.com")

    assert (cs.request_id, cs.source, cs.actor) == ("req-1", "mcp", JANE.id)
    assert cs.reason == "Mark PII (approved by bob@example.com)"
    assert desired(db).tags == {"pii": None}
    assert changes.record_prepared(db, request_id="req-1", prepared=prepared, requester=JANE,
                                   approver="bob@example.com").id == cs.id
    assert change_set_count(db) == 1


def test_a_concurrent_second_record_returns_the_first(db, fakes, monkeypatch):
    fakes.sensitive = {"pii"}
    filed = submit(db, op("set_tags", tags={"pii": None}), reason="Mark PII")
    prepared = {"items": [d.model_dump(mode="json") for d in filed.preview.items]}
    cs = changes.record_prepared(db, request_id=filed.request_id, prepared=prepared, requester=JANE, approver=None)
    real_find = store.find_recorded
    calls = []

    def find_misses_once(db_, request_id):
        calls.append(request_id)
        return None if len(calls) == 1 else real_find(db_, request_id)

    monkeypatch.setattr(store, "find_recorded", find_misses_once)

    again = changes.record_prepared(db, request_id=filed.request_id, prepared=prepared, requester=JANE,
                                    approver=None)

    assert again.id == cs.id and change_set_count(db) == 1


def test_an_approved_revert_marks_the_original_reverted(db, fakes):
    change = submit(db, op("set_tags", tags={"pii": "x"}))
    fakes.sensitive = {"pii"}
    filed = changes.revert(db, change.change_set.id, RevertRequest(reason="Undo"), JANE, source="ui")
    prepared = {"items": [d.model_dump(mode="json") for d in filed.preview.items],
                "reverts_change_set_id": fakes.filed[0]["reverts"]}

    cs = changes.record_prepared(db, request_id=filed.request_id, prepared=prepared, requester=JANE, approver=None)

    assert store.get_change_set(db, change.change_set.id).reverted_by == cs.id


def test_record_prepared_refuses_drift_and_bad_payloads(db, fakes):
    items = [d.item.model_dump(mode="json") for d in preview(db, op("set_tags", tags={"tier": "gold"})).items]
    submit(db, op("set_tags", tags={"tier": "silver"}), actor=BOB)

    with pytest.raises(errors.VersionConflict):
        changes.record_prepared(db, request_id="req-1", prepared={"items": items}, requester=JANE, approver=None)
    with pytest.raises(errors.MetadataError, match="no prepared changes"):
        changes.record_prepared(db, request_id="req-2", prepared={"items": []}, requester=JANE, approver=None)
    with pytest.raises(errors.MetadataError, match="invalid field"):
        changes.record_prepared(db, request_id="req-3", prepared={"items": [{"bogus": 1}]}, requester=JANE,
                                approver=None)
    assert desired(db).tags == {"tier": "silver"}


def test_record_prepared_defaults_and_reverts(db, fakes):
    submit(db, op("set_comment", comment="Orders."))
    change = submit(db, op("set_tags", tags={"tier": "gold"}))
    items = [i.model_dump(mode="json") for i in store.revert_items(db, change.change_set.id)]

    cs = changes.record_prepared(db, request_id="req-9", requester=JANE, approver=None, prepared={
        "items": items, "source": "nonsense", "reverts_change_set_id": change.change_set.id})

    assert (cs.source, cs.reason, cs.reverts_change_set_id) == ("agent", "Approved", change.change_set.id)
    assert desired(db).tags == {}
    assert store.get_change_set(db, change.change_set.id).reverted_by == cs.id


def test_record_prepared_of_an_approved_undo_of_a_first_edit_stops_managing(db, fakes):
    change = submit(db, op("set_tags", tags={"tier": "gold"}))
    items = [i.model_dump(mode="json") for i in store.revert_items(db, change.change_set.id)]

    with pytest.raises(errors.MetadataError, match="isn't a revert"):
        changes.record_prepared(db, request_id="req-8", requester=JANE, approver=None, prepared={"items": items})
    cs = changes.record_prepared(db, request_id="req-9", requester=JANE, approver=None, prepared={
        "items": items, "reverts_change_set_id": change.change_set.id})

    assert desired(db) is None and cs.summary["unmanaged"] == 1


# --- Starting from a recent read of UC ----------------------------------------------


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def fresh_reads(monkeypatch):
    """Turns the freshness checks on; returns the catalog -> last whole read map they see."""
    monkeypatch.setattr(settings, "METADATA_ADOPT_MAX_READ_AGE_MINUTES", 30)
    monkeypatch.setattr(reconciler, "current_mode", lambda: "plan")
    reads = {}
    monkeypatch.setattr(observed, "_catalog_reads", lambda db: dict(reads))
    return reads


def test_a_first_edit_re_reads_the_object_before_starting_from_it(db, fakes, fresh_reads):
    fakes.observe(DEV, tags={"legacy": "x"})

    preview(db, op("set_tags", tags={"tier": "gold"}))
    assert fakes.refreshed == []

    submit(db, op("set_tags", tags={"tier": "gold"}))
    assert fakes.refreshed == [[DEV_KEY]]
    assert desired(db).tags == {"legacy": "x", "tier": "gold"}

    submit(db, op("set_tags", tags={"tier": "silver"}))
    assert len(fakes.refreshed) == 1


@pytest.mark.parametrize("failure", ["raises", "unread"])
def test_a_failed_re_read_refuses_the_first_edit(db, fakes, fresh_reads, failure):
    fakes.observe(DEV, tags={"legacy": "x"})
    if failure == "raises":
        fakes.refresh_fails = True
    else:
        fakes.unreadable = {DEV_KEY}

    with pytest.raises(errors.MetadataError, match="couldn't read Sales_Dev.Bronze.Orders from Unity Catalog"):
        submit(db, op("set_tags", tags={"tier": "gold"}))

    assert desired(db) is None and change_set_count(db) == 0 and fakes.filed == []


def test_the_checks_are_off_at_zero_minutes_or_when_syncing_is_off(db, fakes, fresh_reads, monkeypatch):
    fakes.observe(DEV, tags={"legacy": "x"})
    fakes.refresh_fails = True
    monkeypatch.setattr(reconciler, "current_mode", lambda: "off")
    submit(db, op("set_tags", tags={"tier": "gold"}))

    monkeypatch.setattr(reconciler, "current_mode", lambda: "plan")
    monkeypatch.setattr(settings, "METADATA_ADOPT_MAX_READ_AGE_MINUTES", 0)
    fakes.observe("Sales_Dev.Bronze.Events")
    assert changes.adopt(db, AdoptRequest(names=["Sales_Dev.Bronze.Events"]), ADMIN).status == "committed"
    assert fakes.refreshed == []


def test_a_large_first_edit_needs_a_recent_whole_read_instead(db, fakes, fresh_reads, monkeypatch):
    monkeypatch.setattr(changes, "FRESH_READ_LIMIT", 1)
    fakes.observe(DEV, tags={"legacy": "x"})
    fakes.observe("Sales_Dev.Bronze.Events", tags={"legacy": "y"})
    both = (op("set_tags", tags={"tier": "gold"}), op("set_tags", name="Sales_Dev.Bronze.Events", tags={"tier": "gold"}))

    fresh_reads["sales_dev"] = _now() - timedelta(hours=2)
    with pytest.raises(errors.MetadataError, match="Run Check now under Sync history"):
        submit(db, *both)
    assert desired(db) is None

    fresh_reads["sales_dev"] = _now()
    submit(db, *both)
    assert fakes.refreshed == [] and desired(db).tags == {"legacy": "x", "tier": "gold"}


def test_start_managing_needs_a_recent_whole_read_of_the_catalog(db, fakes, fresh_reads):
    fakes.observe(DEV, tags={"legacy": "x"})

    with pytest.raises(errors.MetadataError, match="sales_dev is more than 30 minutes old"):
        changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN)
    fresh_reads["sales_dev"] = _now() - timedelta(minutes=31)
    with pytest.raises(errors.MetadataError, match="Check now"):
        changes.adopt(db, AdoptRequest(catalogs=["Sales_Dev"]), ADMIN)
    assert desired(db) is None

    fresh_reads["sales_dev"] = _now() - timedelta(minutes=5)
    assert changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN).status == "committed"
    fresh_reads["sales_dev"] = _now() - timedelta(hours=2)
    assert changes.adopt(db, AdoptRequest(names=[DEV]), ADMIN).status == "noop"


def test_an_import_that_starts_managing_needs_a_recent_whole_read(db, fakes, fresh_reads):
    fakes.observe(DEV, tags={"legacy": "x"})
    rows = [ImportRow(name=DEV, tags={"tier": "gold"})]

    pv = changes.preview_import(db, ImportRequest(rows=rows), ADMIN)
    assert pv.blocked and [f.code for f in pv.findings] == ["stale_snapshot"]
    with pytest.raises(errors.ChangeBlocked):
        changes.import_rows(db, ImportRequest(rows=rows), ADMIN)

    submit(db, op("set_comment", comment="Orders."))
    assert not changes.preview_import(db, ImportRequest(rows=rows), ADMIN).blocked
