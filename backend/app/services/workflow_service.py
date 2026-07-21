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

from app.db.workflow import WorkflowModel, WorkflowVersionModel, WorkflowTombstoneModel

logger = logging.getLogger(__name__)

_GOAL_RE = re.compile(r"\*\*Goal\*\*:\s*(.*?)(?:\n|$)")

# Portable bundle format tag (bumped if the export shape changes).
BUNDLE_FORMAT = "selfservice.workflows/v1"

# Body fields that travel with a workflow across envs / snapshots (no ids/status/version).
_BODY_FIELDS = (
    "name", "goal", "instructions_markdown", "allowed_tools",
    "policy_ref", "params_schema", "graph_spec", "request_type",
)

# Obsolete instruction-only workflow keys mapped to the executable catalog
# workflow that superseded them. Early builds seeded agent *instructions* under
# one key while the runnable graph catalog later landed under a different key,
# leaving two published rows for the same workflow: the legacy one with rich
# instructions but no graph (so a request created for it dies with "no workflow
# graph registered"), and the catalog one with the graph but blank instructions.
# :meth:`consolidate_legacy_workflows` reconciles them. Add a row here whenever a
# workflow is renamed so the rename self-heals across environments.
LEGACY_WORKFLOW_ALIASES = {
    "request_data_access": "data_access_request",
    "create_workspace": "workspace_provision",
    "onboarding": "project_onboarding",
    "create_catalog_schema": "catalog_schema_table",
    "data_deduplication_sentinel": "asset_deduplication",
}


def _rewrite_subworkflow_refs(node: Any, alias_map: Dict[str, str]) -> bool:
    """Recursively remap ``"ref"`` values in a graph_spec via ``alias_map``.

    Returns True if anything changed. Walks the whole spec (not just top-level
    stages) so nested/compound subgraphs are covered too.
    """
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "ref" and isinstance(value, str) and value in alias_map:
                node[key] = alias_map[value]
                changed = True
            elif _rewrite_subworkflow_refs(value, alias_map):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _rewrite_subworkflow_refs(item, alias_map):
                changed = True
    return changed


