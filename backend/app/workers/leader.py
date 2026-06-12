"""Cluster-wide poller leader election.

In production the API runs as multiple replicas, and each one starts the
background poller thread. The per-request advisory lock (``state_machines.lock``)
keeps processing *correct* — no request is double-processed and mutating tools
are idempotency-keyed — but every replica still wakes up on its interval, runs
the selection query, and races to claim the same rows. That is a thundering herd
whose DB load scales with replica count, and it lets the report/sentinel cron
spawns fire from several replicas at once.

This module elects a single active poller cluster-wide using a Postgres
*session-level* advisory lock (``pg_try_advisory_lock``). Only the lock holder
processes work; the others idle and re-attempt every cycle, so leadership fails
over automatically when the leader dies (its connection drops and Postgres
releases the advisory lock).

On SQLite (local dev / single process) there is exactly one poller, so the
elector always reports leadership and never touches the (unsupported) advisory
lock API.
"""
from __future__ import annotations

import logging

from app.db.session import get_engine

logger = logging.getLogger(__name__)

# Stable, arbitrary 64-bit key namespacing *this app's* poller leadership.
# Advisory locks share a global key space per database, so the value just needs
# to be unique to this concern.
_ADVISORY_LOCK_KEY = 8273401273


class PollerLeaderElector:
    """Tracks whether this process currently holds poller leadership.

    Holds a dedicated raw DBAPI connection for the lifetime of the lock. The
    connection is intentionally never returned to the pool: the advisory lock is
    bound to its session and is released the moment that session ends (graceful
    shutdown or process death), which is exactly the failover semantics we want.
    """

    def __init__(self) -> None:
        self._conn = None  # dedicated connection holding the advisory lock
        self._is_leader = False

    # -- public API ---------------------------------------------------------
    def is_leader(self) -> bool:
        """Whether this process should run the poll cycle right now."""
        if not self._engine_is_postgres():
            return True  # single-process SQLite dev: always the leader
        if self._is_leader and self._conn is not None and self._connection_alive():
            return True
        # Lost / never had the lock (or the held connection died): (re)attempt.
        self._reset()
        return self._try_acquire()

    def release(self) -> None:
        """Drop leadership (used on shutdown)."""
        if self._conn is not None:
            try:
                cur = self._conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
                cur.close()
            except Exception:  # noqa: BLE001 - best effort; closing also releases it
                pass
        self._reset()

    # -- internals ----------------------------------------------------------
    def _engine_is_postgres(self) -> bool:
        try:
            return get_engine().dialect.name == "postgresql"
        except Exception:  # noqa: BLE001 - if we can't tell, don't gate the poller
            return False

    def _connection_alive(self) -> bool:
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return True
        except Exception:  # noqa: BLE001 - dead/stale connection
            return False

    def _try_acquire(self) -> bool:
        try:
            raw = get_engine().raw_connection()
            cur = raw.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
            got = bool(cur.fetchone()[0])
            cur.close()
            if got:
                self._conn = raw  # keep it open: holds the session lock
                self._is_leader = True
                logger.info(
                    "Poller leadership ACQUIRED (advisory lock %s).", _ADVISORY_LOCK_KEY
                )
                return True
            raw.close()  # return to pool; another replica is the leader
            self._is_leader = False
            return False
        except Exception as e:  # noqa: BLE001 - never crash the poller on election
            logger.warning("Poller leader election attempt failed: %s", e)
            self._reset()
            return False

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None
        self._is_leader = False
