"""
Database models for Workflow tests — the "does this workflow actually behave?" loop.

A workflow's runtime behavior is driven by its instructions markdown plus its
allowed tools, and until now nothing verified that. ``evaluate-spec`` scores the
*shape* of a graph; the golden-transcript harness compares tool/gate counts. Neither
answers the only question an author really has: *if a user asks this, does the agent
do the right thing?*

A :class:`WorkflowTestModel` is that question written down — a plain-English
question plus a plain-English expected outcome. Running one starts the real agent
against the workflow's own instructions and tools with the ToolExecutor in
``dry_run`` (nothing mutates), then an LLM judge compares the transcript to the
expectation. Results land in :class:`WorkflowTestRunModel`.

The judge is non-deterministic, so a run row keeps the transcript and the judge's
rationale, not just a verdict: a surprising result has to be reviewable, and any
case can be re-run.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, Boolean
from sqlalchemy.orm import Mapped

from app.db.base import Base


class WorkflowTestModel(Base):
    """One test case for a workflow: a question and the outcome it should produce."""

    __tablename__ = "workflow_tests"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the test case")
    workflow_id: Mapped[str] = Column(
        String, nullable=False, index=True, comment="FK to workflows.id",
    )
    name: Mapped[str] = Column(
        String, nullable=False,
        comment="Short label, e.g. 'happy path' or 'missing required field'",
    )
    question: Mapped[str] = Column(
        Text, nullable=False,
        comment="What the user says to the agent to start this case",
    )
    expected_outcome: Mapped[str] = Column(
        Text, nullable=False,
        comment=(
            "Plain-English description of what should happen — the judge compares "
            "the agent's actual transcript against this, so it describes behavior "
            "(what it asks for, what it refuses, which tool it calls), not exact wording"
        ),
    )
    enabled: Mapped[bool] = Column(
        Boolean, nullable=False, default=True,
        comment="Disabled cases are kept for reference but skipped by 'Run all'",
    )
    source: Mapped[str] = Column(
        String, nullable=False, default="user", index=True,
        comment="agent (proposed by the authoring assistant) | user (written by an admin)",
    )
    created_by: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="Email of the admin who created it",
    )
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


class WorkflowTestRunModel(Base):
    """The outcome of running one test case once.

    Rows are created up-front in ``queued`` status when a run is requested, so the
    UI can poll a stable set instead of guessing how many results to expect.
    ``run_group_id`` ties together the cases launched by a single "Run all".
    """

    __tablename__ = "workflow_test_runs"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the run")
    run_group_id: Mapped[str] = Column(
        String, nullable=False, index=True,
        comment="Groups the runs started by one Run all / Run one action",
    )
    workflow_id: Mapped[str] = Column(String, nullable=False, index=True, comment="FK to workflows.id")
    test_id: Mapped[str] = Column(String, nullable=False, index=True, comment="FK to workflow_tests.id")
    # Denormalized so a run stays readable after the case is edited or deleted —
    # otherwise history silently re-describes itself when someone rewords a test.
    test_name: Mapped[Optional[str]] = Column(String, nullable=True)
    question: Mapped[Optional[str]] = Column(Text, nullable=True)
    expected_outcome: Mapped[Optional[str]] = Column(Text, nullable=True)
    status: Mapped[str] = Column(
        String, nullable=False, default="queued", index=True,
        comment="queued | running | complete | error",
    )
    verdict: Mapped[Optional[str]] = Column(
        String, nullable=True, index=True, comment="pass | fail | partial (judge's call)",
    )
    score: Mapped[Optional[int]] = Column(
        Integer, nullable=True, comment="0-100 confidence the expected outcome was met",
    )
    rationale: Mapped[Optional[str]] = Column(
        Text, nullable=True, comment="Why the judge decided that — always stored, never inferred",
    )
    missing: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="Specific expectations the judge found unmet",
    )
    transcript: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="The agent's messages for this case (evidence for the verdict)",
    )
    tool_calls: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="Tools the agent called, with args (all simulated if mutating)",
    )
    error: Mapped[Optional[str]] = Column(
        Text, nullable=True, comment="Set when status='error' (agent or judge failed to produce a verdict)",
    )
    duration_ms: Mapped[Optional[int]] = Column(Integer, nullable=True)
    triggered_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who ran it")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
