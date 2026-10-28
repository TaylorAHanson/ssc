"""Reconciler phase 2: runs (plan/enforce, breaker, lifecycle, adopt), queue drains and the poller pass."""
import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.metadata import (
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataReconcileItemModel,
    MetadataReconcileQueueModel,
    MetadataReconcileRunModel,
    MetadataSyncStateModel,
    MetadataTagModel,
)
from app.services.metadata import changes, observed, reconciler, store
from app.services.metadata.observed import SweepResult
from app.services.metadata.types import ObjectKey, ObservedState

TABLES = [
    MetadataObjectModel.__table__,
    MetadataTagModel.__table__,
    MetadataObservedModel.__table__,
    MetadataReconcileQueueModel.__table__,
    MetadataReconcileRunModel.__table__,
    MetadataReconcileItemModel.__table__,
    MetadataSyncStateModel.__table__,
]
LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}


@pytest.fixture
def db():
    # Own engine: the shared db_session joins an outer transaction that rollback() would wipe.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for name, value in {
        "METADATA_RECONCILE_MODE": "enforce",
        "METADATA_RECONCILE_CATALOGS": ["*"],
        "METADATA_EXEMPT_KEYS": ["system.certified"],
        "METADATA_MAX_REMOVALS_PER_RUN": 200,
        "METADATA_NEW_OBJECT_POLICY": "adopt",
        "METADATA_NEW_OBJECT_POLL_SECONDS": 0,
        "METADATA_ORPHAN_RETENTION_DAYS": 30,
        "METADATA_RECONCILE_CRON": "",
        "DATABRICKS_WAREHOUSE_ID": "wh-1",
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(reconciler, "_DEFAULT_APPLY_WORKERS", 1)
    for name in ("_next_sweep_time", "_last_poll_at", "_breaker_noted", "_pass_error_noted"):
        monkeypatch.setattr(reconciler, name, None)


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(reconciler, "_alert_governance", lambda subject, lines: sent.append((subject, list(lines))))
    return sent


class FakeUC:
    """Unity Catalog as the observed module would read it, written into the real snapshot table."""

    def __init__(self):
        self.objects = {}
        self.unreadable = set()
        self.refreshed = []
        self.swept = []
        self.polled = []
        self.scope = ["c"]
        self.fail_refresh = None

    def add(self, fqn_key, tags=None, comment=None, *, securable_type=None, created=None):
        level = ObjectKey.from_fqn_key(fqn_key).level
        self.objects[fqn_key] = {
            "tags": dict(tags or {}), "comment": comment,
            "type": securable_type or LEVEL_TYPES[level], "created": created,
        }

    def _write(self, db, fqn_key):
        key = ObjectKey.from_fqn_key(fqn_key)
        obj = self.objects.get(fqn_key)
        row = db.get(MetadataObservedModel, fqn_key)
        if obj is None:
            if row is not None:
                row.present = False
            return
        if row is None:
            parent = key.parent()
            row = MetadataObservedModel(
                fqn_key=fqn_key, catalog=key.catalog, schema_name=key.schema_name, object_name=key.object_name,
                column_name=key.column_name, parent_key=parent.fqn_key if parent else None, display_name=key.display,
            )
            db.add(row)
        row.securable_type = obj["type"]
        row.tags = dict(obj["tags"])
        row.comment = obj["comment"]
        row.present = True
        row.created = obj["created"]
        row.observed_at = datetime(2026, 10, 20, 12, 0)

    def refresh(self, db, keys, provider=None):
        if self.fail_refresh:
            raise RuntimeError(self.fail_refresh)
        keys = list(keys)
        self.refreshed.append(sorted(k.fqn_key for k in keys))
        written = []
        for key in keys:
            if key.catalog.lower() in self.unreadable:
                continue
            targets = [key.fqn_key]
            if key.level == "relation":
                prefix = key.fqn_key + "::"
                targets += [k for k in self.objects if k.startswith(prefix)]
                targets += [r.fqn_key for r in db.query(MetadataObservedModel).filter(
                    MetadataObservedModel.parent_key == key.fqn_key)]
            for target in dict.fromkeys(targets):
                self._write(db, target)
                written.append(target)
        db.flush()
        states = observed.get_observed(db, written)
        for key in keys:
            if key.catalog.lower() not in self.unreadable and key.fqn_key not in states:
                states[key.fqn_key] = ObservedState(
                    fqn_key=key.fqn_key, key=key, securable_type=LEVEL_TYPES[key.level], present=False,
                )
        return states

    def sweep(self, db, provider=None, catalogs=None):
        self.swept.append(list(catalogs))
        result = SweepResult()
        for catalog in catalogs:
            if catalog in self.unreadable:
                result.failed_catalogs[catalog] = "PERMISSION_DENIED"
                continue
            keys = {k for k in self.objects if ObjectKey.from_fqn_key(k).catalog == catalog}
            keys |= {r.fqn_key for r in db.query(MetadataObservedModel).filter(MetadataObservedModel.catalog == catalog)}
            for key in keys:
                self._write(db, key)
            db.commit()
            result.catalogs.append(catalog)
        return result

    def poll_new_objects(self, db, provider=None):
        for key in self.polled:
            self._write(db, key)
        db.flush()
        return list(self.polled)


