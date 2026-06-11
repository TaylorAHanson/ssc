"""
Service layer for Workflows (DB-backed "workflows as data").

Owns CRUD + draft/publish lifecycle and the one-time import of the legacy
filesystem instructions (``app/agents/instructions/*.md``) into the ``workflows``
table. The agent prompt builder and ``get_workflow_instructions`` read through
:meth:`list_published` / :meth:`get_published` here instead of the filesystem.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.workflow import WorkflowModel, WorkflowVersionModel

logger = logging.getLogger(__name__)

_GOAL_RE = re.compile(r"\*\*Goal\*\*:\s*(.*?)(?:\n|$)")

# Portable bundle format tag (bumped if the export shape changes).
BUNDLE_FORMAT = "atlas.workflows/v1"

# Body fields that travel with a workflow across envs / snapshots (no ids/status/version).
_BODY_FIELDS = (
    "name", "goal", "instructions_markdown", "allowed_tools",
    "policy_ref", "params_schema", "graph_spec", "request_type",
)


class WorkflowService:
    # ------------------------------------------------------------------ reads
    @staticmethod
    def list_workflows(db: Session, *, include_drafts: bool = True) -> List[WorkflowModel]:
        q = db.query(WorkflowModel)
        if not include_drafts:
            q = q.filter(WorkflowModel.status == "published")
        return q.order_by(WorkflowModel.key.asc()).all()

    @staticmethod
    def list_published(db: Session) -> List[WorkflowModel]:
        return WorkflowService.list_workflows(db, include_drafts=False)

    @staticmethod
    def get(db: Session, workflow_id: str) -> Optional[WorkflowModel]:
        return db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()

    @staticmethod
    def get_by_key(db: Session, key: str, *, published_only: bool = False) -> Optional[WorkflowModel]:
        q = db.query(WorkflowModel).filter(WorkflowModel.key == key)
        if published_only:
            q = q.filter(WorkflowModel.status == "published")
        return q.first()

    # ----------------------------------------------------------------- writes
    @staticmethod
    def create(db: Session, *, created_by: Optional[str] = None, **fields) -> WorkflowModel:
        key = (fields.get("key") or "").strip()
        if not key:
            raise ValueError("key is required")
        if WorkflowService.get_by_key(db, key):
            raise ValueError(f"A workflow with key '{key}' already exists")
        workflow = WorkflowModel(
            id=str(uuid.uuid4()),
            key=key,
            name=fields.get("name") or key,
            goal=fields.get("goal"),
            instructions_markdown=fields.get("instructions_markdown"),
            allowed_tools=fields.get("allowed_tools"),
            policy_ref=fields.get("policy_ref"),
            params_schema=fields.get("params_schema"),
            graph_spec=fields.get("graph_spec"),
            request_type=fields.get("request_type"),
            status=fields.get("status") or "draft",
            source=fields.get("source") or "user",
            created_by=created_by,
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def update(db: Session, workflow_id: str, **fields) -> WorkflowModel:
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        for col in ("name", "goal", "instructions_markdown", "allowed_tools",
                    "policy_ref", "params_schema", "graph_spec", "request_type", "status"):
            if col in fields and fields[col] is not None:
                setattr(workflow, col, fields[col])
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def publish(db: Session, workflow_id: str, *, published_by: Optional[str] = None) -> WorkflowModel:
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        workflow.status = "published"
        workflow.version = (workflow.version or 0) + 1
        # Snapshot the published body so it can be inspected / rolled back to later.
        db.add(WorkflowVersionModel(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            workflow_key=workflow.key,
            version=workflow.version,
            name=workflow.name,
            goal=workflow.goal,
            instructions_markdown=workflow.instructions_markdown,
            allowed_tools=workflow.allowed_tools,
            policy_ref=workflow.policy_ref,
            params_schema=workflow.params_schema,
            graph_spec=workflow.graph_spec,
            request_type=workflow.request_type,
            published_by=published_by,
        ))
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def unpublish(db: Session, workflow_id: str) -> WorkflowModel:
        return WorkflowService.update(db, workflow_id, status="draft")

    # ----------------------------------------------------- version history
    @staticmethod
    def list_versions(db: Session, workflow_id: str) -> List[WorkflowVersionModel]:
        return (
            db.query(WorkflowVersionModel)
            .filter(WorkflowVersionModel.workflow_id == workflow_id)
            .order_by(WorkflowVersionModel.version.desc())
            .all()
        )

    @staticmethod
    def rollback(db: Session, workflow_id: str, version: int) -> WorkflowModel:
        """Restore a prior published snapshot into the workflow as a *draft*.

        Rolling back loads the body of ``version`` back onto the live row but
        leaves it as a draft so an admin can review (and re-test) before
        re-publishing — which then snapshots again as the next version.
        """
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        snap = (
            db.query(WorkflowVersionModel)
            .filter(WorkflowVersionModel.workflow_id == workflow_id,
                    WorkflowVersionModel.version == version)
            .first()
        )
        if not snap:
            raise ValueError(f"Version {version} not found for this workflow")
        for col in _BODY_FIELDS:
            setattr(workflow, col, getattr(snap, col))
        workflow.status = "draft"
        db.commit()
        db.refresh(workflow)
        return workflow

    # ------------------------------------------------- export / import (envs)
    @staticmethod
    def export_bundle(
        db: Session, *, ids: Optional[List[str]] = None, published_only: bool = False,
    ) -> Dict[str, Any]:
        """Build a portable, env-agnostic bundle of workflow definitions.

        Bundles are keyed by ``key`` (no ids/status/version), so they import
        cleanly into another environment for the dev -> staging -> prod flow.
        """
        from datetime import datetime as _dt

        q = db.query(WorkflowModel)
        if ids:
            q = q.filter(WorkflowModel.id.in_(ids))
        if published_only:
            q = q.filter(WorkflowModel.status == "published")
        workflows = q.order_by(WorkflowModel.key.asc()).all()
        return {
            "format": BUNDLE_FORMAT,
            "exported_at": _dt.utcnow().isoformat(),
            "workflows": [
                {"key": s.key, **{f: getattr(s, f) for f in _BODY_FIELDS}}
                for s in workflows
            ],
        }

    @staticmethod
    def import_bundle(
        db: Session,
        bundle: Dict[str, Any],
        *,
        as_status: str = "draft",
        overwrite: bool = True,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert workflows from a bundle (by key). Returns a per-workflow report.

        Defaults to importing as **draft** so a promoted workflow is reviewed and
        tested in the target env before it is published.
        """
        from app.v2.spec_loader import SpecError, validate_spec_dict

        if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"Unrecognized bundle format (expected {BUNDLE_FORMAT})")
        entries = bundle.get("workflows")
        if not isinstance(entries, list):
            raise ValueError("Bundle 'workflows' must be a list")
        if as_status not in ("draft", "published"):
            raise ValueError("as_status must be 'draft' or 'published'")

        report: Dict[str, Any] = {"created": [], "updated": [], "skipped": [], "errors": []}
        for entry in entries:
            key = (entry or {}).get("key")
            if not key:
                report["errors"].append({"key": None, "error": "missing key"})
                continue
            try:
                spec = entry.get("graph_spec")
                if spec is not None:
                    validate_spec_dict(spec)  # reject malformed graphs at the border
            except SpecError as e:
                report["errors"].append({"key": key, "error": f"invalid graph_spec: {e}"})
                continue

            body = {f: entry.get(f) for f in _BODY_FIELDS}
            existing = WorkflowService.get_by_key(db, key)
            if existing:
                if not overwrite:
                    report["skipped"].append(key)
                    continue
                for col in _BODY_FIELDS:
                    setattr(existing, col, body.get(col))
                existing.status = as_status
                report["updated"].append(key)
            else:
                db.add(WorkflowModel(
                    id=str(uuid.uuid4()),
                    key=key,
                    name=body.get("name") or key,
                    goal=body.get("goal"),
                    instructions_markdown=body.get("instructions_markdown"),
                    allowed_tools=body.get("allowed_tools"),
                    policy_ref=body.get("policy_ref"),
                    params_schema=body.get("params_schema"),
                    graph_spec=body.get("graph_spec"),
                    request_type=body.get("request_type"),
                    status=as_status,
                    source="import",
                    created_by=created_by,
                ))
                report["created"].append(key)
        db.commit()
        return report

    @staticmethod
    def delete(db: Session, workflow_id: str) -> None:
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        db.delete(workflow)
        db.commit()

    # --------------------------------------------------------------- seeding
    @staticmethod
    def seed_from_filesystem(db: Session) -> int:
        """Import legacy instruction markdown files as published Workflows, once.

        Idempotent: only inserts keys not already present, so re-running (or
        running after admins have edited Workflows) never clobbers DB state.
        """
        instructions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agents", "instructions",
        )
        if not os.path.isdir(instructions_dir):
            return 0
        existing = {s.key for s in db.query(WorkflowModel.key).all()}
        inserted = 0
        for filename in sorted(os.listdir(instructions_dir)):
            if not filename.endswith(".md"):
                continue
            key = filename[:-3]
            if key in existing:
                continue
            try:
                with open(os.path.join(instructions_dir, filename), "r") as f:
                    content = f.read()
            except Exception as e:  # noqa: BLE001
                logger.warning("Skipping instruction %s: %s", filename, e)
                continue
            goal_match = _GOAL_RE.search(content)
            db.add(WorkflowModel(
                id=str(uuid.uuid4()),
                key=key,
                name=key.replace("_", " ").title(),
                goal=goal_match.group(1).strip() if goal_match else None,
                instructions_markdown=content,
                status="published",
                source="seed",
                created_by="system",
            ))
            inserted += 1
        if inserted:
            db.commit()
            logger.info("Seeded %d workflows from filesystem instructions", inserted)
        return inserted

    @staticmethod
    def seed_specs_from_catalog(db: Session) -> int:
        """Attach the code spec catalog to Workflows as editable ``graph_spec`` data.

        For each workflow in ``app.v2.graphs.specs.SPECS`` (keyed by RequestType
        value): if a workflow with that key exists, backfill its ``graph_spec`` +
        ``request_type`` only when not already set (never clobbers admin edits);
        otherwise create a published workflow carrying the graph. Idempotent.
        """
        from app.v2.graphs.specs import SPECS

        inserted = 0
        updated = 0
        for rt, spec in SPECS.items():
            existing = WorkflowService.get_by_key(db, rt)
            if existing:
                if not existing.graph_spec:
                    existing.graph_spec = spec
                    existing.request_type = existing.request_type or rt
                    updated += 1
                continue
            db.add(WorkflowModel(
                id=str(uuid.uuid4()),
                key=rt,
                name=rt.replace("_", " ").title(),
                goal=None,
                graph_spec=spec,
                request_type=rt,
                status="published",
                source="seed",
                created_by="system",
            ))
            inserted += 1
        if inserted or updated:
            db.commit()
            logger.info("Seeded workflow graph_specs: %d new, %d backfilled", inserted, updated)
        return inserted + updated

    # --------------------------------------------------------------- mapping
    @staticmethod
    def to_dict(workflow: WorkflowModel, *, include_body: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": workflow.id,
            "key": workflow.key,
            "name": workflow.name,
            "goal": workflow.goal,
            "allowed_tools": workflow.allowed_tools,
            "policy_ref": workflow.policy_ref,
            "params_schema": workflow.params_schema,
            "request_type": workflow.request_type,
            "status": workflow.status,
            "version": workflow.version,
            "source": workflow.source,
            "created_by": workflow.created_by,
            "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
            "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
        }
        if include_body:
            d["instructions_markdown"] = workflow.instructions_markdown
            d["graph_spec"] = workflow.graph_spec
        return d

    @staticmethod
    def version_to_dict(snap: WorkflowVersionModel, *, include_body: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": snap.id,
            "workflow_id": snap.workflow_id,
            "workflow_key": snap.workflow_key,
            "version": snap.version,
            "name": snap.name,
            "goal": snap.goal,
            "request_type": snap.request_type,
            "published_by": snap.published_by,
            "published_at": snap.published_at.isoformat() if snap.published_at else None,
            "has_graph": snap.graph_spec is not None,
            "stage_count": len((snap.graph_spec or {}).get("stages", []))
            if snap.graph_spec else 0,
        }
        if include_body:
            d["instructions_markdown"] = snap.instructions_markdown
            d["graph_spec"] = snap.graph_spec
            d["allowed_tools"] = snap.allowed_tools
            d["policy_ref"] = snap.policy_ref
            d["params_schema"] = snap.params_schema
        return d
