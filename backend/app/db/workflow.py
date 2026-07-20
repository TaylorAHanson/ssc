"""
Database model for Workflows (the "workflows as data" / no-code authoring object).

A Workflow is the admin-authored, DB-backed definition of an agent capability. It
replaces the filesystem ``app/agents/instructions/*.md`` scan: the agent's
capabilities list and the ``get_workflow_instructions`` lookup both read
published Workflows from this table. Admins create/edit/publish Workflows in the UI
instead of editing markdown files and redeploying.

Fields mirror the M1/M2 guardrail metadata so a Workflow fully describes how its
workflow runs: which tools it may call (capability scoping), its policy ref, and
its parameter schema — alongside the human-facing instructions markdown.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, Boolean
from sqlalchemy.orm import Mapped

from app.db.base import Base


class WorkflowModel(Base):
    """An admin-authored agent capability (workflow) stored as data."""

    __tablename__ = "workflows"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the workflow")
    key: Mapped[str] = Column(
        String, nullable=False, unique=True, index=True,
        comment="Stable internal name the agent references (e.g. 'workspace_access')",
    )
    name: Mapped[str] = Column(String, nullable=False, comment="Human-readable workflow name")
    goal: Mapped[Optional[str]] = Column(
        Text, nullable=True,
        comment="One-line description shown in the agent's capabilities list",
    )
    instructions_markdown: Mapped[Optional[str]] = Column(
        Text, nullable=True,
        comment="Full markdown instructions returned by get_workflow_instructions",
    )
    allowed_tools: Mapped[Optional[list]] = Column(
        JSON, nullable=True,
        comment="Capability scoping: tool names this workflow may call ([] / null = inherit defaults)",
    )
    policy_ref: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="OPA policy ref governing this workflow's mutating calls",
    )
    params_schema: Mapped[Optional[dict]] = Column(
        JSON, nullable=True, comment="JSON schema for the execute_workflow parameters",
    )
    graph_spec: Mapped[Optional[dict]] = Column(
        JSON, nullable=True,
        comment=(
            "Serializable workflow graph (stages of gates/steps with expr-based "
            "args). When a published workflow has one, the durable executor compiles "
            "and runs it instead of the code catalog \u2014 the no-code core."
        ),
    )
    request_type: Mapped[Optional[str]] = Column(
        String, nullable=True, index=True,
        comment="V2 graph/RequestType this workflow maps to (if it provisions)",
    )
    status: Mapped[str] = Column(
        String, nullable=False, default="draft", index=True,
        comment="draft | published (only published workflows are visible to the agent)",
    )
    disabled: Mapped[bool] = Column(
        Boolean, nullable=False, default=False, index=True,
        comment=(
            "Operational kill switch: when true the workflow is hidden from the "
            "agent (capabilities list, instructions lookup, and execution) even if "
            "status='published'. Unlike unpublish/edit this is an OPERATIONAL toggle "
            "that stays available when authoring is locked (prod), and is fully "
            "reversible — it never touches the workflow's definition or version."
        ),
    )
    version: Mapped[int] = Column(Integer, nullable=False, default=1, comment="Bumped on each publish")
    source: Mapped[str] = Column(
        String, nullable=False, default="user", index=True,
        comment="user (authored in UI) | seed (imported from filesystem instructions)",
    )
    created_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who created it")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WorkflowVersionModel(Base):
    """An immutable snapshot of a Workflow captured each time it is published.

    Powers version history + one-click rollback: every publish writes the full
    body here keyed by (workflow_id, version), so an admin can see what changed and
    restore a prior published definition if a change misbehaves in an env.
    """

    __tablename__ = "workflow_versions"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the snapshot")
    workflow_id: Mapped[str] = Column(String, nullable=False, index=True, comment="FK to workflows.id")
    workflow_key: Mapped[str] = Column(String, nullable=False, index=True, comment="Workflow key at snapshot time")
    version: Mapped[int] = Column(Integer, nullable=False, comment="The published version number")
    name: Mapped[Optional[str]] = Column(String, nullable=True)
    goal: Mapped[Optional[str]] = Column(Text, nullable=True)
    instructions_markdown: Mapped[Optional[str]] = Column(Text, nullable=True)
    allowed_tools: Mapped[Optional[list]] = Column(JSON, nullable=True)
    policy_ref: Mapped[Optional[str]] = Column(String, nullable=True)
    params_schema: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    graph_spec: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    request_type: Mapped[Optional[str]] = Column(String, nullable=True)
    published_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who published")
    published_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