@pytest.fixture
def uc(monkeypatch):
    fake = FakeUC()
    fake.add("c")
    monkeypatch.setattr(observed, "refresh", fake.refresh)
    monkeypatch.setattr(observed, "sweep", fake.sweep)
    monkeypatch.setattr(observed, "scope_catalogs", lambda provider=None: list(fake.scope))
    monkeypatch.setattr(observed, "poll_new_objects", fake.poll_new_objects)
    return fake


class FakeStatements:
    def __init__(self):
        self.executed = []
        self.failures = {}
        self.on_execute = None

    def execute_statement(self, statement, warehouse_id, wait_timeout):
        self.executed.append(statement)
        if self.on_execute:
            self.on_execute(statement)
        for fragment, message in self.failures.items():
            if fragment in statement:
                raise RuntimeError(message)
        return SimpleNamespace(statement_id="s", status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None))


@pytest.fixture
def provider():
    statements = FakeStatements()
    return SimpleNamespace(client=SimpleNamespace(statement_execution=statements), statements=statements)


def desired(db, fqn_key, tags=None, comment=None, *, state="exists", securable_type=None, drift=None,
            apply_status=None):
    key = ObjectKey.from_fqn_key(fqn_key)
    parent = key.parent()
    object_id = f"o-{fqn_key}"
    db.add(MetadataObjectModel(
        id=object_id, fqn_key=fqn_key, securable_type=securable_type or LEVEL_TYPES[key.level], catalog=key.catalog,
        schema_name=key.schema_name, object_name=key.object_name, column_name=key.column_name,
        parent_key=parent.fqn_key if parent else None, display_name=key.display, comment=comment,
        comment_version=1 if comment else 0, object_state=state, desired_hash=store.desired_hash(tags or {}, comment),
        drift_detected_at=drift, last_apply_status=apply_status,
    ))
    for tag_key, value in (tags or {}).items():
        db.add(MetadataTagModel(object_id=object_id, key=tag_key, value=value))
    db.commit()
    return object_id


def obj(db, fqn_key):
    db.expire_all()
    return db.query(MetadataObjectModel).filter_by(fqn_key=fqn_key).one()


def queue(db):
    db.expire_all()
    return {r.fqn_key: r for r in db.query(MetadataReconcileQueueModel)}


def ops(result):
    return [(i.fqn_key, i.operation, i.status) for i in result.items]


def make_due(db):
    db.query(MetadataReconcileQueueModel).update({"not_before": datetime(2000, 1, 1)})
    db.commit()


# --- run: plan vs enforce -----------------------------------------------------------------


