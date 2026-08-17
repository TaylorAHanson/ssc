"""
Service layer for workflow test cases and their runs.

Owns the boring half of the Tests tab: CRUD on cases, creating the run rows a
sandboxed execution will fill in, reading results back for polling, and the
rate limit on what is ultimately an agent-invocation surface. The interesting
half — actually running the agent and judging it — lives in
``app.workflows.test_runner``.

Two deliberate choices:

* Run rows are created **queued up front**, before any agent starts. The UI then
  polls a known set of rows instead of guessing how many results to expect, and a
  crash mid-run leaves visible evidence rather than a silently short list.
* A run row **denormalizes** the case's question/expected outcome. Editing a case
  must not retroactively rewrite what a past run was checking.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.workflow_test import WorkflowTestModel, WorkflowTestRunModel

logger = logging.getLogger(__name__)

# A case whose latest run is older than this is shown as stale rather than green:
# a pass against a since-edited workflow says nothing about the workflow today.
STALE_RUN_HOURS = 24 * 7


class WorkflowTestService:
    # ------------------------------------------------------------------ cases
    @staticmethod
    def list_tests(db: Session, workflow_id: str) -> List[WorkflowTestModel]:
        return (
            db.query(WorkflowTestModel)
            .filter(WorkflowTestModel.workflow_id == workflow_id)
            .order_by(WorkflowTestModel.created_at.asc())
            .all()
        )

    @staticmethod
    def get_test(db: Session, test_id: str) -> Optional[WorkflowTestModel]:
        return db.query(WorkflowTestModel).filter(WorkflowTestModel.id == test_id).first()

    @staticmethod
    def create_test(
        db: Session,
        workflow_id: str,
        *,
        name: str,
        question: str,
        expected_outcome: str,
        enabled: bool = True,
        source: str = "user",
        created_by: Optional[str] = None,
    ) -> WorkflowTestModel:
        row = WorkflowTestModel(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            name=(name or "").strip() or "Untitled case",
            question=(question or "").strip(),
            expected_outcome=(expected_outcome or "").strip(),
            enabled=enabled,
            source=source,
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def update_test(db: Session, test_id: str, payload: Dict[str, Any]) -> Optional[WorkflowTestModel]:
        row = WorkflowTestService.get_test(db, test_id)
        if row is None:
            return None
        for field in ("name", "question", "expected_outcome", "enabled"):
            if field in payload and payload[field] is not None:
                value = payload[field]
                setattr(row, field, value.strip() if isinstance(value, str) else value)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def delete_test(db: Session, test_id: str) -> bool:
        row = WorkflowTestService.get_test(db, test_id)
        if row is None:
            return False
        # Runs are kept: they're the audit trail of what was verified and when.
        # Orphaned rows still read correctly thanks to the denormalized fields.
        db.delete(row)
        db.commit()
        return True

    @staticmethod
    def replace_tests(
        db: Session,
        workflow_id: str,
        cases: List[Dict[str, Any]],
        *,
        source: str = "agent",
        created_by: Optional[str] = None,
        keep_user_authored: bool = True,
    ) -> List[WorkflowTestModel]:
        """Replace a workflow's cases with ``cases`` (used by the authoring tool).

        ``keep_user_authored`` protects hand-written cases from being wiped by an
        assistant proposal — the same "don't clobber the admin's work" rule the
        studio applies to instructions. Only previously agent-sourced cases are
        replaced.
        """
        existing = WorkflowTestService.list_tests(db, workflow_id)
        for row in existing:
            if keep_user_authored and row.source == "user":
                continue
            db.delete(row)
        db.flush()

        out: List[WorkflowTestModel] = []
        for case in cases or []:
            question = (case.get("question") or "").strip()
            expected = (case.get("expected_outcome") or "").strip()
            if not question or not expected:
                # A case missing either half can't be judged; skip rather than
                # persisting something that will always error at run time.
                continue
            row = WorkflowTestModel(
                id=str(uuid.uuid4()),
                workflow_id=workflow_id,
                name=(case.get("name") or "").strip() or "Untitled case",
                question=question,
                expected_outcome=expected,
                enabled=bool(case.get("enabled", True)),
                source=source,
                created_by=created_by,
            )
            db.add(row)
            out.append(row)
        db.commit()
        for row in out:
            db.refresh(row)
        return out

    # ------------------------------------------------------------------- runs
    @staticmethod
    def recent_run_count(db: Session, actor_email: Optional[str], *, hours: int = 1) -> int:
        """Cases this actor has launched in the last ``hours`` (for rate limiting)."""
        if not actor_email:
            return 0
        since = datetime.utcnow() - timedelta(hours=hours)
        return (
            db.query(WorkflowTestRunModel)
            .filter(
                WorkflowTestRunModel.triggered_by == actor_email,
                WorkflowTestRunModel.created_at >= since,
            )
            .count()
        )

    @staticmethod
    def create_run_group(
        db: Session,
        workflow_id: str,
        tests: List[WorkflowTestModel],
        *,
        triggered_by: Optional[str] = None,
    ) -> tuple[str, List[WorkflowTestRunModel]]:
        """Create ``queued`` run rows for ``tests`` and return their group id."""
        group_id = str(uuid.uuid4())
        rows: List[WorkflowTestRunModel] = []
        for test in tests:
            row = WorkflowTestRunModel(
                id=str(uuid.uuid4()),
                run_group_id=group_id,
                workflow_id=workflow_id,
                test_id=test.id,
                test_name=test.name,
                question=test.question,
                expected_outcome=test.expected_outcome,
                status="queued",
                triggered_by=triggered_by,
            )
            db.add(row)
            rows.append(row)
        db.commit()
        for row in rows:
            db.refresh(row)
        return group_id, rows

    @staticmethod
    def get_run_group(db: Session, group_id: str) -> List[WorkflowTestRunModel]:
        return (
            db.query(WorkflowTestRunModel)
            .filter(WorkflowTestRunModel.run_group_id == group_id)
            .order_by(WorkflowTestRunModel.created_at.asc())
            .all()
        )

    @staticmethod
    def latest_runs(db: Session, workflow_id: str) -> Dict[str, WorkflowTestRunModel]:
        """Most recent run per test id — what the Tests tab shows at rest."""
        rows = (
            db.query(WorkflowTestRunModel)
            .filter(WorkflowTestRunModel.workflow_id == workflow_id)
            .order_by(WorkflowTestRunModel.created_at.desc())
            .all()
        )
        latest: Dict[str, WorkflowTestRunModel] = {}
        for row in rows:
            latest.setdefault(row.test_id, row)
        return latest

    # ------------------------------------------------------------- publish view
    @staticmethod
    def health(db: Session, workflow_id: str) -> Dict[str, Any]:
        """Test posture for a workflow, for the studio list and publish confirmation.

        Reports ``never_run`` separately from ``failing``: "we don't know" and "we
        know it's broken" call for different reactions, and collapsing them into
        one number is how a workflow ships untested.
        """
        tests = [t for t in WorkflowTestService.list_tests(db, workflow_id) if t.enabled]
        latest = WorkflowTestService.latest_runs(db, workflow_id)
        threshold = int(getattr(settings, "WORKFLOW_TEST_PASS_THRESHOLD", 70) or 70)
        passing = failing = never_run = errored = stale = 0
        stale_before = datetime.utcnow() - timedelta(hours=STALE_RUN_HOURS)
        for test in tests:
            run = latest.get(test.id)
            if run is None or run.status in ("queued", "running"):
                never_run += 1
                continue
            if run.status == "error":
                errored += 1
                continue
            if run.created_at and run.created_at < stale_before:
                stale += 1
            if WorkflowTestService.is_pass(run, threshold):
                passing += 1
            else:
                failing += 1
        return {
            "total": len(tests),
            "passing": passing,
            "failing": failing,
            "errored": errored,
            "never_run": never_run,
            "stale": stale,
            "pass_threshold": threshold,
            # "Ready" means every enabled case has been run and passed. An empty
            # suite is NOT ready — it just has nothing to fail.
            "ready": bool(tests) and passing == len(tests),
        }

    @staticmethod
    def health_map(db: Session, workflow_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """``health`` for many workflows in two queries, for the studio list.

        Calling ``health`` per row would put two queries per workflow behind the
        list endpoint, so the health columns are computed in bulk instead.
        """
        ids = [wid for wid in workflow_ids if wid]
        if not ids:
            return {}
        tests = (
            db.query(WorkflowTestModel)
            .filter(WorkflowTestModel.workflow_id.in_(ids))
            .all()
        )
        runs = (
            db.query(WorkflowTestRunModel)
            .filter(WorkflowTestRunModel.workflow_id.in_(ids))
            .order_by(WorkflowTestRunModel.created_at.desc())
            .all()
        )
        latest: Dict[str, WorkflowTestRunModel] = {}
        for row in runs:
            latest.setdefault(row.test_id, row)

        threshold = int(getattr(settings, "WORKFLOW_TEST_PASS_THRESHOLD", 70) or 70)
        stale_before = datetime.utcnow() - timedelta(hours=STALE_RUN_HOURS)
        by_workflow: Dict[str, Dict[str, Any]] = {
            wid: {
                "total": 0, "passing": 0, "failing": 0, "errored": 0,
                "never_run": 0, "stale": 0, "pass_threshold": threshold,
                "ready": False,
            }
            for wid in ids
        }
        for test in tests:
            if not test.enabled:
                continue
            bucket = by_workflow.get(test.workflow_id)
            if bucket is None:
                continue
            bucket["total"] += 1
            run = latest.get(test.id)
            if run is None or run.status in ("queued", "running"):
                bucket["never_run"] += 1
                continue
            if run.status == "error":
                bucket["errored"] += 1
                continue
            if run.created_at and run.created_at < stale_before:
                bucket["stale"] += 1
            if WorkflowTestService.is_pass(run, threshold):
                bucket["passing"] += 1
            else:
                bucket["failing"] += 1
        for bucket in by_workflow.values():
            bucket["ready"] = bucket["total"] > 0 and bucket["passing"] == bucket["total"]
        return by_workflow

    @staticmethod
    def is_pass(run: WorkflowTestRunModel, threshold: Optional[int] = None) -> bool:
        """Whether a completed run counts as a pass.

        The judge's own verdict is authoritative for ``fail``; the numeric
        threshold decides borderline ``partial`` results. A run with no verdict at
        all is not a pass.
        """
        if run.status != "complete" or not run.verdict:
            return False
        verdict = (run.verdict or "").strip().lower()
        if verdict == "fail":
            return False
        if threshold is None:
            threshold = int(getattr(settings, "WORKFLOW_TEST_PASS_THRESHOLD", 70) or 70)
        if verdict == "pass":
            # Trust an explicit pass unless the score contradicts it outright.
            return run.score is None or run.score >= threshold
        return run.score is not None and run.score >= threshold


def test_to_dict(row: WorkflowTestModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "name": row.name,
        "question": row.question,
        "expected_outcome": row.expected_outcome,
        "enabled": bool(row.enabled),
        "source": row.source,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def run_to_dict(row: WorkflowTestRunModel, *, include_transcript: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": row.id,
        "run_group_id": row.run_group_id,
        "workflow_id": row.workflow_id,
        "test_id": row.test_id,
        "test_name": row.test_name,
        "question": row.question,
        "expected_outcome": row.expected_outcome,
        "status": row.status,
        "verdict": row.verdict,
        "score": row.score,
        "rationale": row.rationale,
        "missing": row.missing or [],
        "error": row.error,
        "duration_ms": row.duration_ms,
        "triggered_by": row.triggered_by,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
        "passed": WorkflowTestService.is_pass(row),
    }
    if include_transcript:
        out["transcript"] = row.transcript or []
        out["tool_calls"] = row.tool_calls or []
    return out


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    # Rows are written with naive UTC timestamps; label them so the browser
    # doesn't render them as local time and make a fresh run look hours old.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