class WorkflowService:
    # ------------------------------------------------------------------ reads
    @staticmethod
    def list_workflows(db: Session, *, include_drafts: bool = True) -> List[WorkflowModel]:
        q = db.query(WorkflowModel)
        if not include_drafts:
            q = q.filter(WorkflowModel.status == "published")
        return q.order_by(WorkflowModel.key.asc()).all()

    @staticmethod
    def list_published(db: Session, *, include_disabled: bool = False) -> List[WorkflowModel]:
        """Published workflows the agent can see.

        Excludes operationally *disabled* workflows by default (the "turn off"
        kill switch), so a disabled workflow disappears from the agent's
        capabilities list and request-type routing while its published
        definition/version is left untouched. Pass ``include_disabled=True`` for
        admin/inventory views that need to show and re-enable them.
        """
        q = db.query(WorkflowModel).filter(WorkflowModel.status == "published")
        if not include_disabled:
            q = q.filter(WorkflowModel.disabled.isnot(True))
        return q.order_by(WorkflowModel.key.asc()).all()

    @staticmethod
    def get(db: Session, workflow_id: str) -> Optional[WorkflowModel]:
        return db.query(WorkflowModel).filter(WorkflowModel.id == workflow_id).first()

    @staticmethod
    def get_by_key(db: Session, key: str, *, published_only: bool = False) -> Optional[WorkflowModel]:
        q = db.query(WorkflowModel).filter(WorkflowModel.key == key)
        if published_only:
            # "published_only" is the agent-facing lookup (e.g. instructions), so a
            # disabled workflow is treated as not-published (turned off).
            q = q.filter(
                WorkflowModel.status == "published",
                WorkflowModel.disabled.isnot(True),
            )
        return q.first()

    # ------------------------------------------------- type registry / lookup
    @staticmethod
    def known_request_types(db: Session) -> set:
        """Every request-type string the system will accept for a new request.

        The data-driven replacement for the old ``RequestType`` enum gate: union
        of published workflows' ``request_type`` + ``key`` values, the bundled
        JSON spec catalog keys, and the slim system constants. Authoring and
        publishing a workflow (UI or agent) is all it takes to make a new type
        valid — no code change, no enum entry.
        """
        known: set = set()
        try:
            for wf in WorkflowService.list_published(db):
                if wf.request_type:
                    known.add(wf.request_type)
                if wf.key:
                    known.add(wf.key)
        except Exception as e:  # noqa: BLE001 - validation must not hard-fail on a read
            logger.debug("known_request_types: DB lookup failed: %s", e)
        try:
            from app.workflows.graphs.specs import SPECS
            known.update(SPECS.keys())
        except Exception as e:  # noqa: BLE001
            logger.debug("known_request_types: catalog load failed: %s", e)
        from app.models.request import RequestType
        known.update(rt.value for rt in RequestType)
        # Honor the operational kill switch: a disabled published workflow is
        # "off", so drop its key/request_type from the routable set even when the
        # same string is also a bundled catalog spec or enum value. Without this a
        # disabled catalog-backed workflow would still validate as a known type.
        try:
            disabled = (
                db.query(WorkflowModel)
                .filter(
                    WorkflowModel.status == "published",
                    WorkflowModel.disabled.is_(True),
                )
                .all()
            )
            for wf in disabled:
                known.discard(wf.request_type)
                known.discard(wf.key)
        except Exception as e:  # noqa: BLE001 - never hard-fail validation on a read
            logger.debug("known_request_types: disabled-filter lookup failed: %s", e)
        return known

    @staticmethod
    def is_known_request_type(db: Session, request_type: Optional[str]) -> bool:
        return bool(request_type) and request_type in WorkflowService.known_request_types(db)

    @staticmethod
    def effective_spec(db: Session, request_type: str) -> Optional[Dict[str, Any]]:
        """The ``graph_spec`` that will actually run for ``request_type``.

        Prefers a published DB ``graph_spec`` (the no-code override) and falls
        back to the bundled code catalog. Returns ``None`` for instruction-only
        or unknown types (which have no executable graph).
        """
        try:
            wf = (
                db.query(WorkflowModel)
                .filter(
                    WorkflowModel.request_type == request_type,
                    WorkflowModel.status == "published",
                    WorkflowModel.graph_spec.isnot(None),
                )
                .first()
            )
            if wf is not None:
                # An operationally disabled workflow is turned off: return no spec
                # rather than silently falling back to the bundled catalog graph,
                # so the kill switch actually stops execution for its type.
                if bool(getattr(wf, "disabled", False)):
                    logger.info(
                        "effective_spec: workflow '%s' (type=%s) is disabled; "
                        "returning no spec.", wf.key, request_type,
                    )
                    return None
                if wf.graph_spec:
                    return wf.graph_spec
        except Exception as e:  # noqa: BLE001
            logger.debug("effective_spec: DB lookup failed for %s: %s", request_type, e)
        from app.workflows.graphs.specs import SPECS
        return SPECS.get(request_type)

    @staticmethod
    def spec_requires_training(db: Session, request_type: str) -> bool:
        """True if the effective spec has a ``training`` gate.

        Drives a request's ``requires_training`` flag from the workflow's own
        definition instead of a hardcoded per-type check.
        """
        spec = WorkflowService.effective_spec(db, request_type)
        if not spec:
            return False
        return any(
            isinstance(s, dict) and s.get("kind") == "gate" and s.get("type") == "training"
            for s in (spec.get("stages") or [])
        )

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
            instructions_markdown=WorkflowService._synced_instructions(
                fields.get("instructions_markdown"),
                fields.get("graph_spec"),
                fields.get("request_type"),
                goal=fields.get("goal"),
            ),
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
        # Re-creating a previously-deleted key is a deliberate revival: clear its
        # tombstone so it behaves normally (and re-seeds if it's a catalog key).
        WorkflowService._clear_tombstone(db, key)
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def update(db: Session, workflow_id: str, **fields) -> WorkflowModel:
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        # Editing the definition of a catalog-seeded workflow forks it to
        # user-owned, so the startup catalog re-sync (seed_specs_from_catalog)
        # never overwrites the admin's changes.
        _definition_cols = ("goal", "instructions_markdown", "allowed_tools",
                             "policy_ref", "params_schema", "graph_spec")
        if workflow.source == "seed" and any(
            col in fields and fields[col] is not None for col in _definition_cols
        ):
            workflow.source = "user"
        for col in ("name", "goal", "instructions_markdown", "allowed_tools",
                    "policy_ref", "params_schema", "graph_spec", "request_type", "status"):
            if col in fields and fields[col] is not None:
                setattr(workflow, col, fields[col])
        # Keep the stored execute_workflow block in sync with the graph: whether
        # the prose or the graph changed, re-derive the canonical Execution block
        # so the editor always shows the call and it can't drift from the spec.
        workflow.instructions_markdown = WorkflowService._synced_instructions(
            workflow.instructions_markdown, workflow.graph_spec, workflow.request_type,
            goal=workflow.goal,
        )
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def _synced_instructions(
        instructions_markdown: Optional[str],
        graph_spec: Optional[dict],
        request_type: Optional[str],
        *,
        goal: Optional[str] = None,
    ) -> Optional[str]:
        """Normalize a workflow's instructions on every save so they're never blank
        and always carry the canonical, graph-derived ``execute_workflow`` block.

        - No graph to derive from (legacy/code workflow): leave instructions as-is.
        - Graph present + prose given: splice the canonical Execution block in
          (persisted, so the editor shows the call and it can't drift from the spec).
        - Graph present + blank prose: generate a baseline from the spec, so EVERY
          save path (agent tool, the editor's manual Save button, import) gets the
          "instructions are never empty" guarantee — not just the agent tool.
        """
        spec = graph_spec or {}
        if not spec.get("stages"):
            return instructions_markdown
        from app.workflows.instructions import (
            render_instructions_markdown,
            with_canonical_execution,
        )

        if not (instructions_markdown and instructions_markdown.strip()):
            return render_instructions_markdown(spec, request_type=request_type, goal=goal)
        return with_canonical_execution(
            instructions_markdown, spec, request_type=request_type
        )

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

    @staticmethod
    def set_disabled(db: Session, workflow_id: str, disabled: bool) -> WorkflowModel:
        """Flip the operational kill switch on a workflow (turn off / back on).

        This is deliberately NOT routed through :meth:`update`: it's an
        operational toggle, not an authoring edit. It never bumps the version,
        never forks a seed workflow to ``source="user"``, and never touches the
        definition — so it stays safe (and, at the API layer, lock-exempt) to use
        in a locked prod environment.
        """
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        workflow.disabled = bool(disabled)
        db.commit()
        db.refresh(workflow)
        return workflow

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
        prune: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert workflows from a bundle (by key). Returns a per-workflow report.

        Defaults to importing as **draft** so a promoted workflow is reviewed and
        tested in the target env before it is published.

        When ``prune`` is True, this also DELETES workflows in the target whose
        ``key`` is not present in the bundle — the way to make a deletion in a
        source env (dev) propagate to a locked target (prod), where manual delete
        is blocked. This includes code-seeded rows (``source == 'seed'``): each
        pruned key is tombstoned so the startup seeders don't re-create it. A full
        export contains all surviving workflows (seeds included), so only the ones
        actually deleted in the source env are removed here.
        Destructive, so it is opt-in and surfaced with a confirmation in the UI.
        """
        from app.workflows.spec_loader import SpecError, validate_spec_dict

        if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"Unrecognized bundle format (expected {BUNDLE_FORMAT})")
        entries = bundle.get("workflows")
        if not isinstance(entries, list):
            raise ValueError("Bundle 'workflows' must be a list")
        if as_status not in ("draft", "published"):
            raise ValueError("as_status must be 'draft' or 'published'")

        report: Dict[str, Any] = {"created": [], "updated": [], "skipped": [], "errors": [], "pruned": []}
        bundle_keys: set = set()
        for entry in entries:
            key = (entry or {}).get("key")
            if not key:
                report["errors"].append({"key": None, "error": "missing key"})
                continue
            bundle_keys.add(key)
            try:
                spec = entry.get("graph_spec")
                if spec is not None:
                    validate_spec_dict(spec)  # reject malformed graphs at the border
            except SpecError as e:
                report["errors"].append({"key": key, "error": f"invalid graph_spec: {e}"})
                continue

            body = {f: entry.get(f) for f in _BODY_FIELDS}
            # An imported key is deliberately present — revive it by clearing any
            # tombstone so it's treated normally (and re-seeds if it's a catalog key).
            WorkflowService._clear_tombstone(db, key)
            existing = WorkflowService.get_by_key(db, key)
            if existing:
                if not overwrite:
                    report["skipped"].append(key)
                    continue
                for col in _BODY_FIELDS:
                    setattr(existing, col, body.get(col))
                existing.status = as_status
                # Never let an import persist a graph with blank instructions.
                existing.instructions_markdown = WorkflowService._synced_instructions(
                    existing.instructions_markdown, existing.graph_spec,
                    existing.request_type, goal=existing.goal,
                )
                report["updated"].append(key)
            else:
                db.add(WorkflowModel(
                    id=str(uuid.uuid4()),
                    key=key,
                    name=body.get("name") or key,
                    goal=body.get("goal"),
                    instructions_markdown=WorkflowService._synced_instructions(
                        body.get("instructions_markdown"),
                        body.get("graph_spec"),
                        body.get("request_type"),
                        goal=body.get("goal"),
                    ),
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

        if prune and bundle_keys:
            # Delete every workflow the bundle no longer contains so a source-env
            # deletion propagates here — INCLUDING code-seeded rows. Pruned keys are
            # tombstoned below so the startup seeders don't just re-create them on
            # the next boot (that's why we can safely prune seeds now; before the
            # tombstone existed we had to skip source='seed'). Guarded by a
            # non-empty bundle so an empty/garbled import can't wipe everything, and
            # a full export always contains the surviving seeds (so they're kept).
            orphans = (
                db.query(WorkflowModel)
                .filter(WorkflowModel.key.notin_(bundle_keys))
                .all()
            )
            for wf in orphans:
                db.query(WorkflowVersionModel).filter(
                    WorkflowVersionModel.workflow_id == wf.id
                ).delete(synchronize_session=False)
                pruned_key = wf.key
                db.delete(wf)
                # Tombstone pruned keys too: a catalog key (present in SPECS) would
                # otherwise be re-seeded on the next boot, undoing the prune.
                if pruned_key and db.get(WorkflowTombstoneModel, pruned_key) is None:
                    db.add(WorkflowTombstoneModel(key=pruned_key))
                report["pruned"].append(pruned_key)

        db.commit()
        return report

    @staticmethod
    def delete(db: Session, workflow_id: str, *, deleted_by: Optional[str] = None) -> None:
        workflow = WorkflowService.get(db, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        key = workflow.key
        db.delete(workflow)
        # Tombstone the key so the startup seeders don't re-create a
        # bundled/catalog workflow the admin intentionally deleted (otherwise it
        # "pops back in" on the next deploy). Re-creating the key (UI create or
        # bundle import) clears the tombstone. Idempotent upsert.
        if key and db.get(WorkflowTombstoneModel, key) is None:
            db.add(WorkflowTombstoneModel(key=key, deleted_by=deleted_by))
        db.commit()

    @staticmethod
    def _tombstoned_keys(db: Session) -> set:
        """Keys the admin deleted that must not be re-seeded on boot."""
        return {row.key for row in db.query(WorkflowTombstoneModel.key).all()}

    @staticmethod
    def _clear_tombstone(db: Session, key: str) -> None:
        """Drop a key's tombstone so a re-created/imported workflow seeds again."""
        if not key:
            return
        row = db.get(WorkflowTombstoneModel, key)
        if row is not None:
            db.delete(row)

    # --------------------------------------------------------------- seeding
    @staticmethod
    def seed_from_filesystem(db: Session) -> int:
        """Seed / re-sync workflow instruction markdown from the bundled ``.md`` files.

        For each ``agents/instructions/*.md`` file: insert a published workflow when
        the key is new, otherwise re-sync ``instructions_markdown`` (and the parsed
        ``goal``) for ``source="seed"`` rows whose stored prose differs from the file.
        This mirrors :meth:`seed_specs_from_catalog` so bundled instruction edits
        actually deploy (the DB row persists across deploys, so a one-time insert is
        not enough). Admin edits fork the row to ``source="user"`` (see :meth:`update`),
        which is never touched here. Idempotent: unchanged files produce no writes.
        """
        instructions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agents", "instructions",
        )
        if not os.path.isdir(instructions_dir):
            return 0
        existing = {w.key: w for w in db.query(WorkflowModel).all()}
        tombstoned = WorkflowService._tombstoned_keys(db)
        inserted = 0
        updated = 0
        for filename in sorted(os.listdir(instructions_dir)):
            if not filename.endswith(".md"):
                continue
            key = filename[:-3]
            # Respect an intentional deletion — don't re-seed a tombstoned key.
            if key in tombstoned and key not in existing:
                continue
            try:
                with open(os.path.join(instructions_dir, filename), "r") as f:
                    content = f.read()
            except Exception as e:  # noqa: BLE001
                logger.warning("Skipping instruction %s: %s", filename, e)
                continue
            goal_match = _GOAL_RE.search(content)
            goal_val = goal_match.group(1).strip() if goal_match else None

            row = existing.get(key)
            if row is None:
                db.add(WorkflowModel(
                    id=str(uuid.uuid4()),
                    key=key,
                    name=key.replace("_", " ").title(),
                    goal=goal_val,
                    instructions_markdown=content,
                    status="published",
                    source="seed",
                    created_by="system",
                ))
                inserted += 1
                continue

            # Re-sync catalog-managed (seed) rows; never clobber admin edits.
            if row.source == "seed" and (row.instructions_markdown or "") != content:
                row.instructions_markdown = content
                if goal_val:
                    row.goal = goal_val
                db.add(row)
                updated += 1
        if inserted or updated:
            db.commit()
            logger.info("Seeded workflow instructions: %d new, %d re-synced", inserted, updated)
        return inserted + updated

    @staticmethod
    def seed_specs_from_catalog(db: Session) -> int:
        """Attach the code spec catalog to Workflows as editable ``graph_spec`` data.

        For each workflow in ``app.workflows.graphs.specs.SPECS`` (keyed by RequestType
        value): if a workflow with that key exists, backfill its ``graph_spec`` +
        ``request_type`` only when not already set (never clobbers admin edits);
        otherwise create a published workflow carrying the graph. Idempotent.
        """
        from app.workflows.graphs.specs import SPECS

        tombstoned = WorkflowService._tombstoned_keys(db)
        inserted = 0
        updated = 0
        for rt, spec in SPECS.items():
            existing = WorkflowService.get_by_key(db, rt)
            # Respect an intentional deletion — don't re-seed a tombstoned key.
            if not existing and rt in tombstoned:
                continue
            if existing:
                touched = False
                if not existing.graph_spec:
                    # Backfill a missing spec regardless of source.
                    existing.graph_spec = spec
                    existing.request_type = existing.request_type or rt
                    touched = True
                elif existing.source == "seed" and existing.graph_spec != spec:
                    # Re-sync catalog-managed workflows so bundled spec changes
                    # actually deploy (the DB row persists across deploys, so a
                    # one-time backfill is not enough). Admin edits fork the
                    # workflow to source="user" (see update()), which we never
                    # touch here — so this only refreshes untouched seeds.
                    existing.graph_spec = spec
                    existing.request_type = existing.request_type or rt
                    touched = True
                # Guarantee instructions are never blank when a graph exists.
                # Catalog seeds carry ONLY a graph_spec (there's no bundled .md for
                # keys like simple_email/tag_change), so without this they persist
                # with NULL instructions_markdown and render as a completely blank
                # Details page. Generate the graph-derived baseline whenever prose is
                # missing; we never clobber existing prose (an admin edit forks the
                # row to source="user"), so this only heals empty rows.
                if existing.graph_spec and not (existing.instructions_markdown or "").strip():
                    generated = WorkflowService._synced_instructions(
                        None, existing.graph_spec, existing.request_type or rt, goal=existing.goal
                    )
                    # Only assign when a real baseline was produced. A stage-less
                    # spec yields nothing (see _synced_instructions), so guarding
                    # here keeps re-seeding idempotent instead of re-touching those
                    # rows on every boot.
                    if generated and generated.strip():
                        existing.instructions_markdown = generated
                        touched = True
                if touched:
                    updated += 1
                continue
            db.add(WorkflowModel(
                id=str(uuid.uuid4()),
                key=rt,
                name=rt.replace("_", " ").title(),
                goal=None,
                graph_spec=spec,
                # Derive a baseline playbook up front so a freshly-seeded catalog
                # workflow is never blank (mirrors create()/update()).
                instructions_markdown=WorkflowService._synced_instructions(
                    None, spec, rt, goal=None
                ),
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

    @staticmethod
    def consolidate_legacy_workflows(db: Session) -> int:
        """Retire obsolete instruction-only workflow keys onto their catalog twins.

        For each ``LEGACY_WORKFLOW_ALIASES`` entry this:
          1. carries the legacy row's instructions over to the catalog row when
             the catalog row has none (never clobbering existing prose),
          2. rewrites any other workflow's subworkflow ``ref`` that still points
             at the legacy key (e.g. project_onboarding -> ``create_workspace``),
          3. deletes the orphaned legacy row (and its version snapshots).

        Idempotent: once the legacy rows are gone there's nothing left to do, so
        it's safe to run on every boot. Runs after both seed passes so the
        catalog twin is guaranteed to exist.
        """
        from sqlalchemy.orm.attributes import flag_modified

        changed = 0

        # 1 + 3: fold each legacy row into its catalog twin, then delete it.
        for legacy_key, catalog_key in LEGACY_WORKFLOW_ALIASES.items():
            legacy = WorkflowService.get_by_key(db, legacy_key)
            if not legacy:
                continue
            target = WorkflowService.get_by_key(db, catalog_key)
            if not target:
                # Catalog twin missing (unexpected once seed_specs has run): leave
                # the legacy row in place rather than silently dropping content.
                logger.warning(
                    "consolidate_legacy_workflows: no catalog twin '%s' for legacy "
                    "'%s'; leaving legacy row intact", catalog_key, legacy_key,
                )
                continue
            if legacy.instructions_markdown and not (target.instructions_markdown or "").strip():
                target.instructions_markdown = legacy.instructions_markdown
                db.add(target)
            # Carry the goal too — it's what surfaces the workflow in the agent's
            # capabilities menu. Without it the catalog twin is invisible and the
            # agent routes to a sound-alike (e.g. data_access -> batch_data_access).
            if legacy.goal and not (target.goal or "").strip():
                target.goal = legacy.goal
                db.add(target)
            db.query(WorkflowVersionModel).filter(
                WorkflowVersionModel.workflow_id == legacy.id
            ).delete(synchronize_session=False)
            db.delete(legacy)
            changed += 1

        # Backfill goals for any published workflow that has instructions with a
        # "**Goal**:" line but no stored goal (e.g. catalog-seeded twins on an env
        # whose legacy rows were already removed). The goal is what lists the
        # workflow in the agent's menu, so a missing one silently hides it.
        for workflow in (
            db.query(WorkflowModel)
            .filter(WorkflowModel.status == "published", WorkflowModel.instructions_markdown.isnot(None))
            .all()
        ):
            if (workflow.goal or "").strip():
                continue
            match = _GOAL_RE.search(workflow.instructions_markdown or "")
            if match and match.group(1).strip():
                workflow.goal = match.group(1).strip()
                db.add(workflow)
                changed += 1

        # 2: repair dangling subworkflow refs in every remaining workflow.
        for workflow in db.query(WorkflowModel).filter(WorkflowModel.graph_spec.isnot(None)).all():
            spec = workflow.graph_spec
            if isinstance(spec, dict) and _rewrite_subworkflow_refs(spec, LEGACY_WORKFLOW_ALIASES):
                workflow.graph_spec = spec
                flag_modified(workflow, "graph_spec")
                db.add(workflow)
                changed += 1

        if changed:
            db.commit()
            logger.info("Consolidated %d legacy workflow alias(es) / refs", changed)
        return changed

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
            "disabled": bool(workflow.disabled),
            "version": workflow.version,
            "source": workflow.source,
            "created_by": workflow.created_by,
            "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
            "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
        }
        # Composition is derived from the spec so the UI can badge atomic vs
        # compound (nested-subgraph) workflows in the list without the full body.
        from app.workflows.spec_loader import is_compound_spec, subworkflow_refs

        spec = workflow.graph_spec
        compound = is_compound_spec(spec)
        d["composition"] = "compound" if compound else "atomic"
        d["subworkflow_refs"] = subworkflow_refs(spec)
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