def test_plan_mode_records_the_plan_and_never_runs_a_statement(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    uc.add("c.s.t", {"a": "old", "stray": "1", "system.certified": "yes"})
    desired(db, "c.s.t", {"a": "1"})

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert provider.statements.executed == []
    assert result.status == "completed" and result.mode == "plan"
    assert ops(result) == [("c.s.t", "unset", "planned"), ("c.s.t", "set", "planned")]
    assert (result.objects_checked, result.objects_drifted, result.removals_planned) == (1, 1, 1)
    row = obj(db, "c.s.t")
    assert row.drift_detected_at is not None and row.last_apply_status is None
    stored = reconciler.get_run(db, result.run_id)
    assert [(i.operation, i.keys, i.tags) for i in stored.items] == [("unset", ["stray"], {}), ("set", [], {"a": "1"})]


def test_plan_mode_never_runs_a_statement_even_past_the_removal_limit(db, uc, provider, monkeypatch, alerts):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    monkeypatch.setattr(settings, "METADATA_MAX_REMOVALS_PER_RUN", 1)
    uc.add("c.s.t", {"x": "1", "y": "2"})
    desired(db, "c.s.t")

    result = reconciler.run(db, trigger="sweep", provider=provider)

    assert provider.statements.executed == [] and alerts == []
    assert result.status == "completed" and result.removals_planned == 2
    assert reconciler.circuit_breaker_tripped(db) is False


def test_enforce_applies_and_records_convergence(db, uc, provider):
    uc.add("c.s.t", {"a": "old", "stray": "1", "system.certified": "yes"}, comment="old")
    uc.add("c.s.t::email")
    desired(db, "c.s.t", {"a": "1"}, comment="Orders", drift=datetime(2026, 1, 1))
    desired(db, "c.s.t::email", {"pii": None})

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert len(provider.statements.executed) == 4 and result.status == "completed"
    assert result.statements_applied == 4 and result.objects_drifted == 2
    for key in ("c.s.t", "c.s.t::email"):
        row = obj(db, key)
        assert (row.last_apply_status, row.drift_detected_at, row.last_apply_error) == ("applied", None, None)
        assert row.last_applied_at is not None and row.last_observed_at is not None
    snapshot = observed.get_observed(db, ["c.s.t", "c.s.t::email"])
    assert snapshot["c.s.t"].tags == {"a": "1", "system.certified": "yes"} and snapshot["c.s.t"].comment == "Orders"
    assert snapshot["c.s.t::email"].tags == {"pii": None}


def test_enforce_skips_in_sync_objects(db, uc, provider):
    uc.add("c.s.t", {"a": "1"})
    desired(db, "c.s.t", {"a": "1"}, apply_status="failed")

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert provider.statements.executed == [] and result.items == []
    assert (result.objects_checked, result.objects_drifted) == (1, 0)
    assert obj(db, "c.s.t").last_apply_status == "applied"


def test_a_failed_statement_records_the_error_and_drift(db, uc, provider):
    uc.add("c")
    uc.add("c.s.t", {"stray": "1"})
    desired(db, "c.s.t", {"a": "1"})
    provider.statements.failures = {"UNSET TAGS": "PERMISSION_DENIED: no APPLY TAG"}

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert result.status == "failed"
    assert [(o.operation, o.status) for o in result.items] == [("unset", "failed"), ("set", "skipped")]
    row = obj(db, "c.s.t")
    assert row.last_apply_status == "failed" and "PERMISSION_DENIED" in row.last_apply_error
    assert row.drift_detected_at is not None and row.last_applied_at is None


def test_a_desired_edit_during_the_run_keeps_its_own_state(db, uc, provider):
    uc.add("c")
    uc.add("c.s.t")
    desired(db, "c.s.t", {"a": "1"})

    def edit(_sql):
        db.query(MetadataObjectModel).filter_by(fqn_key="c.s.t").update({"desired_hash": "edited"})
        db.commit()

    provider.statements.on_execute = edit
    reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert obj(db, "c.s.t").last_apply_status is None


# --- run: circuit breaker -------------------------------------------------------------------


def test_too_many_removals_trip_the_breaker_before_anything_runs(db, uc, provider, monkeypatch, alerts):
    monkeypatch.setattr(settings, "METADATA_MAX_REMOVALS_PER_RUN", 1)
    uc.add("c.s.t", {"x": "1", "y": "2"})
    desired(db, "c.s.t")

    tripped = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert provider.statements.executed == []
    assert tripped.status == "paused" and "remove 2 tags" in tripped.paused_reason
    assert {i.status for i in tripped.items} == {"planned"} and tripped.removals_planned == 2
    assert reconciler.circuit_breaker_state(db)["run_id"] == tripped.run_id
    assert obj(db, "c.s.t").drift_detected_at is not None
    assert len(alerts) == 1 and "too many removals" in alerts[0][0]

    paused = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)
    assert paused.status == "paused" and "circuit breaker" in paused.paused_reason
    assert len(uc.refreshed) == 1 and provider.statements.executed == []

    planned = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], mode="plan", provider=provider)
    assert planned.status == "completed" and provider.statements.executed == []

    override = reconciler.run(
        db, trigger="manual", fqn_keys=["c.s.t"], provider=provider, actor="admin@x.com", override_circuit_breaker=True,
    )
    assert override.status == "completed"
    assert provider.statements.executed[-1] == "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('x', 'y');"
    assert reconciler.circuit_breaker_tripped(db) is False
    assert reconciler._get_state(db, reconciler.CIRCUIT_BREAKER_KEY)["cleared_by"] == "admin@x.com"
    assert len(alerts) == 1


