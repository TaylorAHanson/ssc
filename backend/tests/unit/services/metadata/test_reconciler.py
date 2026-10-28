"""Reconciler phase 1: modes, circuit breaker, runs, single flight and the sweep cron."""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.metadata import (
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataReconcileItemModel,
    MetadataReconcileQueueModel,
    MetadataReconcileRunModel,
)
from app.services.metadata import errors, reconciler
from app.services.metadata.types import PlanOutcome, ReconcileResult

@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(reconciler, "_next_sweep_time", None)
    monkeypatch.setattr(reconciler, "_bad_cron", None)
    monkeypatch.setattr(reconciler, "_bad_mode", None)


def outcome(fqn_key, operation, status, *, name=None, column=None, securable_type="table", removals=0, **kw):
    return PlanOutcome(
        fqn_key=fqn_key, name=name or fqn_key.split("::")[0], column=column, securable_type=securable_type,
        operation=operation, sql=f"-- {operation} {fqn_key}", status=status, removals=removals, **kw,
    )


# --- Mode ----------------------------------------------------------------------


@pytest.mark.parametrize("raw,mode", [
    ("off", "off"), ("plan", "plan"), ("enforce", "enforce"), (" Enforce ", "enforce"), ("", "plan"), (None, "plan"),
])
def test_current_mode(monkeypatch, raw, mode):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", raw)
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["sales_dev"])

    assert reconciler.current_mode() == mode


def test_enforce_needs_a_catalog_list_and_says_so_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "enforce")
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [])
    monkeypatch.setattr(reconciler, "_bad_mode", None)

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.reconciler"):
        assert reconciler.current_mode() == "plan"
        assert reconciler.current_mode() == "plan"

    assert sum("METADATA_RECONCILE_CATALOGS" in r.getMessage() for r in caplog.records) == 1


def test_an_unknown_mode_falls_back_to_plan_and_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", "apply")

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.reconciler"):
        assert reconciler.current_mode() == "plan"
        assert reconciler.current_mode() == "plan"

    assert sum("METADATA_RECONCILE_MODE" in r.getMessage() for r in caplog.records) == 1


def test_re_enqueue_makes_a_backing_off_row_due_with_a_fresh_attempt_count(db_session):
    reconciler.enqueue(db_session, ["c.s.t"])
    row = db_session.query(MetadataReconcileQueueModel).one()
    row.attempts, row.last_error = 4, "boom"
    row.not_before = datetime(2099, 1, 1)
    db_session.flush()

    reconciler.enqueue(db_session, ["c.s.t::col"], reason="notify")
    db_session.flush()

    db_session.refresh(row)
    assert (row.attempts, row.last_error, row.reason) == (0, None, "notify")
    assert row.not_before < datetime(2099, 1, 1)


# --- Circuit breaker ----------------------------------------------------------------------


def test_circuit_breaker_trips_and_clears(db_session):
    assert reconciler.circuit_breaker_tripped(db_session) is False
    assert reconciler.clear_circuit_breaker(db_session) is False

    reconciler.trip_circuit_breaker(db_session, run_id="run-1", removals=250, limit=200)
    db_session.commit()

    assert reconciler.circuit_breaker_tripped(db_session) is True
    state = reconciler.circuit_breaker_state(db_session)
    assert (state["run_id"], state["removals"], state["limit"]) == ("run-1", 250, 200)
    assert "250" in state["reason"]

    assert reconciler.clear_circuit_breaker(db_session, cleared_by="admin@x.com") is True
    db_session.commit()

    assert reconciler.circuit_breaker_tripped(db_session) is False
    assert reconciler.circuit_breaker_state(db_session) is None


# --- Runs ---------------------------------------------------------------------------------


def _seed_run(db, run_id, *, minutes_ago, trigger="sweep", status="completed", mode="plan"):
    db.add(MetadataReconcileRunModel(
        id=run_id, trigger=trigger, mode=mode, status=status,
        started_at=datetime(2026, 10, 20, 12, 0) - timedelta(minutes=minutes_ago), skipped_catalogs=["other"],
        statements_planned=3, removals_planned=1,
    ))


