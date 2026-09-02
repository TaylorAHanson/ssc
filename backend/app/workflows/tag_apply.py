"""
Local Tag Application Engine.

Applies planned tag changes directly to Unity Catalog via Databricks SQL execution,
handling VIEW vs TABLE rewrites, idempotent UNSETs, and optional ledger recording.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.workflows.tag_plan import StatementPlan, TagPlan
from app.workflows.tag_sql import _escape_sql_literal

logger = logging.getLogger(__name__)

APPLIED = "applied"
NOOP = "noop"
FAILED = "failed"
PARTIAL = "partial"

_VIEW_MISMATCH_PATTERNS = (
    "expect_table_not_view",
    "wrong_command_for_object_type",
    "is a view",
    "not a table",
)
_TABLE_MISMATCH_PATTERNS = (
    "expect_view_not_table",
    "is a table",
    "not a view",
)
_MISSING_TAG_CODES = ("tag_not_found", "no_such_tag", "tag_does_not_exist")
_MISSING_TAG_PHRASES = ("not found", "does not exist", "cannot be found", "no such")


def _is_object_type_mismatch(message: str) -> Optional[str]:
    lowered = (message or "").lower()
    if any(p in lowered for p in _VIEW_MISMATCH_PATTERNS):
        return "VIEW"
    if any(p in lowered for p in _TABLE_MISMATCH_PATTERNS):
        return "TABLE"
    return None


def _is_missing_tag_error(message: str) -> bool:
    lowered = (message or "").lower()
    if any(code in lowered for code in _MISSING_TAG_CODES):
        return True
    return "tag" in lowered and any(p in lowered for p in _MISSING_TAG_PHRASES)


@dataclass
class StatementOutcome:
    table: str
    operation: str
    sql: str
    status: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "operation": self.operation,
            "sql": self.sql,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class ApplyResult:
    status: str  # "applied", "noop", "partial", "failed"
    applied_count: int = 0
    noop_count: int = 0
    failed_count: int = 0
    outcomes: List[StatementOutcome] = field(default_factory=list)
    error: Optional[str] = None
    applied_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "applied_count": self.applied_count,
            "noop_count": self.noop_count,
            "failed_count": self.failed_count,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "error": self.error,
            "applied_at": self.applied_at,
        }


def _record_ledger(
    provider,
    ledger_table: str,
    environment: str,
    request_id: str,
    status: str,
    applied_count: int,
    noop_count: int,
    failed_count: int,
    actor: str,
    error: str = "",
) -> None:
    """Record the tag change outcome to the configured Delta ledger table."""
    if not ledger_table:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    file_path = f"local-mode/{request_id}.sql"
    file_sha = hashlib.sha256(f"{request_id}:{now_iso}".encode()).hexdigest()

    clean_actor = _escape_sql_literal(actor or "system")
    clean_error = _escape_sql_literal((error or "")[:4000])

    insert_sql = (
        f"INSERT INTO {ledger_table} "
        f"(environment, file_path, file_sha256, status, statements_applied, statements_noop, "
        f"statements_failed, commit_sha, pr_number, actor, run_url, error, applied_at) "
        f"VALUES ('{environment}', '{file_path}', '{file_sha}', '{status}', {applied_count}, "
        f"{noop_count}, {failed_count}, 'local', NULL, '{clean_actor}', 'local://tag-manager', "
        f"'{clean_error}', current_timestamp())"
    )
    try:
        provider.client.statement_execution.execute_statement(
            statement=insert_sql,
            warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
            wait_timeout="30s",
        )
        logger.info(f"Recorded tag change {request_id} to ledger {ledger_table}")
    except Exception as e:
        logger.warning(f"Could not record to ledger table {ledger_table}: {e}")


def apply_tag_plan(
    provider,
    plan: TagPlan,
    request_id: str,
    actor: str = "system",
    environment: str = "dev",
) -> ApplyResult:
    """Execute planned statements sequentially against Unity Catalog."""
    outcomes: List[StatementOutcome] = []
    applied_count = 0
    noop_count = 0
    failed_count = 0
    general_error: Optional[str] = None
    aborted = False

    for stmt_plan in plan.statement_plans:
        if stmt_plan.is_noop:
            outcomes.append(
                StatementOutcome(
                    table=stmt_plan.table,
                    operation=stmt_plan.operation,
                    sql="",
                    status=NOOP,
                    detail=stmt_plan.noop_reason or "tags already match",
                )
            )
            noop_count += 1
            continue

        if aborted:
            outcomes.append(
                StatementOutcome(
                    table=stmt_plan.table,
                    operation=stmt_plan.operation,
                    sql=stmt_plan.sql,
                    status=FAILED,
                    detail="skipped due to prior failure",
                )
            )
            failed_count += 1
            continue

        sql = stmt_plan.sql
        try:
            resp = provider.client.statement_execution.execute_statement(
                statement=sql,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            if resp.status and resp.status.state and resp.status.state.value == "FAILED":
                err_msg = (resp.status.error.message if resp.status.error else "Execution failed")
                raise Exception(err_msg)

            outcomes.append(
                StatementOutcome(
                    table=stmt_plan.table,
                    operation=stmt_plan.operation,
                    sql=sql,
                    status=APPLIED,
                    detail="applied successfully",
                )
            )
            applied_count += 1

        except Exception as exc:
            err_text = str(exc)
            # 1. Check if view vs table mismatch
            corrected_type = _is_object_type_mismatch(err_text)
            if corrected_type and corrected_type != stmt_plan.object_type:
                logger.info(f"Retrying {stmt_plan.table} as ALTER {corrected_type}")
                if stmt_plan.operation == "set":
                    pairs = ", ".join(
                        f"'{_escape_sql_literal(k)}' = '{_escape_sql_literal(v)}'"
                        for k, v in stmt_plan.tags.items()
                    )
                    retry_sql = f"ALTER {corrected_type} {stmt_plan.table} SET TAGS ({pairs});"
                else:
                    keys_str = ", ".join(f"'{_escape_sql_literal(k)}'" for k in stmt_plan.keys)
                    retry_sql = f"ALTER {corrected_type} {stmt_plan.table} UNSET TAGS ({keys_str});"

                try:
                    retry_resp = provider.client.statement_execution.execute_statement(
                        statement=retry_sql,
                        warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                        wait_timeout="30s",
                    )
                    if retry_resp.status and retry_resp.status.state and retry_resp.status.state.value == "FAILED":
                        r_err = (retry_resp.status.error.message if retry_resp.status.error else "Execution failed")
                        raise Exception(r_err)

                    outcomes.append(
                        StatementOutcome(
                            table=stmt_plan.table,
                            operation=stmt_plan.operation,
                            sql=retry_sql,
                            status=APPLIED,
                            detail=f"applied as ALTER {corrected_type}",
                        )
                    )
                    applied_count += 1
                    continue
                except Exception as retry_exc:
                    err_text = str(retry_exc)

            # 2. Check if missing tag error on UNSET
            if stmt_plan.operation == "unset" and _is_missing_tag_error(err_text):
                outcomes.append(
                    StatementOutcome(
                        table=stmt_plan.table,
                        operation=stmt_plan.operation,
                        sql=sql,
                        status=NOOP,
                        detail="tag was already absent",
                    )
                )
                noop_count += 1
                continue

            # 3. Real failure
            outcomes.append(
                StatementOutcome(
                    table=stmt_plan.table,
                    operation=stmt_plan.operation,
                    sql=sql,
                    status=FAILED,
                    detail=err_text,
                )
            )
            failed_count += 1
            aborted = True
            general_error = f"{stmt_plan.table}: {err_text}"

    # Determine overall status
    if failed_count > 0:
        overall_status = PARTIAL if applied_count > 0 else FAILED
    elif applied_count > 0:
        overall_status = APPLIED
    else:
        overall_status = NOOP

    now_iso = datetime.now(timezone.utc).isoformat()

    # Record ledger if configured
    if settings.GOVERNANCE_TAGS_LEDGER_TABLE:
        _record_ledger(
            provider=provider,
            ledger_table=settings.GOVERNANCE_TAGS_LEDGER_TABLE,
            environment=environment,
            request_id=request_id,
            status=overall_status,
            applied_count=applied_count,
            noop_count=noop_count,
            failed_count=failed_count,
            actor=actor,
            error=general_error or "",
        )

    return ApplyResult(
        status=overall_status,
        applied_count=applied_count,
        noop_count=noop_count,
        failed_count=failed_count,
        outcomes=outcomes,
        error=general_error,
        applied_at=now_iso,
    )