# --- run: lifecycle, adoption ---------------------------------------------------------------


def test_a_targeted_run_reads_only_the_named_objects(db, uc, provider):
    uc.add("c.s.t")
    desired(db, "c.s.t", {"a": "1"})

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert ops(result) == [("c.s.t", "set", "applied")]
    assert uc.refreshed == [["c.s.t"]]


def test_a_pending_object_that_now_exists_becomes_exists_and_is_applied(db, uc, provider):
    uc.add("c")
    uc.add("c.s.v", securable_type="view")
    desired(db, "c.s.v", {"a": "1"}, state="pending")

    reconciler.run(db, trigger="manual", fqn_keys=["c.s.v"], provider=provider)

    row = obj(db, "c.s.v")
    assert (row.object_state, row.securable_type, row.last_apply_status) == ("exists", "view", "applied")
    assert provider.statements.executed == ["ALTER VIEW `c`.`s`.`v` SET TAGS ('a' = '1');"]


def test_a_vanished_object_is_orphaned_and_keeps_its_desired_state(db, uc, provider):
    desired(db, "c.s.gone", {"a": "1"}, drift=datetime(2026, 1, 1))
    desired(db, "c.s.later", {"b": "2"}, state="pending")

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.gone", "c.s.later"], provider=provider)

    assert provider.statements.executed == [] and result.items == []
    gone = obj(db, "c.s.gone")
    assert (gone.object_state, gone.drift_detected_at) == ("orphaned", None) and gone.orphaned_at is not None
    assert store.get_objects(db, ["c.s.gone"])["c.s.gone"].tags == {"a": "1"}
    assert obj(db, "c.s.later").object_state == "pending"


def test_adopt_policy_adopts_new_objects_from_the_poll_but_not_from_a_change(db, uc, provider, monkeypatch):
    adopted = []
    monkeypatch.setattr(changes, "adopt_observed", lambda db_, keys, *, reason: adopted.append((list(keys), reason)))
    uc.add("c.s.fresh", {"a": "1"})
    uc.add("c.s.edited", {"b": "2"})
    reconciler.enqueue(db, ["c.s.fresh"], reason="new_object")
    reconciler.enqueue(db, ["c.s.edited"], reason="change")
    db.commit()

    result = reconciler.drain_queue(db, provider=provider)

    assert adopted == [(["c.s.fresh"], reconciler._ADOPT_REASON)]
    assert provider.statements.executed == [] and result.items == []
    assert queue(db) == {}