def test_list_runs_is_newest_first_paged_and_filtered(db_session):
    _seed_run(db_session, "old", minutes_ago=30, trigger="queue")
    _seed_run(db_session, "new", minutes_ago=1, trigger="sweep", status="paused")
    _seed_run(db_session, "mid", minutes_ago=10, trigger="queue", status="failed")
    db_session.flush()

    page = reconciler.list_runs(db_session)
    assert [r.run_id for r in page.items] == ["new", "mid", "old"]
    assert page.total == 3
    assert page.items[0].skipped_catalogs == ["other"] and page.items[0].items == []

    second = reconciler.list_runs(db_session, page=2, page_size=2)
    assert [r.run_id for r in second.items] == ["old"] and (second.page, second.page_size, second.total) == (2, 2, 3)

    assert [r.run_id for r in reconciler.list_runs(db_session, trigger="queue").items] == ["mid", "old"]
    assert [r.run_id for r in reconciler.list_runs(db_session, status="paused").items] == ["new"]


def test_list_runs_clamps_paging(db_session):
    page = reconciler.list_runs(db_session, page=0, page_size=10_000)

    assert (page.page, page.page_size) == (1, 500)


def test_get_run_returns_its_statements_typed_from_the_snapshot(db_session):
    """An older run's catalog-claim statement reads as a set."""
    _seed_run(db_session, "r1", minutes_ago=0)
    db_session.add(MetadataObjectModel(
        id="o1", fqn_key="c.s.v", securable_type="table", catalog="c", display_name="c.s.v",
    ))
    db_session.add(MetadataObservedModel(fqn_key="c.s.v", securable_type="view", catalog="c", display_name="c.s.v"))
    db_session.add(MetadataObjectModel(
        id="o2", fqn_key="c.s.mv", securable_type="materialized_view", catalog="c", display_name="c.s.mv",
    ))
    for fqn_key, operation, status in [
        ("c", "claim", "applied"), ("c.s", "set", "applied"), ("c.s.v", "unset", "failed"),
        ("c.s.v::Col", "set", "skipped"), ("c.s.mv", "comment", "planned"), ("c.s.t", "set", "noop"),
    ]:
        db_session.add(MetadataReconcileItemModel(
            run_id="r1", fqn_key=fqn_key, operation=operation, sql=f"-- {fqn_key}", status=status, detail="d",
        ))
    db_session.flush()

    result = reconciler.get_run(db_session, "r1")

    assert [(i.fqn_key, i.securable_type, i.name, i.column, i.operation, i.status) for i in result.items] == [
        ("c", "catalog", "c", None, "set", "applied"),
        ("c.s", "schema", "c.s", None, "set", "applied"),
        ("c.s.v", "view", "c.s.v", None, "unset", "failed"),
        ("c.s.v::Col", "column", "c.s.v", "Col", "set", "skipped"),
        ("c.s.mv", "materialized_view", "c.s.mv", None, "comment", "planned"),
        ("c.s.t", "table", "c.s.t", None, "set", "noop"),
    ]
    assert result.items[0].sql == "-- c" and result.items[0].detail == "d"


def test_get_run_of_an_unknown_id_is_not_found(db_session):
    with pytest.raises(errors.NotFound):
        reconciler.get_run(db_session, "nope")


def test_start_and_finish_run_record_counters_and_items(db_session):
    run_id = reconciler.start_run(db_session, trigger="manual", mode="enforce", actor="admin@x.com")
    assert reconciler.get_run(db_session, run_id).status == "running"

    outcomes = [
        outcome("c.s.t", "unset", "applied", keys=["a", "b"], removals=2),
        outcome("c.s.t", "set", "failed", tags={"x": "1"}, detail="denied"),
        outcome("c.s.u", "comment", "noop", comment="hi"),
        outcome("c.s.t", "comment", "skipped"),
    ]
    result = reconciler.finish_run(
        db_session, run_id, status=reconciler.run_status(outcomes, mode="enforce"), outcomes=outcomes,
        objects_checked=5, objects_drifted=2, skipped_catalogs=["b", "a", "b"], summary={"scope": "manual"},
    )

    assert result.status == "partial"
    assert (result.statements_planned, result.statements_applied, result.statements_failed) == (4, 1, 1)
    assert (result.removals_planned, result.objects_checked, result.objects_drifted) == (2, 5, 2)
    assert result.skipped_catalogs == ["a", "b"]
    assert result.items[0].keys == ["a", "b"] and result.finished_at is not None

    stored = reconciler.get_run(db_session, run_id)
    assert [(i.operation, i.status, i.detail) for i in stored.items] == [
        ("unset", "applied", None), ("set", "failed", "denied"), ("comment", "noop", None), ("comment", "skipped", None),
    ]
    assert [(i.keys, i.tags, i.comment, i.removals) for i in stored.items[:3]] == [
        (["a", "b"], {}, None, 2), ([], {"x": "1"}, None, 0), ([], {}, "hi", 0),
    ]
    assert stored.statements_applied == 1 and stored.actor == "admin@x.com"
    assert db_session.get(MetadataReconcileRunModel, run_id).summary == {"scope": "manual"}


