"""Executing planned statements against UC through the governance SP.

A TABLE/VIEW mix-up is retried with the other keyword, and an UNSET of a tag that's
already gone is a no-op. A refused SET of several tags is retried one tag at a time,
so one rejected key doesn't hold back the rest; a refused statement doesn't hold
back the object's other statements either, unless the object itself is missing or
the warehouse timed out. A view column changes a value with an unset then a set; if
the set is refused, the old value is put back.

Statements run synchronously through ``provider.client.statement_execution`` (the
Databricks SDK), so call ``execute`` from a worker thread, not the event loop. With
``workers`` > 1, different relations run in parallel; one relation's statements (its
own and its columns') always run in order.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.core.config import settings
from app.services.metadata.sql import alter_keyword, build_tag_statement
from app.services.metadata.types import COLUMN_SEPARATOR, PlanItem, PlanOutcome

logger = logging.getLogger(__name__)

_WAIT_TIMEOUT = "30s"
# A cold warehouse can take minutes to start; past this a statement is cancelled and failed.
_MAX_WAIT_SECONDS = 300
_POLL_SECONDS = 2.0
_RUNNING_STATES = ("PENDING", "RUNNING")
_FAILED_STATES = ("FAILED", "CANCELED", "CLOSED")
_MAX_DETAIL = 2000
# Failed statements logged one by one per ``execute`` call; the rest are counted in one line.
_MAX_FAILURE_LOGS = 20

# WRONG_COMMAND_FOR_OBJECT_TYPE / EXPECT_*_NOT_* name the object's real type:
# "... But `c`.`s`.`v` is a VIEW. Use ALTER VIEW instead."
_ACTUAL_TYPE = re.compile(r"\bis an? (materialized[ _]view|streaming[ _]table|metric[ _]view|view|table)\b")
_VIEW_HINTS = ("expect_table_not_view", "not a table")
_TABLE_HINTS = ("expect_view_not_table", "not a view")
_RETRYABLE_TYPES = ("table", "view", "metric_view", "materialized_view", "streaming_table")

_MISSING_TAG_CODES = ("tag_not_found", "no_such_tag", "tag_does_not_exist")
_MISSING_TAG_PHRASES = ("not found", "does not exist", "cannot be found", "no such")
# A missing table, schema, catalog or column also reads "not found", and the error
# echoes the statement (which says TAGS); those must never pass as a missing tag.
_MISSING_OBJECT_CODES = (
    "table_or_view_not_found", "schema_not_found", "catalog_not_found", "no_such_catalog",
    "unresolved_column", "column_not_found", "field_not_found",
)
# Errors that mean nothing else will work on this object right now: its later statements are skipped.
_OBJECT_ERRORS = _MISSING_OBJECT_CODES + ("timed out", "sql warehouse")


class StatementFailed(Exception):
    """UC refused or didn't finish a statement; the message is UC's."""


def _core(message: str) -> str:
    """The error text without the echoed statement (``== SQL == ...``), lowercased."""
    return (message or "").split("== SQL", 1)[0].lower()


def _actual_relation_type(message: str, current: str) -> Optional[str]:
    core = _core(message)
    found = _ACTUAL_TYPE.search(core)
    if found:
        return found.group(1).replace(" ", "_")
    if any(h in core for h in _VIEW_HINTS):
        return "view"
    if any(h in core for h in _TABLE_HINTS):
        return "table"
    if "wrong_command_for_object_type" in core:
        return "table" if current in ("view", "metric_view") else "view"
    return None


def _retry_type(item: PlanItem, message: str) -> Optional[str]:
    """The relation type to retry a relation-level tag statement as, or None.

    Column statements aren't retried: a view column takes a different statement shape
    (one tag at a time, no overwrite), and comments use forms every relation accepts.
    """
    if item.column is not None or item.operation not in ("set", "unset") or item.securable_type not in _RETRYABLE_TYPES:
        return None
    actual = _actual_relation_type(message, item.securable_type)
    if actual is None or alter_keyword(actual) == alter_keyword(item.securable_type):
        return None
    return actual


def _is_missing_tag_error(message: str) -> bool:
    core = _core(message)
    if any(code in core for code in _MISSING_OBJECT_CODES):
        return False
    if any(code in core for code in _MISSING_TAG_CODES):
        return True
    return "tag" in core and any(p in core for p in _MISSING_TAG_PHRASES)


def _warehouse_id(provider) -> Optional[str]:
    getter = getattr(provider, "get_config", None)
    configured = None
    if callable(getter):
        try:
            configured = getter("warehouse_id")
        except Exception:  # noqa: BLE001 - fall back to the app's warehouse
            configured = None
    return configured or settings.DATABRICKS_WAREHOUSE_ID


def _state(resp) -> str:
    state = getattr(getattr(resp, "status", None), "state", None)
    return str(getattr(state, "value", state) or "").upper()


def _error_text(resp, state: str) -> str:
    error = getattr(getattr(resp, "status", None), "error", None)
    message = getattr(error, "message", None)
    return str(message) if message else f"The statement ended {state.lower()}."


def run_statement(provider, sql: str) -> None:
    """Run one statement, waiting for a slow warehouse; raises ``StatementFailed``."""
    executor = provider.client.statement_execution
    resp = executor.execute_statement(statement=sql, warehouse_id=_warehouse_id(provider), wait_timeout=_WAIT_TIMEOUT)
    state = _state(resp)
    statement_id = getattr(resp, "statement_id", None)
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    while state in _RUNNING_STATES and statement_id:
        if time.monotonic() >= deadline:
            try:
                executor.cancel_execution(statement_id=statement_id)
            except Exception:  # noqa: BLE001 - the timeout is what gets reported
                logger.debug("Couldn't cancel statement %s", statement_id, exc_info=True)
            raise StatementFailed(f"Timed out after {_MAX_WAIT_SECONDS}s waiting for the SQL warehouse.")
        time.sleep(_POLL_SECONDS)
        resp = executor.get_statement(statement_id=statement_id)
        state = _state(resp)
    if state in _RUNNING_STATES:
        raise StatementFailed("The SQL warehouse didn't report the statement as finished.")
    if state in _FAILED_STATES:
        raise StatementFailed(_error_text(resp, state))


def _message(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL] + "..."


def _outcome(item: PlanItem, status: str, detail: Optional[str] = None, **changes) -> PlanOutcome:
    data = item.model_dump()
    data.update(changes, status=status, detail=detail)
    return PlanOutcome(**data)


class _FailureLog:
    """Warns about the first few failed statements and counts the rest (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0

    def failed(self, item: PlanItem, error: str) -> None:
        with self._lock:
            self.count += 1
            count = self.count
        if count <= _MAX_FAILURE_LOGS:
            logger.warning("Metadata reconcile statement failed on %s (%s): %s", item.fqn_key, item.operation, error)

    def summarize(self) -> None:
        if self.count > _MAX_FAILURE_LOGS:
            logger.warning("Metadata reconcile: %d more failed statements weren't logged one by one (%d in all).",
                           self.count - _MAX_FAILURE_LOGS, self.count)


def _execute_one(provider, item: PlanItem, failures: Optional[_FailureLog] = None) -> PlanOutcome:
    try:
        run_statement(provider, item.sql)
        return _outcome(item, "applied")
    except Exception as exc:  # noqa: BLE001 - every failure becomes an outcome
        error = _message(exc)

    last_error = error
    retry = _retry_type(item, error)
    if retry is not None:
        keyword = alter_keyword(retry)
        retry_sql = build_tag_statement(retry, item.name, item.operation, tags=item.tags, keys=item.keys)[0]
        try:
            run_statement(provider, retry_sql)
            logger.debug("Applied %s %s as ALTER %s after a type mix-up", item.operation, item.fqn_key, keyword)
            return _outcome(item, "applied", f"Applied as ALTER {keyword}.", sql=retry_sql, securable_type=retry)
        except Exception as exc:  # noqa: BLE001
            last_error = _message(exc)
            error = f"{error} Retried as ALTER {keyword}: {last_error}"

    if item.operation == "unset" and _is_missing_tag_error(last_error):
        return _outcome(item, "noop", "The tag was already gone.")
    (failures or _FailureLog()).failed(item, error)
    return _outcome(item, "failed", error)


def _run_item(provider, item: PlanItem, failures: _FailureLog) -> PlanOutcome:
    if not item.sql.strip():
        return _outcome(item, "noop", "Nothing to run.")
    try:
        return _execute_one(provider, item, failures)
    except Exception as exc:  # noqa: BLE001 - a bug here still yields one outcome per item
        failures.failed(item, _message(exc))
        return _outcome(item, "failed", _message(exc))


def not_run(item: PlanItem, reason: Optional[str]) -> PlanOutcome:
    return _outcome(item, "skipped", f"Not applied: {reason}")


def _blocks_object(error: Optional[str]) -> bool:
    core = _core(error or "")
    return any(code in core for code in _OBJECT_ERRORS)


def _tag_item(item: PlanItem, tags: Dict[str, Optional[str]]) -> Optional[PlanItem]:
    """``item`` narrowed to setting ``tags``; None when its statement can't be rebuilt."""
    if not item.statement_type:
        return None
    try:
        sql = build_tag_statement(item.statement_type, item.name, "set", tags=tags, column=item.column)[0]
    except (ValueError, IndexError):
        return None
    blanks = [k for k in item.blanks if k in tags]
    return item.model_copy(update={"tags": dict(tags), "sql": sql, "blanks": blanks, "removals": len(blanks),
                                   "restore": None})


def _one_key_at_a_time(provider, item: PlanItem, refused: PlanOutcome, failures: _FailureLog) -> List[PlanOutcome]:
    """A refused SET of several tags, retried per tag so the accepted ones still land."""
    parts = [_tag_item(item, {key: value}) for key, value in item.tags.items()]
    if any(part is None for part in parts):
        return [refused]
    logger.info("Metadata reconcile: SET TAGS on %s was refused; retrying its %d tags one at a time.",
                item.fqn_key, len(parts))
    return [_run_item(provider, part, failures) for part in parts]  # type: ignore[arg-type]


def _put_back(provider, item: PlanItem, refused: PlanOutcome, failures: _FailureLog) -> List[PlanOutcome]:
    """A view column's new value was refused after its old one was removed: set the old one again."""
    undo = _tag_item(item, item.restore or {})
    if undo is None:
        return [refused]
    put = _run_item(provider, undo, failures)
    if put.status == "applied":
        return [refused, put.model_copy(update={"detail": "Put the previous value back after the new one was refused."})]
    detail = f"{refused.detail} Putting the previous value back also failed, so the tag is missing until the next run: {put.detail}"
    return [refused.model_copy(update={"detail": detail})]


def execute(
    provider, items: Sequence[PlanItem], *, workers: int = 1, stop: Optional[Callable[[], Optional[str]]] = None,
) -> List[PlanOutcome]:
    """Run the items' statements and return one outcome per item, in the order given.

    ``applied`` / ``noop`` (UNSET of a tag already gone) / ``failed`` (UC's error in
    ``detail``) / ``skipped`` (the object is missing or the warehouse timed out on an
    earlier statement, a view column's old value couldn't be removed, or ``stop`` gave a
    reason to stop before the relation started). Never raises for a statement failure.
    A refused multi-tag SET comes back as one outcome per tag, and a put-back value as
    an extra ``set`` outcome after the refused one.

    Each relation's statements (its own and its columns', in the order given) run one
    after another, with up to ``workers`` relations in parallel.
    """
    items = list(items)
    outcomes: List[List[PlanOutcome]] = [[] for _ in items]
    failures = _FailureLog()
    groups: Dict[str, List[int]] = {}
    for index, item in enumerate(items):
        groups.setdefault(item.fqn_key.partition(COLUMN_SEPARATOR)[0], []).append(index)

    def run_group(indices: List[int]) -> None:
        reason = stop() if stop is not None else None
        if reason:
            for index in indices:
                outcomes[index] = [not_run(items[index], reason)]
            return
        blocked: Set[str] = set()
        # (fqn_key, tag key) unset by this run, and the subset it actually removed (not already gone).
        cleared: Set[Tuple[str, str]] = set()
        removed: Set[Tuple[str, str]] = set()
        for index in indices:
            item = items[index]
            if item.fqn_key in blocked:
                outcomes[index] = [_outcome(item, "skipped", "Skipped: an earlier statement for this object failed.")]
                continue
            if item.restore and not all((item.fqn_key, k) in cleared for k in item.restore):
                outcomes[index] = [_outcome(item, "skipped", "Skipped: the old value couldn't be removed first.")]
                continue
            outcome = _run_item(provider, item, failures)
            done = [outcome]
            if outcome.status == "failed" and _blocks_object(outcome.detail):
                blocked.add(item.fqn_key)
            elif outcome.status == "failed" and item.operation == "set" and len(item.tags) > 1:
                done = _one_key_at_a_time(provider, item, outcome, failures)
            elif outcome.status == "failed" and item.restore and all((item.fqn_key, k) in removed for k in item.restore):
                done = _put_back(provider, item, outcome, failures)
            if item.operation == "unset" and outcome.status in ("applied", "noop"):
                cleared.update((item.fqn_key, k) for k in item.keys)
                if outcome.status == "applied":
                    removed.update((item.fqn_key, k) for k in item.keys)
            outcomes[index] = done

    batches = list(groups.values())
    workers = max(1, min(int(workers or 1), len(batches) or 1))
    if workers == 1:
        for batch in batches:
            run_group(batch)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="metadata-apply") as pool:
            list(pool.map(run_group, batches))
    failures.summarize()
    return [o for done in outcomes for o in done]