def test_sync_history_says_what_started_a_queue_run_and_that_it_wasnt_a_full_check(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    monkeypatch.setattr(changes, "adopt_observed", lambda db_, keys, *, reason: None)
    uc.add("c.s.t")
    triggers = []
    for reasons in (["new_object"], ["notify"], ["new_object", "notify"], ["change", "new_object"]):
        for i, reason in enumerate(reasons):
            reconciler.enqueue(db, [f"c.s.t{i}"], reason=reason)
        db.commit()
        result = reconciler.drain_queue(db, provider=provider)
        assert result.full_check is False
        triggers.append(result.trigger)

    assert triggers == ["new_object", "notify", "new_object", "queue"]
    assert reconciler.run(db, trigger="sweep", provider=provider).full_check is True


def test_new_objects_arent_adopted_without_a_catalog_list(db, uc, provider, monkeypatch):
    adopted = []
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    monkeypatch.setattr(changes, "adopt_observed", lambda db_, keys, *, reason: adopted.append(keys))
    uc.add("c.s.fresh", {"a": "1"})

    reconciler.run(db, trigger="new_object", fqn_keys=["c.s.fresh"], provider=provider)

    assert adopted == []


# --- The catalog list is the write boundary ------------------------------------------------


def test_enforce_without_a_catalog_list_only_plans(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    uc.add("c.s.t", {"stray": "1"})
    desired(db, "c.s.t", {"a": "1"})

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert result.mode == "plan" and provider.statements.executed == []
    assert {i.status for i in result.items} == {"planned"}


def test_a_queued_object_outside_the_catalog_list_is_dropped_untouched(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["c"])
    uc.add("prod")
    uc.add("prod.s.t", {"stray": "1"})
    uc.add("c.s.t")
    desired(db, "prod.s.t", {"a": "1"})
    desired(db, "c.s.t", {"a": "1"})
    reconciler.enqueue(db, ["prod.s.t", "c.s.t"])
    db.commit()

    result = reconciler.drain_queue(db, provider=provider)

    executed = " ".join(provider.statements.executed)
    assert "`prod`" not in executed and "`c`.`s`.`t`" in executed
    assert "prod" in result.skipped_catalogs
    assert all(not k.startswith("prod") for batch in uc.refreshed for k in batch)
    assert queue(db) == {}


def test_a_sweep_leaves_catalogs_outside_the_list_alone(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["c*"])
    uc.add("prod")
    uc.add("prod.s.t", {"stray": "1"})
    desired(db, "prod.s.t", {"a": "1"})

    result = reconciler.run(db, trigger="manual", catalogs=["c", "prod"], provider=provider)

    assert "prod" in result.skipped_catalogs
    assert all("prod" not in s for s in provider.statements.executed)
    assert all("prod" not in batch for batch in uc.swept)


def test_a_cleared_description_counts_toward_the_removal_limit(db, uc, provider, monkeypatch, alerts):
    monkeypatch.setattr(settings, "METADATA_MAX_REMOVALS_PER_RUN", 0)
    uc.add("c.s.t", comment="Orders placed online")
    desired(db, "c.s.t")

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert result.status == "paused" and provider.statements.executed == []
    assert result.removals_planned == 1


def test_ignore_policy_leaves_new_objects_unmanaged(db, uc, provider, monkeypatch):
    adopted = []
    monkeypatch.setattr(settings, "METADATA_NEW_OBJECT_POLICY", "ignore")
    monkeypatch.setattr(changes, "adopt_observed", lambda db_, keys, *, reason: adopted.append(keys))
    uc.add("c.s.fresh", {"a": "1"})

    reconciler.run(db, trigger="new_object", fqn_keys=["c.s.fresh"], provider=provider)

    assert adopted == [] and provider.statements.executed == []


def test_a_failed_adopt_is_retried(db, uc, provider, monkeypatch):
    def boom(db_, keys, *, reason):
        raise RuntimeError("db is down")

    monkeypatch.setattr(changes, "adopt_observed", boom)
    uc.add("c.s.fresh")
    reconciler.enqueue(db, ["c.s.fresh"], reason="notify")
    db.commit()

    reconciler.drain_queue(db, provider=provider)

    row = queue(db)["c.s.fresh"]
    assert row.attempts == 1 and "db is down" in row.last_error


# --- run: sweeps ------------------------------------------------------------------------------


def test_a_sweep_reconciles_every_managed_object_and_purges_orphans(db, uc, provider, monkeypatch):
    purged = []
    monkeypatch.setattr(store, "purge_orphans", lambda db_, *, older_than_days: purged.append(older_than_days) or 0)
    uc.scope = ["c", "d"]
    uc.unreadable = {"d"}
    uc.add("c")
    uc.add("c.s.a", {"stray": "1"})
    uc.add("c.s.b", {"b": "1"})
    desired(db, "c.s.a")
    desired(db, "c.s.b", {"b": "1"})
    desired(db, "c.s.gone", {"x": "1"})
    desired(db, "d.s.x", {"x": "1"})

    result = reconciler.run(db, trigger="sweep", provider=provider)

    assert uc.swept == [["c", "d"]]
    assert provider.statements.executed == ["ALTER TABLE `c`.`s`.`a` UNSET TAGS ('stray');"]
    assert result.skipped_catalogs == ["d"] and result.objects_checked == 2
    assert result.unread_catalogs == ["d"]
    assert obj(db, "c.s.gone").object_state == "orphaned"
    assert obj(db, "d.s.x").object_state == "exists" and obj(db, "d.s.x").last_apply_status is None
    assert purged == [30]

    reconciler.run(db, trigger="manual", catalogs=["C"], provider=provider)
    assert uc.swept[-1] == ["c"] and purged == [30]


def test_a_read_that_fails_or_misses_things_never_removes_a_tag(db, uc, provider, monkeypatch, alerts):
    """Removals only come from tags a read just saw in UC: seeing less can never remove more."""
    monkeypatch.setattr(store, "purge_orphans", lambda db_, *, older_than_days: 0)
    uc.scope = ["c", "d"]
    uc.add("c")
    uc.add("c.s.untagged")
    uc.add("d")
    uc.add("d.s.t", {"a": "1", "stray": "1"})
    desired(db, "c.s.untagged", {"a": "1"})
    desired(db, "c.s.unseen", {"a": "1"})
    desired(db, "d.s.t", {"a": "1"})
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    assert reconciler.run(db, trigger="sweep", provider=provider).removals_planned == 1

    # d's read now fails, though its snapshot still holds the tag a good read would remove.
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "enforce")
    uc.unreadable = {"d"}
    swept = reconciler.run(db, trigger="sweep", provider=provider)
    targeted = reconciler.run(db, trigger="manual", fqn_keys=["d.s.t", "c.s.unseen"], provider=provider)

    for result in (swept, targeted):
        assert result.removals_planned == 0 and result.unread_catalogs == ["d"]
        assert all(i.operation != "unset" and i.fqn_key == "c.s.untagged" for i in result.items)
    assert provider.statements.executed == ["ALTER TABLE `c`.`s`.`untagged` SET TAGS ('a' = '1');"]
    assert observed.get_observed(db, ["d.s.t"])["d.s.t"].tags == {"a": "1", "stray": "1"}
    assert obj(db, "c.s.unseen").object_state == "orphaned"


def test_every_line_a_run_logs_carries_its_run_id(db, uc, provider, monkeypatch):
    from app.core.logging_formatter import current_request_id

    seen = []
    real_sweep = uc.sweep
    monkeypatch.setattr(observed, "sweep", lambda *a, **kw: seen.append(current_request_id.get()) or real_sweep(*a, **kw))

    result = reconciler.run(db, trigger="manual", catalogs=["c"], provider=provider)

    assert seen == [f"metadata_reconcile:{result.run_id}"]
    assert current_request_id.get() is None


def test_a_run_that_breaks_is_recorded_as_failed(db, uc, provider):
    uc.fail_refresh = "warehouse unreachable"
    desired(db, "c.s.t", {"a": "1"})

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert result.status == "failed" and "warehouse unreachable" in result.error
    assert db.get(MetadataReconcileRunModel, result.run_id).finished_at is not None


def test_mode_off_records_a_skipped_run(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "off")

    result = reconciler.run(db, trigger="manual", fqn_keys=["c.s.t"], provider=provider)

    assert result.status == "skipped" and uc.refreshed == []


# --- drain_queue ------------------------------------------------------------------------------


def test_drain_takes_due_rows_oldest_first_in_one_bulk_refresh(db, uc, provider, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    for i in range(5):
        uc.add(f"c.s.t{i}")
        reconciler.enqueue(db, [f"c.s.t{i}"])
        db.commit()
    reconciler.enqueue(db, ["c.s.later"])
    db.query(MetadataReconcileQueueModel).filter_by(fqn_key="c.s.later").update({"not_before": datetime(2099, 1, 1)})
    db.commit()

    result = reconciler.drain_queue(db, provider=provider, limit=2)

    assert result.trigger == "queue"
    assert uc.refreshed == [["c.s.t0", "c.s.t1"]]
    assert sorted(queue(db)) == ["c.s.later", "c.s.t2", "c.s.t3", "c.s.t4"]


def test_nothing_due_returns_none(db, uc, provider):
    reconciler.enqueue(db, ["c.s.t"])
    db.query(MetadataReconcileQueueModel).update({"not_before": datetime(2099, 1, 1)})
    db.commit()

    assert reconciler.drain_queue(db, provider=provider) is None
    assert db.query(MetadataReconcileRunModel).count() == 0


def test_failures_back_off_exponentially_and_converged_rows_leave(db, uc, provider):
    uc.add("c")
    uc.add("c.s.bad")
    uc.add("c.s.good")
    desired(db, "c.s.bad", {"a": "1"})
    desired(db, "c.s.good", {"a": "1"})
    provider.statements.failures = {"`bad`": "PERMISSION_DENIED"}
    reconciler.enqueue(db, ["c.s.bad", "c.s.good"])
    db.commit()

    before = reconciler._utcnow()
    reconciler.drain_queue(db, provider=provider)

    rows = queue(db)
    assert list(rows) == ["c.s.bad"]
    assert rows["c.s.bad"].attempts == 1 and "PERMISSION_DENIED" in rows["c.s.bad"].last_error
    assert timedelta(seconds=29) < rows["c.s.bad"].not_before - before < timedelta(seconds=40)

    make_due(db)
    before = reconciler._utcnow()
    reconciler.drain_queue(db, provider=provider)
    row = queue(db)["c.s.bad"]
    assert row.attempts == 2
    assert timedelta(seconds=59) < row.not_before - before < timedelta(seconds=70)
    assert reconciler._backoff_seconds(12) == 3600


def test_a_row_re_enqueued_during_the_run_stays_queued(db, uc, provider):
    uc.add("c")
    uc.add("c.s.t")
    desired(db, "c.s.t", {"a": "1"})
    provider.statements.failures = {"SET TAGS": "boom"}
    reconciler.enqueue(db, ["c.s.t"])
    db.commit()

    def edit(_sql):
        reconciler.enqueue(db, ["c.s.t"], reason="change")
        db.commit()

    provider.statements.on_execute = edit
    reconciler.drain_queue(db, provider=provider)

    row = queue(db)["c.s.t"]
    assert (row.attempts, row.last_error) == (0, None)


def test_the_fifth_failure_alerts_governance_once(db, uc, provider, alerts):
    uc.add("c")
    uc.add("c.s.t")
    desired(db, "c.s.t", {"a": "1"})
    provider.statements.failures = {"SET TAGS": "PERMISSION_DENIED"}
    reconciler.enqueue(db, ["c.s.t"])
    db.query(MetadataReconcileQueueModel).update({"attempts": 4})
    db.commit()

    reconciler.drain_queue(db, provider=provider)
    make_due(db)
    reconciler.drain_queue(db, provider=provider)

    assert queue(db)["c.s.t"].attempts == 6
    assert len(alerts) == 1 and any("c.s.t: " in line for line in alerts[0][1])


def test_an_unreadable_catalog_is_retried(db, uc, provider):
    uc.unreadable = {"d"}
    uc.add("c")
    uc.add("c.s.t")
    desired(db, "c.s.t", {"a": "1"})
    desired(db, "d.s.u", {"a": "1"})
    reconciler.enqueue(db, ["c.s.t", "d.s.u"])
    db.commit()

    result = reconciler.drain_queue(db, provider=provider)

    assert result.skipped_catalogs == ["d"]
    rows = queue(db)
    assert list(rows) == ["d.s.u"] and rows["d.s.u"].attempts == 1
    assert obj(db, "c.s.t").last_apply_status == "applied"


def test_a_paused_run_leaves_the_queue_alone(db, uc, provider):
    reconciler.trip_circuit_breaker(db, run_id="r0", removals=500, limit=200)
    reconciler.enqueue(db, ["c.s.t"])
    db.commit()

    result = reconciler.drain_queue(db, provider=provider)

    assert result.status == "paused"
    assert queue(db)["c.s.t"].attempts == 0


def test_a_broken_run_backs_off_every_row(db, uc, provider):
    uc.fail_refresh = "warehouse unreachable"
    reconciler.enqueue(db, ["c.s.a", "c.s.b"])
    db.commit()

    reconciler.drain_queue(db, provider=provider)

    assert {k: r.attempts for k, r in queue(db).items()} == {"c.s.a": 1, "c.s.b": 1}


# --- reconcile_task / the poller pass ---------------------------------------------------------


@pytest.fixture
def pass_env(db, uc, provider, monkeypatch):
    monkeypatch.setattr(reconciler, "_session", lambda: db)
    monkeypatch.setattr(reconciler, "_governance_provider", lambda: provider)
    monkeypatch.setattr(db, "close", lambda: None)
    return SimpleNamespace(db=db, uc=uc, provider=provider)


def test_reconcile_task_does_nothing_when_mode_is_off(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "off")
    monkeypatch.setattr(reconciler, "_session", lambda: pytest.fail("mode off must not open a session"))

    asyncio.run(reconciler.reconcile_task())


def test_reconcile_task_never_raises(monkeypatch, caplog):
    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr(reconciler, "_session", broken)
    with caplog.at_level(logging.ERROR, logger=reconciler.__name__):
        asyncio.run(reconciler.reconcile_task())
        asyncio.run(reconciler.reconcile_task())

    assert [r.getMessage() for r in caplog.records] == ["Metadata reconcile pass failed: no database"]


def test_a_pass_drains_batches_until_its_time_budget_runs_out(pass_env, monkeypatch):
    clock = [0.0]
    calls = []

    def fake_drain(db, *, provider=None, limit=reconciler._BATCH_SIZE):
        calls.append(limit)
        clock[0] += 20
        return SimpleNamespace(status="completed", error=None)

    monkeypatch.setattr(reconciler, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(reconciler, "drain_queue", fake_drain)
    reconciler.enqueue(pass_env.db, ["c.s.t"])
    pass_env.db.commit()

    reconciler._reconcile_pass(budget_seconds=50)

    assert calls == [reconciler._BATCH_SIZE] * 3


def test_a_pass_drains_a_large_queue_in_batches(pass_env, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "plan")
    keys = [f"c.s.t{i:03d}" for i in range(450)]
    for key in keys:
        pass_env.uc.add(key)
    reconciler.enqueue(pass_env.db, keys)
    pass_env.db.commit()

    reconciler._reconcile_pass(budget_seconds=600)

    assert queue(pass_env.db) == {}
    assert [len(r) for r in pass_env.uc.refreshed] == [200, 200, 50]
    assert pass_env.db.query(MetadataReconcileRunModel).filter_by(trigger="queue").count() == 3


def test_a_pass_waits_while_the_breaker_is_tripped(pass_env, monkeypatch, caplog):
    reconciler.trip_circuit_breaker(pass_env.db, run_id="r0", removals=500, limit=200)
    reconciler.enqueue(pass_env.db, ["c.s.t"])
    pass_env.db.commit()

    with caplog.at_level(logging.WARNING, logger=reconciler.__name__):
        reconciler._reconcile_pass()
        reconciler._reconcile_pass()

    assert pass_env.db.query(MetadataReconcileRunModel).count() == 0
    assert len([r for r in caplog.records if "circuit breaker" in r.getMessage()]) == 1


def test_a_pass_with_nothing_due_doesnt_connect(pass_env, monkeypatch):
    monkeypatch.setattr(reconciler, "_governance_provider", lambda: pytest.fail("nothing due, no connection"))

    reconciler._reconcile_pass()


def test_a_pass_runs_the_sweep_when_due(pass_env, monkeypatch):
    runs = []
    monkeypatch.setattr(reconciler, "_sweep_due", lambda: True)
    monkeypatch.setattr(reconciler, "run", lambda db, **kw: runs.append(kw))

    reconciler._reconcile_pass()

    assert runs == [{"trigger": "sweep", "provider": pass_env.provider}]


def test_the_poll_queues_only_relations_that_need_work(pass_env, monkeypatch):
    db, uc = pass_env.db, pass_env.uc
    monkeypatch.setattr(settings, "METADATA_NEW_OBJECT_POLL_SECONDS", 60)
    watermark = datetime(2026, 10, 20, 11, 0)
    db.add(MetadataSyncStateModel(key=observed.NEW_OBJECT_WATERMARK_KEY, value={"at": watermark.isoformat()}))
    db.commit()
    uc.add("c.s.in_sync", {"a": "1"})
    uc.add("c.s.drifted", {"a": "2"})
    uc.add("c.s.new", created=watermark + timedelta(minutes=5))
    uc.add("c.s.old_altered", created=watermark - timedelta(days=5))
    desired(db, "c.s.in_sync", {"a": "1"})
    desired(db, "c.s.drifted", {"a": "1"})
    desired(db, "c.s.appeared", state="pending")
    uc.add("c.s.appeared")
    uc.polled = ["c.s.appeared", "c.s.drifted", "c.s.in_sync", "c.s.new", "c.s.old_altered"]
    monkeypatch.setattr(reconciler, "drain_queue", lambda db_, **kw: None)

    reconciler._reconcile_pass()

    rows = queue(db)
    assert sorted(rows) == ["c.s.appeared", "c.s.drifted", "c.s.new"]
    assert {r.reason for r in rows.values()} == {"new_object"}

    reconciler._reconcile_pass()
    assert len(queue(db)) == 3


def test_the_poll_with_ignore_policy_queues_no_unmanaged_relations(pass_env, monkeypatch):
    db, uc = pass_env.db, pass_env.uc
    monkeypatch.setattr(settings, "METADATA_NEW_OBJECT_POLL_SECONDS", 60)
    monkeypatch.setattr(settings, "METADATA_NEW_OBJECT_POLICY", "ignore")
    db.add(MetadataSyncStateModel(key=observed.NEW_OBJECT_WATERMARK_KEY, value={"at": "2026-10-20T11:00:00"}))
    db.commit()
    uc.add("c.s.new", created=datetime(2026, 10, 20, 11, 5))
    uc.polled = ["c.s.new"]

    reconciler._reconcile_pass()

    assert queue(db) == {}
