"""
Service layer for Skills (DB-backed "workflows as data").

Owns CRUD + draft/publish lifecycle and the one-time import of the legacy
filesystem instructions (``app/agents/instructions/*.md``) into the ``skills``
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

from app.db.skill import SkillModel, SkillVersionModel

logger = logging.getLogger(__name__)

_GOAL_RE = re.compile(r"\*\*Goal\*\*:\s*(.*?)(?:\n|$)")

# Portable bundle format tag (bumped if the export shape changes).
BUNDLE_FORMAT = "atlas.skills/v1"

# Body fields that travel with a skill across envs / snapshots (no ids/status/version).
_BODY_FIELDS = (
    "name", "goal", "instructions_markdown", "allowed_tools",
    "policy_ref", "params_schema", "graph_spec", "request_type",
)


class SkillService:
    # ------------------------------------------------------------------ reads
    @staticmethod
    def list_skills(db: Session, *, include_drafts: bool = True) -> List[SkillModel]:
        q = db.query(SkillModel)
        if not include_drafts:
            q = q.filter(SkillModel.status == "published")
        return q.order_by(SkillModel.key.asc()).all()

    @staticmethod
    def list_published(db: Session) -> List[SkillModel]:
        return SkillService.list_skills(db, include_drafts=False)

    @staticmethod
    def get(db: Session, skill_id: str) -> Optional[SkillModel]:
        return db.query(SkillModel).filter(SkillModel.id == skill_id).first()

    @staticmethod
    def get_by_key(db: Session, key: str, *, published_only: bool = False) -> Optional[SkillModel]:
        q = db.query(SkillModel).filter(SkillModel.key == key)
        if published_only:
            q = q.filter(SkillModel.status == "published")
        return q.first()

    # ----------------------------------------------------------------- writes
    @staticmethod
    def create(db: Session, *, created_by: Optional[str] = None, **fields) -> SkillModel:
        key = (fields.get("key") or "").strip()
        if not key:
            raise ValueError("key is required")
        if SkillService.get_by_key(db, key):
            raise ValueError(f"A skill with key '{key}' already exists")
        skill = SkillModel(
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
        db.add(skill)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def update(db: Session, skill_id: str, **fields) -> SkillModel:
        skill = SkillService.get(db, skill_id)
        if not skill:
            raise ValueError("Skill not found")
        for col in ("name", "goal", "instructions_markdown", "allowed_tools",
                    "policy_ref", "params_schema", "graph_spec", "request_type", "status"):
            if col in fields and fields[col] is not None:
                setattr(skill, col, fields[col])
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def publish(db: Session, skill_id: str, *, published_by: Optional[str] = None) -> SkillModel:
        skill = SkillService.get(db, skill_id)
        if not skill:
            raise ValueError("Skill not found")
        skill.status = "published"
        skill.version = (skill.version or 0) + 1
        # Snapshot the published body so it can be inspected / rolled back to later.
        db.add(SkillVersionModel(
            id=str(uuid.uuid4()),
            skill_id=skill.id,
            skill_key=skill.key,
            version=skill.version,
            name=skill.name,
            goal=skill.goal,
            instructions_markdown=skill.instructions_markdown,
            allowed_tools=skill.allowed_tools,
            policy_ref=skill.policy_ref,
            params_schema=skill.params_schema,
            graph_spec=skill.graph_spec,
            request_type=skill.request_type,
            published_by=published_by,
        ))
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def unpublish(db: Session, skill_id: str) -> SkillModel:
        return SkillService.update(db, skill_id, status="draft")

    # ----------------------------------------------------- version history
    @staticmethod
    def list_versions(db: Session, skill_id: str) -> List[SkillVersionModel]:
        return (
            db.query(SkillVersionModel)
            .filter(SkillVersionModel.skill_id == skill_id)
            .order_by(SkillVersionModel.version.desc())
            .all()
        )

    @staticmethod
    def rollback(db: Session, skill_id: str, version: int) -> SkillModel:
        """Restore a prior published snapshot into the skill as a *draft*.

        Rolling back loads the body of ``version`` back onto the live row but
        leaves it as a draft so an admin can review (and re-test) before
        re-publishing — which then snapshots again as the next version.
        """
        skill = SkillService.get(db, skill_id)
        if not skill:
            raise ValueError("Skill not found")
        snap = (
            db.query(SkillVersionModel)
            .filter(SkillVersionModel.skill_id == skill_id,
                    SkillVersionModel.version == version)
            .first()
        )
        if not snap:
            raise ValueError(f"Version {version} not found for this skill")
        for col in _BODY_FIELDS:
            setattr(skill, col, getattr(snap, col))
        skill.status = "draft"
        db.commit()
        db.refresh(skill)
        return skill

    # ------------------------------------------------- export / import (envs)
    @staticmethod
    def export_bundle(
        db: Session, *, ids: Optional[List[str]] = None, published_only: bool = False,
    ) -> Dict[str, Any]:
        """Build a portable, env-agnostic bundle of skill definitions.

        Bundles are keyed by ``key`` (no ids/status/version), so they import
        cleanly into another environment for the dev -> staging -> prod flow.
        """
        from datetime import datetime as _dt

        q = db.query(SkillModel)
        if ids:
            q = q.filter(SkillModel.id.in_(ids))
        if published_only:
            q = q.filter(SkillModel.status == "published")
        skills = q.order_by(SkillModel.key.asc()).all()
        return {
            "format": BUNDLE_FORMAT,
            "exported_at": _dt.utcnow().isoformat(),
            "skills": [
                {"key": s.key, **{f: getattr(s, f) for f in _BODY_FIELDS}}
                for s in skills
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
        """Upsert skills from a bundle (by key). Returns a per-skill report.

        Defaults to importing as **draft** so a promoted workflow is reviewed and
        tested in the target env before it is published.
        """
        from app.v2.spec_loader import SpecError, validate_spec_dict

        if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"Unrecognized bundle format (expected {BUNDLE_FORMAT})")
        entries = bundle.get("skills")
        if not isinstance(entries, list):
            raise ValueError("Bundle 'skills' must be a list")
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
            existing = SkillService.get_by_key(db, key)
            if existing:
                if not overwrite:
                    report["skipped"].append(key)
                    continue
                for col in _BODY_FIELDS:
                    setattr(existing, col, body.get(col))
                existing.status = as_status
                report["updated"].append(key)
            else:
                db.add(SkillModel(
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
    def delete(db: Session, skill_id: str) -> None:
        skill = SkillService.get(db, skill_id)
        if not skill:
            raise ValueError("Skill not found")
        db.delete(skill)
        db.commit()

    # --------------------------------------------------------------- seeding
    @staticmethod
    def seed_from_filesystem(db: Session) -> int:
        """Import legacy instruction markdown files as published Skills, once.

        Idempotent: only inserts keys not already present, so re-running (or
        running after admins have edited Skills) never clobbers DB state.
        """
        instructions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agents", "instructions",
        )
        if not os.path.isdir(instructions_dir):
            return 0
        existing = {s.key for s in db.query(SkillModel.key).all()}
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
            db.add(SkillModel(
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
            logger.info("Seeded %d skills from filesystem instructions", inserted)
        return inserted

    @staticmethod
    def seed_specs_from_catalog(db: Session) -> int:
        """Attach the code spec catalog to Skills as editable ``graph_spec`` data.

        For each workflow in ``app.v2.graphs.specs.SPECS`` (keyed by RequestType
        value): if a skill with that key exists, backfill its ``graph_spec`` +
        ``request_type`` only when not already set (never clobbers admin edits);
        otherwise create a published skill carrying the graph. Idempotent.
        """
        from app.v2.graphs.specs import SPECS

        inserted = 0
        updated = 0
        for rt, spec in SPECS.items():
            existing = SkillService.get_by_key(db, rt)
            if existing:
                if not existing.graph_spec:
                    existing.graph_spec = spec
                    existing.request_type = existing.request_type or rt
                    updated += 1
                continue
            db.add(SkillModel(
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
    def to_dict(skill: SkillModel, *, include_body: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": skill.id,
            "key": skill.key,
            "name": skill.name,
            "goal": skill.goal,
            "allowed_tools": skill.allowed_tools,
            "policy_ref": skill.policy_ref,
            "params_schema": skill.params_schema,
            "request_type": skill.request_type,
            "status": skill.status,
            "version": skill.version,
            "source": skill.source,
            "created_by": skill.created_by,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
            "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
        }
        if include_body:
            d["instructions_markdown"] = skill.instructions_markdown
            d["graph_spec"] = skill.graph_spec
        return d

    @staticmethod
    def version_to_dict(snap: SkillVersionModel, *, include_body: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": snap.id,
            "skill_id": snap.skill_id,
            "skill_key": snap.skill_key,
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