def test_a_run_whose_process_is_gone_or_that_is_very_old_is_closed_when_the_next_starts(db_session, monkeypatch):
    """A restart mid-run leaves it ``running``; the next run closes it, but not one another process still owns."""
    monkeypatch.setattr(reconciler.socket, "gethostname", lambda: "host-a")
    monkeypatch.setattr(reconciler.os, "getpid", lambda: 200)
    alive = {200, 300}

    def kill(pid, sig):
        if pid not in alive:
            raise ProcessLookupError(pid)

    monkeypatch.setattr(reconciler.os, "kill", kill)
    now = reconciler._utcnow()

    def running(worker, age=timedelta(minutes=5)):
        run = MetadataReconcileRunModel(id=worker, trigger="sweep", mode="plan", status="running", skipped_catalogs=[],
                                        summary={"scope": "all", "worker": worker}, started_at=now - age)
        db_session.add(run)
        return run.id

    dead_here = running("host-a:100")
    live_here = running("host-a:300")
    other_host = running("host-b:100")
    ancient = running("host-b:101", age=timedelta(hours=7))
    db_session.commit()

    new_id = reconciler.start_run(db_session, trigger="queue", mode="plan")

    runs = {r.id: r for r in db_session.query(MetadataReconcileRunModel)}
    for run_id in (dead_here, ancient):
        assert runs[run_id].status == "failed" and runs[run_id].finished_at is not None
        assert "restarted" in runs[run_id].error and runs[run_id].summary == {"scope": "all"}
    assert runs[live_here].status == runs[other_host].status == "running"
    assert runs[new_id].summary == {"worker": "host-a:200"}

    reconciler.finish_run(db_session, new_id, status="completed")
    assert db_session.get(MetadataReconcileRunModel, new_id).summary == {}


def test_record_run_writes_a_finished_run_in_one_go(db_session):
    result = reconciler.record_run(
        db_session, trigger="sweep", mode="enforce", status="paused", paused_reason="Too many removals.",
        outcomes=[outcome("c.s.t", "unset", "planned", keys=["a"], removals=1)],
    )

    stored = reconciler.get_run(db_session, result.run_id)
    assert (stored.status, stored.paused_reason, stored.removals_planned) == ("paused", "Too many removals.", 1)


def test_finish_run_of_an_unknown_id_is_not_found(db_session):
    with pytest.raises(errors.NotFound):
        reconciler.finish_run(db_session, "nope", status="completed")


@pytest.mark.parametrize("mode,statuses,expected", [
    ("plan", ["planned", "planned"], "completed"),
    ("enforce", ["applied", "noop"], "completed"),
    ("enforce", [], "completed"),
    ("enforce", ["applied", "failed", "skipped"], "partial"),
    ("enforce", ["failed", "skipped"], "failed"),
])
def test_run_status(mode, statuses, expected):
    assert reconciler.run_status([outcome("c.s.t", "set", s) for s in statuses], mode=mode) == expected


def test_recent_outcomes_cover_the_object_and_its_columns_only(db_session):
    _seed_run(db_session, "r1", minutes_ago=0)
    for fqn_key in ["s_x.b.t", "s_x.b.t::a", "sxx.b.t::a", "s_x.b.t2", "s_x.b.t::b"]:
        db_session.add(MetadataReconcileItemModel(run_id="r1", fqn_key=fqn_key, operation="set", sql="--", status="planned"))
    db_session.flush()

    found = reconciler.recent_outcomes(db_session, "s_x.b.t")

    assert [o.fqn_key for o in found] == ["s_x.b.t::b", "s_x.b.t::a", "s_x.b.t"]
    assert [o.fqn_key for o in reconciler.recent_outcomes(db_session, "s_x.b.t", limit=1)] == ["s_x.b.t::b"]


# --- Single flight --------------------------------------------------------------------------


def test_single_flight_is_a_no_op_on_sqlite(db_session):
    with reconciler._single_flight(db_session):
        with reconciler._single_flight(db_session):
            pass


