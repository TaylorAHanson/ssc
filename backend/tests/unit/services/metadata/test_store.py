"""Desired-state store: versions, authorship, the change log and reconciler bookkeeping (Workstream A)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.metadata import (
    MetadataChangeSetModel,
    MetadataEventModel,
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataReconcileQueueModel,
    MetadataTagModel,
)
from app.services.metadata import authz, environments, errors, store
from app.services.metadata.types import Actor, ChangeItem, ObjectKey

METADATA_TABLES = [
    MetadataObjectModel.__table__,
    MetadataTagModel.__table__,
    MetadataChangeSetModel.__table__,
    MetadataEventModel.__table__,
    MetadataObservedModel.__table__,
    MetadataReconcileQueueModel.__table__,
]

JANE = Actor(id="jane@example.com", groups=["team_a"])
PIPELINE = Actor(id="app-123", actor_type="service_principal")
SYSTEM = Actor(id="system", actor_type="system", display_name="System")

T = "Sales_Dev.Bronze.Orders"
T_KEY = "sales_dev.bronze.orders"


@pytest.fixture
def db():
    # Own engine: the shared db_session joins an outer transaction, and rollback()
    # (which the write path calls on a conflict) would wipe the test's data.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=METADATA_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    def environment_of(catalog):
        c = (catalog or "").lower()
        return "dev" if c.endswith("_dev") else "prd" if c.endswith("_prd") else None

    monkeypatch.setattr(environments, "environment_of", environment_of)
    monkeypatch.setattr(authz, "system_actor", lambda: SYSTEM)


@pytest.fixture
def clock(monkeypatch):
    """A store clock that ticks one second per call, so ordering by time is deterministic."""
    state = {"now": datetime(2026, 1, 1, 12, 0, 0)}

    def now():
        state["now"] += timedelta(seconds=1)
        return state["now"]

    monkeypatch.setattr(store, "_now", now)
    return state


def tag(key, after=None, *, name=T, column=None, action="set", before=None, before_version=0,
        securable_type=None, object_state="exists", environment=None):
    k = ObjectKey.parse(name, column)
    return ChangeItem(
        fqn_key=k.fqn_key, name=k.name, column=k.column_name,
        securable_type=securable_type or ("column" if column else "table"),
        environment=environment, field="tag", key=key, action=action, before=before,
        before_exists=before_version > 0, before_version=before_version,
        after=after if action == "set" else None, object_state=object_state,
    )


def comment(after, *, name=T, column=None, before=None, before_version=0, object_state="exists"):
    k = ObjectKey.parse(name, column)
    return ChangeItem(
        fqn_key=k.fqn_key, name=k.name, column=k.column_name,
        securable_type="column" if column else "table", field="comment",
        action="comment_set" if after is not None else "comment_clear", before=before,
        before_exists=before is not None, before_version=before_version, after=after,
        object_state=object_state,
    )


def write(db, items, actor=JANE, **kwargs):
    kwargs.setdefault("source", "ui")
    return store.write_items(db, items, actor=actor, **kwargs)


def counts(db):
    return db.query(MetadataChangeSetModel).count(), db.query(MetadataEventModel).count()


# --- desired_hash ----------------------------------------------------------------


def test_desired_hash_is_canonical():
    assert store.desired_hash({"b": "2", "a": "1"}, "x") == store.desired_hash({"a": "1", "b": "2"}, "x")
    assert store.desired_hash({"pii": ""}, None) == store.desired_hash({"pii": None}, None)
    assert store.desired_hash({"a": "1"}, None) != store.desired_hash({"a": "2"}, None)
    assert store.desired_hash({"a": "1"}, None) != store.desired_hash({"a": "1"}, "x")


# --- Writing ---------------------------------------------------------------------


def test_first_write_creates_a_pending_row_with_tags_comment_and_authorship(db):
    cs = write(db, [tag("tier", "gold", object_state="pending"), tag("pii", None, object_state="pending"),
                    comment("Orders.", object_state="pending")],
               reason="  Initial tags  ", securable_types={T_KEY: "view"})

    d = store.get_object(db, T_KEY)
    assert d.display_name == T
    assert d.securable_type == "view"
    assert d.environment == "dev"
    assert d.object_state == "pending"
    assert d.created_by == JANE.id and d.created_by_type == "user"
    assert d.tags == {"pii": None, "tier": "gold"}
    assert [t.key for t in d.tag_details] == ["pii", "tier"]
    for t in d.tag_details:
        assert (t.version, t.set_by, t.set_by_type, t.source, t.change_set_id) == (1, JANE.id, "user", "ui", cs.id)
    assert (d.comment.value, d.comment.version, d.comment.set_by, d.comment.change_set_id) == (
        "Orders.", 1, JANE.id, cs.id)
    assert d.desired_hash == store.desired_hash({"pii": None, "tier": "gold"}, "Orders.")
    assert d.last_apply_status == "pending"

    row = db.query(MetadataObjectModel).one()
    assert (row.catalog, row.schema_name, row.object_name, row.parent_key) == ("Sales_Dev", "Bronze", "Orders",
                                                                                "sales_dev.bronze")

    assert cs.reason == "Initial tags"
    assert cs.summary == {"objects": 1, "tags_set": 2, "tags_unset": 0, "comments": 1, "created": 1,
                          "restored": 0, "environments": ["dev"]}
    assert [e.action for e in cs.events] == ["object_create", "tag_set", "tag_set", "comment_set"]
    assert all(e.name == T and e.actor == JANE.id for e in cs.events)
    assert cs.events[1].before is None and cs.events[1].after == {"value": "gold", "version": 1}
    assert cs.events[3].after == {"value": "Orders.", "version": 1}

    queued = db.query(MetadataReconcileQueueModel).one()
    assert (queued.fqn_key, queued.change_set_id, queued.requested_by) == (T_KEY, cs.id, JANE.id)


def test_column_write_creates_its_row_and_queues_the_relation(db):
    write(db, [tag("pii", "email", column="Email")])
    col = store.get_object(db, f"{T_KEY}::email")
    assert col.display_name == f"{T}.Email"
    assert col.securable_type == "column"
    assert db.query(MetadataObjectModel).one().parent_key == T_KEY
    assert [r.fqn_key for r in db.query(MetadataReconcileQueueModel)] == [T_KEY]
    assert [c.fqn_key for c in store.get_columns(db, T_KEY)] == [f"{T_KEY}::email"]


def test_key_only_tags_round_trip_as_none(db):
    write(db, [tag("pii")])
    d = store.get_object(db, T_KEY)
    assert d.tags == {"pii": None}
    assert d.tag_details[0].value is None


def test_versions_are_per_tag_and_writers_on_different_keys_dont_conflict(db):
    write(db, [tag("tier", "gold")])
    # Both writers saw the object at the same time; each only touches its own key.
    write(db, [tag("owner", "team_a")], actor=PIPELINE, source="api")
    write(db, [tag("tier", "silver", before="gold", before_version=1)])

    d = store.get_object(db, T_KEY)
    details = {t.key: t for t in d.tag_details}
    assert d.tags == {"owner": "team_a", "tier": "silver"}
    assert (details["tier"].version, details["tier"].set_by) == (2, JANE.id)
    assert (details["owner"].version, details["owner"].set_by, details["owner"].set_by_type,
            details["owner"].source) == (1, PIPELINE.id, "service_principal", "api")
    assert d.desired_hash == store.desired_hash(d.tags, None)


def test_stale_versions_conflict_listing_every_mismatch_and_write_nothing(db):
    write(db, [tag("tier", "gold"), tag("owner", "team_a")])
    before = counts(db)

    with pytest.raises(errors.VersionConflict) as exc:
        write(db, [
            tag("tier", "silver", before="gold", before_version=1),  # current: fine
            tag("owner", "team_b"),                                  # expects "must not exist"
            comment("New.", before_version=3),
        ])

    conflicts = {(c.key, c.expected_version, c.current_version, c.current_value) for c in exc.value.conflicts}
    assert conflicts == {("owner", 0, 1, "team_a"), (None, 3, 0, None)}
    assert all(c.name == T for c in exc.value.conflicts)
    assert counts(db) == before
    assert store.get_object(db, T_KEY).tags == {"owner": "team_a", "tier": "gold"}


def test_expected_version_zero_means_the_tag_must_not_exist(db):
    write(db, [tag("tier", "gold")])
    with pytest.raises(errors.VersionConflict) as exc:
        write(db, [tag("tier", "gold")])
    assert exc.value.conflicts[0].current_version == 1


def test_unset_removes_the_tag_and_records_its_last_value(db):
    write(db, [tag("tier", "gold"), tag("pii")])
    cs = write(db, [tag("tier", action="unset", before="gold", before_version=1)])

    assert store.get_object(db, T_KEY).tags == {"pii": None}
    assert [(e.action, e.key, e.before, e.after) for e in cs.events] == [
        ("tag_unset", "tier", {"value": "gold", "version": 1}, None)
    ]
    assert cs.summary["tags_unset"] == 1
    assert store.get_object(db, T_KEY).desired_hash == store.desired_hash({"pii": None}, None)


def test_unset_of_a_tag_that_is_already_gone_writes_no_event(db):
    write(db, [tag("pii")])
    cs = write(db, [tag("tier", action="unset")])
    assert cs.events == []


def test_comment_set_then_clear_bumps_the_version(db):
    write(db, [comment("Orders.")])
    cs = write(db, [comment(None, before="Orders.", before_version=1)])
    d = store.get_object(db, T_KEY)
    assert (d.comment.value, d.comment.version) == (None, 2)
    assert [(e.action, e.before, e.after) for e in cs.events] == [
        ("comment_clear", {"value": "Orders.", "version": 1}, None)
    ]


def test_an_item_changed_twice_in_one_write_is_refused(db):
    with pytest.raises(errors.MetadataError, match="more than once"):
        write(db, [tag("tier", "gold"), tag("tier", "silver")])
    assert counts(db) == (0, 0)


def test_an_item_whose_key_does_not_match_its_name_is_refused(db):
    item = tag("tier", "gold").model_copy(update={"fqn_key": "other.s.t"})
    with pytest.raises(errors.MetadataError, match="doesn't match"):
        write(db, [item])


def test_losing_a_race_on_update_is_a_conflict_and_rolls_everything_back(db, monkeypatch):
    write(db, [tag("tier", "gold")])
    write(db, [tag("tier", "silver", before="gold", before_version=1)], actor=PIPELINE)
    before = counts(db)

    # The pre-check passes once (as if read before the other writer committed); the
    # conditional UPDATE then finds version 2, not 1.
    real = store._conflicts
    calls = {"n": 0}

    def stale_once(targets):
        calls["n"] += 1
        return [] if calls["n"] == 1 else real(targets)

    monkeypatch.setattr(store, "_conflicts", stale_once)
    with pytest.raises(errors.VersionConflict) as exc:
        write(db, [tag("new_key", "x"), tag("tier", "bronze", before="gold", before_version=1)])

    assert [(c.key, c.expected_version, c.current_version, c.current_value) for c in exc.value.conflicts] == [
        ("tier", 1, 2, "silver")
    ]
    assert counts(db) == before
    assert store.get_object(db, T_KEY).tags == {"tier": "silver"}


def test_losing_a_race_on_insert_is_a_conflict(db, monkeypatch):
    write(db, [tag("tier", "gold")], actor=PIPELINE)
    before = counts(db)

    # As if the tags were read before the other writer inserted "tier".
    real = store._load_current
    calls = {"n": 0}

    def blind_once(session, targets):
        real(session, targets)
        calls["n"] += 1
        if calls["n"] == 1:
            for target in targets.values():
                target.tags.clear()

    monkeypatch.setattr(store, "_load_current", blind_once)
    with pytest.raises(errors.VersionConflict) as exc:
        write(db, [comment("Orders."), tag("tier", "silver")])

    assert [(c.key, c.expected_version, c.current_version) for c in exc.value.conflicts] == [("tier", 0, 1)]
    assert counts(db) == before
    assert store.get_object(db, T_KEY).comment.value is None


def test_ensure_objects_creates_rows_with_nothing_on_them(db):
    seed = store.ObjectSeed(key=ObjectKey.parse("sales_dev.bronze"), securable_type="schema", object_state="exists")
    cs = write(db, [], ensure_objects=[seed], source="adopt")

    d = store.get_object(db, "sales_dev.bronze")
    assert (d.securable_type, d.object_state, d.tags, d.comment.value) == ("schema", "exists", {}, None)
    assert d.desired_hash == store.desired_hash({}, None)
    assert [e.action for e in cs.events] == ["object_create"]

    again = write(db, [], ensure_objects=[seed], source="adopt")
    assert again.events == []


def test_empty_write_is_an_idempotency_record_without_events(db):
    payload = {"status": "pending_approval", "request_id": "req-1", "preview": {}, "message": "Filed."}
    cs = write(db, [], idempotency_key="k1", request_id="req-1", result_payload=payload)

    assert cs.events == [] and cs.summary["objects"] == 0
    assert store.find_idempotent(db, JANE.id, "k1") == payload
    assert store.find_recorded(db, "req-1") is None
    assert db.query(MetadataReconcileQueueModel).count() == 0


def test_find_idempotent_returns_committed_results_with_their_change_set(db):
    payload = {"status": "committed", "preview": {}, "message": "Saved."}
    cs = write(db, [tag("tier", "gold")], idempotency_key="k1", result_payload=payload)

    found = store.find_idempotent(db, JANE.id, "k1")
    assert found["status"] == "committed"
    assert found["change_set"]["id"] == cs.id
    assert [e["action"] for e in found["change_set"]["events"]] == ["object_create", "tag_set"]
    assert store.find_idempotent(db, PIPELINE.id, "k1") is None
    assert store.find_idempotent(db, JANE.id, "") is None


def test_update_result_replaces_the_stored_result(db):
    cs = write(db, [tag("tier", "gold")], idempotency_key="k1", result_payload={"status": "committed", "preview": {}})
    store.update_result(db, cs.id, {"status": "partial", "request_id": "req-9", "preview": {}, "message": ""})
    found = store.find_idempotent(db, JANE.id, "k1")
    assert (found["status"], found["request_id"], found["change_set"]["id"]) == ("partial", "req-9", cs.id)


def test_find_recorded_returns_the_change_set_that_recorded_a_request(db):
    write(db, [], request_id="req-1", idempotency_key="k")
    cs = write(db, [tag("tier", "gold")], request_id="req-1", source="agent")
    assert store.find_recorded(db, "req-1").id == cs.id
    assert store.find_recorded(db, "req-2") is None


# --- Deleted rows ----------------------------------------------------------------


def _orphan(db, fqn_key, days_ago):
    store.set_object_state(db, fqn_key, "orphaned")
    db.query(MetadataObjectModel).filter_by(fqn_key=fqn_key).update(
        {"orphaned_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)}
    )
    db.commit()


READ_NOW = {"sales_dev": datetime.now(timezone.utc).replace(tzinfo=None)}


def test_purge_orphans_soft_deletes_old_orphans_under_a_system_change_set(db):
    write(db, [tag("tier", "gold"), comment("Orders.")])
    write(db, [tag("tier", "gold", name="sales_dev.bronze.recent")])
    _orphan(db, T_KEY, 40)
    _orphan(db, "sales_dev.bronze.recent", 2)

    assert store.purge_orphans(db, older_than_days=30, read_after=READ_NOW) == 1

    assert store.get_object(db, T_KEY) is None
    deleted = store.get_object(db, T_KEY, include_deleted=True)
    assert (deleted.status, deleted.tags, deleted.comment.value, deleted.comment.version) == ("deleted", {}, None, 0)
    assert store.get_object(db, "sales_dev.bronze.recent").object_state == "orphaned"

    cs = store.list_change_sets(db, source="system").items[0]
    assert (cs.actor, cs.actor_type, cs.summary) == ("system", "system", {"objects": 1, "deleted": 1})
    event = store.get_change_set(db, cs.id).events[0]
    assert event.action == "object_delete"
    assert event.before["tags"] == {"tier": "gold"} and event.before["comment"] == "Orders."
    assert store.purge_orphans(db, older_than_days=30, read_after=READ_NOW) == 0


@pytest.mark.parametrize("read_after", [{}, {"sales_dev": datetime(2000, 1, 1)}, {"other": datetime(2999, 1, 1)}])
def test_an_orphan_is_kept_until_a_later_read_of_its_catalog_confirms_it(db, read_after):
    write(db, [tag("tier", "gold")])
    _orphan(db, T_KEY, 40)

    assert store.purge_orphans(db, older_than_days=30, read_after=read_after) == 0
    assert store.get_object(db, T_KEY).tags == {"tier": "gold"}


def test_writing_to_a_deleted_row_restores_it_empty(db):
    write(db, [tag("tier", "gold"), tag("pii"), comment("Orders.")])
    _orphan(db, T_KEY, 40)
    store.purge_orphans(db, older_than_days=30, read_after=READ_NOW)

    cs = write(db, [tag("owner", "team_a", object_state="pending")])

    d = store.get_object(db, T_KEY)
    assert (d.status, d.object_state, d.tags, d.comment.version) == ("active", "pending", {"owner": "team_a"}, 0)
    assert d.orphaned_at is None
    assert [e.action for e in cs.events] == ["object_restore", "tag_set"]
    assert cs.summary["restored"] == 1


def test_a_deleted_row_has_version_zero_everywhere(db):
    write(db, [tag("tier", "gold"), comment("Orders.")])
    _orphan(db, T_KEY, 40)
    store.purge_orphans(db, older_than_days=30, read_after=READ_NOW)
    with pytest.raises(errors.VersionConflict):
        write(db, [tag("tier", "silver", before="gold", before_version=1)])


# --- Change sets and history -----------------------------------------------------


def test_list_change_sets_filters_and_pages_newest_first(db, clock):
    a = write(db, [tag("tier", "gold")])
    b = write(db, [tag("pii", "email", column="Email")], actor=PIPELINE, source="api")
    # An underscore in the name must not act as a LIKE wildcard.
    c = write(db, [tag("tier", "gold", name="salesXdev.bronze.orders")])

    assert [cs.id for cs in store.list_change_sets(db).items] == [c.id, b.id, a.id]
    assert store.list_change_sets(db).items[0].events == []
    assert [cs.id for cs in store.list_change_sets(db, actor="JANE@example.com").items] == [c.id, a.id]
    assert [cs.id for cs in store.list_change_sets(db, source="api").items] == [b.id]
    assert [cs.id for cs in store.list_change_sets(db, fqn_key=T_KEY).items] == [b.id, a.id]
    assert [cs.id for cs in store.list_change_sets(db, fqn_key=f"{T_KEY}::email").items] == [b.id]
    # A catalog or schema matches everything under it, on whole name parts only.
    schema_key = T_KEY.rsplit(".", 1)[0]
    assert [cs.id for cs in store.list_change_sets(db, fqn_key=schema_key).items] == [b.id, a.id]
    assert [cs.id for cs in store.list_change_sets(db, fqn_key=schema_key.split(".")[0]).items] == [b.id, a.id]
    assert store.list_change_sets(db, fqn_key=schema_key[:-2]).items == []

    page = store.list_change_sets(db, page=2, page_size=2)
    assert (page.total, page.page, page.page_size, [cs.id for cs in page.items]) == (3, 2, 2, [a.id])
    assert store.list_change_sets(db, page_size=10_000).page_size == 500


def test_get_change_set_returns_events_with_names_or_raises(db):
    cs = write(db, [tag("tier", "gold")])
    got = store.get_change_set(db, cs.id)
    assert [e.name for e in got.events] == [T, T]
    assert store.get_change_set(db, cs.id, include_events=False).events == []
    with pytest.raises(errors.NotFound):
        store.get_change_set(db, "nope")


def test_object_history_includes_columns_newest_first(db):
    write(db, [tag("tier", "gold")])
    write(db, [tag("pii", "email", column="Email")])
    write(db, [tag("tier", "gold", name="sales_dev.bronze.orders_archive")])

    history = store.object_history(db, T_KEY)
    assert [(e.fqn_key, e.action) for e in history] == [
        (f"{T_KEY}::email", "tag_set"), (f"{T_KEY}::email", "object_create"),
        (T_KEY, "tag_set"), (T_KEY, "object_create"),
    ]
    assert history[0].name == f"{T}.Email"
    assert len(store.object_history(db, T_KEY, limit=1)) == 1


# --- Revert ----------------------------------------------------------------------


def test_revert_items_restore_before_values_against_current_state(db):
    write(db, [tag("b", "1"), tag("c", "keep"), comment("Old.")])
    cs = write(db, [
        tag("a", "new"),
        tag("b", "2", before="1", before_version=1),
        tag("c", action="unset", before="keep", before_version=1),
        comment("New.", before="Old.", before_version=1),
    ])
    write(db, [tag("a", "newer", before="new", before_version=1)])

    items = {(i.field, i.key): i for i in store.revert_items(db, cs.id)}

    assert set(items) == {("tag", "a"), ("tag", "b"), ("tag", "c"), ("comment", None)}
    assert (items["tag", "a"].action, items["tag", "a"].before, items["tag", "a"].before_version) == (
        "unset", "newer", 2)
    assert (items["tag", "b"].action, items["tag", "b"].after, items["tag", "b"].before_version) == ("set", "1", 2)
    assert (items["tag", "c"].action, items["tag", "c"].after, items["tag", "c"].before_exists,
            items["tag", "c"].before_version) == ("set", "keep", False, 0)
    assert (items["comment", None].action, items["comment", None].after,
            items["comment", None].before_version) == ("comment_set", "Old.", 2)
    assert all(i.name == T and i.environment == "dev" for i in items.values())

    revert = write(db, list(items.values()), source="revert", reverts_change_set_id=cs.id)
    d = store.get_object(db, T_KEY)
    assert d.tags == {"b": "1", "c": "keep"}
    assert d.comment.value == "Old."
    assert store.get_change_set(db, cs.id).reverted_by == revert.id
    assert store.get_change_set(db, revert.id).reverts_change_set_id == cs.id

    with pytest.raises(errors.MetadataError, match="already reverted"):
        store.revert_items(db, cs.id)


def test_revert_items_skip_values_already_restored(db):
    write(db, [], ensure_objects=[store.ObjectSeed(key=ObjectKey.parse(T), securable_type="table")])
    cs = write(db, [tag("a", "1")])
    write(db, [tag("a", action="unset", before="1", before_version=1)])
    plan = store.revert_plan(db, cs.id)
    assert plan.items == [] and plan.unmanaged == []
    assert plan.changed_since == [(T_KEY, T, "a")]


def test_revert_of_unknown_or_empty_change_sets(db):
    with pytest.raises(errors.NotFound):
        store.revert_items(db, "nope")
    cs = write(db, [])
    with pytest.raises(errors.MetadataError, match="nothing to revert"):
        store.revert_items(db, cs.id)


def test_reverting_the_change_set_that_created_a_row_drops_the_row(db):
    cs = write(db, [tag("tier", "gold"), comment("Orders.")])
    write(db, [tag("pii", "x")])

    plan = store.revert_plan(db, cs.id)

    assert plan.unmanaged == [T_KEY]
    assert plan.changed_since == [(T_KEY, T, "pii")]
    assert all(i.stops_managing for i in plan.items)
    assert [(i.field, i.key, i.action, i.before, i.before_version) for i in plan.items] == [
        ("tag", "pii", "unset", "x", 1), ("tag", "tier", "unset", "gold", 1), ("comment", None, "comment_clear", "Orders.", 1),
    ]

    revert = write(db, plan.items, source="revert", reverts_change_set_id=cs.id)

    assert store.get_object(db, T_KEY) is None
    row = store.get_object(db, T_KEY, include_deleted=True)
    assert (row.status, row.tags, row.comment.value) == ("deleted", {}, None)
    assert revert.summary["unmanaged"] == 1
    [event] = revert.events
    assert event.action == "object_delete"
    assert event.before["tags"] == {"pii": "x", "tier": "gold"} and event.before["comment"] == "Orders."
    assert db.query(MetadataReconcileQueueModel).filter_by(change_set_id=revert.id).count() == 0
    assert store.get_change_set(db, cs.id).reverted_by == revert.id
    with pytest.raises(errors.MetadataError, match="stopped managing"):
        store.revert_items(db, revert.id)


def test_a_row_created_by_a_tagless_change_set_is_dropped_too(db):
    cs = write(db, [], ensure_objects=[store.ObjectSeed(key=ObjectKey.parse("sales_dev"), securable_type="catalog")])
    [item] = store.revert_items(db, cs.id)
    assert (item.field, item.action, item.before_exists, item.stops_managing) == ("comment", "comment_clear", False, True)


def test_a_row_recreated_since_is_restored_value_by_value(db):
    cs = write(db, [tag("tier", "gold")])
    write(db, [tag("tier", action="unset", before="gold", before_version=1)])
    db.query(MetadataObjectModel).filter_by(fqn_key=T_KEY).update({"status": "deleted"})
    db.commit()
    write(db, [tag("tier", "silver")])

    plan = store.revert_plan(db, cs.id)

    assert plan.unmanaged == []
    assert [(i.key, i.action, i.stops_managing) for i in plan.items] == [("tier", "unset", False)]


def test_dropping_a_row_conflicts_with_a_tag_set_since_it_was_planned(db):
    cs = write(db, [tag("tier", "gold")])
    items = store.revert_items(db, cs.id)
    write(db, [tag("pii", "x")])
    with pytest.raises(errors.VersionConflict) as exc:
        write(db, items, source="revert", reverts_change_set_id=cs.id)
    assert [(c.key, c.expected_version, c.current_version) for c in exc.value.conflicts] == [("pii", 0, 1)]
    assert store.get_object(db, T_KEY).tags == {"tier": "gold", "pii": "x"}


def test_a_first_row_records_the_uc_values_it_leaves_out(db):
    removed = tag("pii", action="unset", before="email")
    removed.before_exists = True
    cleared = comment(None, before="From UC.")
    cs = write(db, [tag("tier", "gold"), removed, cleared])

    assert cs.summary["tags_unset"] == 1 and cs.summary["comments"] == 1
    events = {(e.action, e.key): e for e in cs.events}
    assert events["tag_unset", "pii"].before == {"value": "email", "version": 0}
    assert events["comment_clear", None].before == {"value": "From UC.", "version": 0}
    assert store.get_object(db, T_KEY).tags == {"tier": "gold"}


def test_a_change_set_can_only_be_reverted_once(db):
    cs = write(db, [tag("a", "1")])
    write(db, [tag("a", action="unset", before="1", before_version=1)], reverts_change_set_id=cs.id)
    before = counts(db)
    with pytest.raises(errors.MetadataError, match="already reverted"):
        write(db, [tag("b", "2")], reverts_change_set_id=cs.id)
    assert counts(db) == before
    assert "b" not in store.get_object(db, T_KEY).tags
    with pytest.raises(errors.NotFound):
        write(db, [tag("b", "2")], reverts_change_set_id="nope")


# --- Reads -----------------------------------------------------------------------


def test_get_objects_batches_and_hides_missing_keys(db):
    write(db, [tag("a", "1")])
    write(db, [tag("a", "1", name="sales_dev.bronze.items")])
    found = store.get_objects(db, [T_KEY, "sales_dev.bronze.items", "nope", ""])
    assert sorted(found) == ["sales_dev.bronze.items", T_KEY]
    assert store.object_key(found[T_KEY]).display == T


# --- Reconciler bookkeeping ------------------------------------------------------


def test_set_object_state_tracks_orphaned_at(db):
    write(db, [tag("a", "1", object_state="pending")])
    updated_at = store.get_object(db, T_KEY).updated_at

    store.set_object_state(db, T_KEY, "exists", securable_type="view")
    d = store.get_object(db, T_KEY)
    assert (d.object_state, d.securable_type, d.orphaned_at) == ("exists", "view", None)
    assert d.updated_at == updated_at

    store.set_object_state(db, T_KEY, "orphaned")
    first = store.get_object(db, T_KEY).orphaned_at
    assert first is not None
    store.set_object_state(db, T_KEY, "orphaned")
    assert store.get_object(db, T_KEY).orphaned_at == first

    store.set_object_state(db, T_KEY, "exists")
    assert store.get_object(db, T_KEY).orphaned_at is None

    store.set_object_state(db, "no.such.object", "exists")
    with pytest.raises(errors.MetadataError):
        store.set_object_state(db, T_KEY, "gone")