class _FakeConn:
    def __init__(self, got):
        self.got = got
        self.statements = []
        self.closed = False

    def execute(self, statement, params):
        self.statements.append((str(statement), params["key"]))
        return SimpleNamespace(scalar=lambda: self.got)

    def commit(self):
        pass

    def close(self):
        self.closed = True

    def invalidate(self):
        pass


def _postgres_session(conn):
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), engine=SimpleNamespace(connect=lambda: conn))
    return SimpleNamespace(get_bind=lambda: bind)


def test_single_flight_holds_a_postgres_advisory_lock_for_the_run():
    conn = _FakeConn(got=True)

    with reconciler._single_flight(_postgres_session(conn)):
        assert conn.statements == [("SELECT pg_try_advisory_lock(:key)", reconciler._ADVISORY_LOCK_KEY)]

    assert conn.statements[-1] == ("SELECT pg_advisory_unlock(:key)", reconciler._ADVISORY_LOCK_KEY)
    assert conn.closed


def test_single_flight_refuses_when_another_run_holds_the_lock():
    conn = _FakeConn(got=False)

    with pytest.raises(reconciler.ReconcileBusy, match="already running"):
        with reconciler._single_flight(_postgres_session(conn)):
            pytest.fail("must not run without the lock")

    assert len(conn.statements) == 1 and conn.closed


def test_run_detached_returns_the_running_row_when_the_sweep_is_slow(monkeypatch):
    import threading

    release = threading.Event()

    def slow(db, **kw):
        release.wait(timeout=5)
        return ReconcileResult(run_id="done", trigger="manual", mode="plan", status="completed", actor=kw.get("actor"))

    monkeypatch.setattr(reconciler, "run", slow)
    monkeypatch.setattr(reconciler, "latest_running", lambda db, actor=None: ReconcileResult(
        run_id="live", trigger="manual", mode="plan", status="running", actor=actor,
    ))

    def factory():
        return SimpleNamespace(close=lambda: None)

    started = datetime.now(timezone.utc)
    result = reconciler.run_detached(trigger="manual", actor="admin@x.com", wait_seconds=0.4, session_factory=factory)
    release.set()
    assert (result.run_id, result.status) == ("live", "running")
    assert (datetime.now(timezone.utc) - started).total_seconds() < 2


# --- Sweep schedule -------------------------------------------------------------------------

T0 = datetime(2026, 10, 20, 12, 5, tzinfo=timezone.utc)


def test_a_blank_cron_never_sweeps(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "  ")

    assert reconciler._sweep_due(T0) is False
    assert reconciler._next_sweep_time is None


def test_the_sweep_is_due_once_per_cron_tick_and_cached(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "30 * * * *")

    assert reconciler._sweep_due(T0) is False
    assert reconciler._next_sweep_time == datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)
    assert reconciler._sweep_due(T0 + timedelta(minutes=10)) is False

    tick = datetime(2026, 10, 20, 12, 30, 2, tzinfo=timezone.utc)
    assert reconciler._sweep_due(tick) is True
    assert reconciler._next_sweep_time == datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)
    assert reconciler._sweep_due(tick + timedelta(seconds=5)) is False


def test_a_naive_now_is_read_as_utc(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "30 * * * *")
    reconciler._sweep_due(T0)

    assert reconciler._sweep_due(datetime(2026, 10, 20, 12, 31)) is True


def test_a_changed_cron_applies_after_settings_store_resets_the_cache(monkeypatch):
    from app.core import settings_store

    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "0 0 * * *")
    reconciler._sweep_due(T0)
    assert reconciler._next_sweep_time == datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "*/10 * * * *")
    settings_store._reset_cron_schedule("METADATA_RECONCILE_CRON")
    assert reconciler._next_sweep_time is None

    assert reconciler._sweep_due(T0) is False
    assert reconciler._next_sweep_time == datetime(2026, 10, 20, 12, 10, tzinfo=timezone.utc)
    assert reconciler._sweep_due(T0 + timedelta(minutes=5)) is True


def test_an_invalid_cron_never_sweeps_and_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CRON", "every hour")

    with caplog.at_level(logging.ERROR, logger="app.services.metadata.reconciler"):
        assert reconciler._sweep_due(T0) is False
        assert reconciler._sweep_due(T0 + timedelta(hours=2)) is False

    assert sum("METADATA_RECONCILE_CRON" in r.getMessage() for r in caplog.records) == 1
