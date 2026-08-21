"""Service layer for the dynamic Tool Registry.

Owns the registry table + MCP sources: seeding local tools, discovering MCP tools
with the Service Principal, admin CRUD, and — most importantly — resolving the set
of executable tool objects an agent surface should see for a given user.

This is the data-driven replacement for the hardcoded gating that used to live in
``app/api/v1/agent.py`` (the ``required_role`` filter + the
``_AUTHORING_TOOL_NAMES`` whitelist). The canonical authoring whitelist now lives
here only as a one-time *seed default* (:data:`DEFAULT_AUTHORING_TOOL_NAMES`).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.tool_registry import (
    IDENTITY_OBO,
    IDENTITY_SP,
    SURFACE_MAIN,
    SURFACE_WORKFLOW_AGENT,
    SURFACE_WORKFLOW_EXECUTION,
    TOOL_ORIGIN_LOCAL,
    TOOL_ORIGIN_MCP,
    TOOL_ORIGIN_WORKFLOW,
    McpSourceModel,
    ToolRegistryModel,
)

logger = logging.getLogger(__name__)

# Seed default: tools the workflow-authoring chat surface starts out able to use.
# Mirrors the legacy ``_AUTHORING_TOOL_NAMES`` whitelist. After first seed an admin
# fully controls each tool's surfaces, so this list only shapes the initial state.
DEFAULT_AUTHORING_TOOL_NAMES = frozenset({
    "list_workflow_building_blocks",
    "search_similar_workflows",
    "research_workflow_context",
    "get_workflow",
    "validate_workflow_spec",
    "preview_workflow_spec",
    "evaluate_workflow_spec",
    "save_workflow_draft",
    "save_workflow_tests",
    "list_workflow_tests",
    "run_workflow_tests",
    "publish_workflow",
    "list_context_domains",
    "search_context_catalog",
    "get_context_document",
})

# Workflow build/preview/publish tools that are specific to the authoring studio and
# would only confuse the EDH chat agent (e.g. "preview workflow"). These seed as
# Workflow-only. Everything NOT in this set seeds enabled for EDH — so the shared,
# general-purpose context-catalog tools (which are in the authoring set above but not
# here) end up available to BOTH surfaces.
WORKFLOW_ONLY_TOOL_NAMES = frozenset({
    "list_workflow_building_blocks",
    "search_similar_workflows",
    "research_workflow_context",
    "get_workflow",
    "validate_workflow_spec",
    "preview_workflow_spec",
    "evaluate_workflow_spec",
    "save_workflow_draft",
    "save_workflow_tests",
    "list_workflow_tests",
    "run_workflow_tests",
    "publish_workflow",
})

# Normalize the inconsistent role strings tools declare (e.g. "governance_admin")
# to the canonical internal role names used by ``User.has_role``.
_ROLE_ALIASES = {
    "platform_admin": "Platform Admin",
    "platform admin": "Platform Admin",
    "governance_admin": "Governance Admin",
    "governance admin": "Governance Admin",
    "finance_admin": "Finance Admin",
    "finance admin": "Finance Admin",
    "security_admin": "Security Admin",
    "security admin": "Security Admin",
    "user": "User",
}


def _normalize_role(role: Optional[str]) -> Optional[str]:
    if not role:
        return None
    return _ROLE_ALIASES.get(role.strip().lower(), role.strip())


class ToolRegistryService:
    # --------------------------------------------------------------- seeding
    @staticmethod
    def sync_local_tools(db: Session) -> int:
        """Upsert every locally-defined ``McpTool`` into the unified catalog.

        Walks the union of chat tools (``origin='local'``) and workflow/provider
        tools (``origin='workflow'``) via :mod:`app.tools.catalog`. Idempotent:
        inserts rows for newly-added code tools and refreshes their description/
        schema/mutating metadata, but never overrides an admin's usage/role/identity
        choices on an existing row. Also prunes local/workflow rows whose code tool
        was removed (the catalog is authoritative for those origins; MCP-discovered
        rows are never touched) so a renamed/deleted tool can't linger as a phantom
        duplicate. Returns the number of rows inserted.
        """
        from app.tools import catalog

        # Existing rows for either local definition site (not MCP-discovered).
        existing = {
            r.tool_name: r
            for r in db.query(ToolRegistryModel)
            .filter(ToolRegistryModel.origin.in_([TOOL_ORIGIN_LOCAL, TOOL_ORIGIN_WORKFLOW]))
            .all()
        }
        inserted = 0
        catalog_names: set[str] = set()
        for name, tool, catalog_origin in catalog.all_tools():
            if not name:
                continue
            catalog_names.add(name)
            schema = tool.input_schema if isinstance(getattr(tool, "input_schema", None), dict) else None
            role = _normalize_role(getattr(tool, "required_role", None))
            is_workflow_tool = catalog_origin == TOOL_ORIGIN_WORKFLOW
            row = existing.get(name)
            if row is None:
                if is_workflow_tool:
                    # Provider/graph tools: usable only as workflow building blocks,
                    # never chat-callable by default (preserves the historical
                    # "raw terraform_apply isn't an agent tool" safety invariant).
                    # They execute via the app's provider clients, so they run as the
                    # Service Principal — there is no user token to act on behalf of.
                    main_agent = False
                    workflow_agent = False
                    workflow_execution = True
                    identity_mode = IDENTITY_SP
                else:
                    # Chat tools: the workflow-authoring agent gets the authoring set;
                    # the main agent gets everything except the workflow build/preview/
                    # publish tools. Shared tools (e.g. context catalog) seed for both.
                    # Default to OBO so reads enforce the calling user's grants.
                    main_agent = name not in WORKFLOW_ONLY_TOOL_NAMES
                    workflow_agent = name in DEFAULT_AUTHORING_TOOL_NAMES
                    workflow_execution = False
                    identity_mode = IDENTITY_OBO
                db.add(
                    ToolRegistryModel(
                        id=str(uuid.uuid4()),
                        tool_name=name,
                        origin=catalog_origin,
                        source_id=None,
                        description=getattr(tool, "description", "") or "",
                        input_schema=schema,
                        is_mutating=bool(getattr(tool, "is_mutating", False)),
                        side_effect_class=getattr(tool, "side_effect_class", "read"),
                        enabled=True,
                        enabled_for_main_agent=main_agent,
                        enabled_for_workflow_agent=workflow_agent,
                        enabled_for_workflow_execution=workflow_execution,
                        exposed_via_mcp=bool(getattr(tool, "external", False)),
                        allowed_roles=[role] if role else [],
                        identity_mode=identity_mode,
                    )
                )
                inserted += 1
            else:
                # Refresh code-owned metadata; preserve admin-owned gating.
                row.description = getattr(tool, "description", "") or row.description
                if schema is not None:
                    row.input_schema = schema
                row.is_mutating = bool(getattr(tool, "is_mutating", False))
                row.side_effect_class = getattr(tool, "side_effect_class", row.side_effect_class)
                # Keep origin in sync if a tool moved definition sites.
                if row.origin != catalog_origin:
                    row.origin = catalog_origin

        # Prune orphans: local/workflow rows whose code tool no longer exists
        # (renamed or deleted). Safe because the catalog is authoritative for
        # these origins; remote MCP rows (origin='mcp') were never in `existing`.
        pruned = 0
        for name, row in existing.items():
            if name not in catalog_names:
                db.delete(row)
                pruned += 1

        db.commit()
        if inserted:
            logger.info("ToolRegistry: seeded %d local tool(s)", inserted)
        if pruned:
            logger.info("ToolRegistry: pruned %d orphaned tool(s) removed from code", pruned)
        return inserted

    @staticmethod
    def ensure_seeded(db: Session) -> None:
        """Seed local tools on first use if the registry has none yet."""
        has_local = (
            db.query(ToolRegistryModel.id)
            .filter(ToolRegistryModel.origin.in_([TOOL_ORIGIN_LOCAL, TOOL_ORIGIN_WORKFLOW]))
            .first()
            is not None
        )
        if not has_local:
            ToolRegistryService.sync_local_tools(db)

    # ------------------------------------------------------------- registry CRUD
    @staticmethod
    def list_tools(db: Session) -> List[ToolRegistryModel]:
        return (
            db.query(ToolRegistryModel)
            .order_by(ToolRegistryModel.origin.asc(), ToolRegistryModel.tool_name.asc())
            .all()
        )

    @staticmethod
    def get_tool(db: Session, tool_id: str) -> Optional[ToolRegistryModel]:
        return db.query(ToolRegistryModel).filter(ToolRegistryModel.id == tool_id).first()

    @staticmethod
    def update_tool(db: Session, tool_id: str, **fields: Any) -> ToolRegistryModel:
        row = ToolRegistryService.get_tool(db, tool_id)
        if not row:
            raise ValueError("Tool not found")
        allowed = {
            "enabled",
            "enabled_for_main_agent",
            "enabled_for_workflow_agent",
            "enabled_for_workflow_execution",
            "exposed_via_mcp",
            "allowed_roles",
            "identity_mode",
            "is_mutating",
            "side_effect_class",
            "success_predicate",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            # success_predicate is the one field where `None`/empty is meaningful
            # (it clears the predicate), so it's handled before the generic
            # None-skip and is validated as a $-expression when non-empty.
            if key == "success_predicate":
                if value in (None, "", {}):
                    row.success_predicate = None
                else:
                    from app.workflows.expr import ExprError, validate as _validate_expr
                    try:
                        _validate_expr(value)
                    except ExprError as e:
                        raise ValueError(f"invalid success_predicate: {e}")
                    row.success_predicate = value
                continue
            if value is None:
                continue
            if key == "identity_mode" and value not in (IDENTITY_SP, IDENTITY_OBO):
                raise ValueError(f"identity_mode must be '{IDENTITY_SP}' or '{IDENTITY_OBO}'")
            if key == "allowed_roles":
                value = [_normalize_role(r) for r in value if r]
            setattr(row, key, value)
        db.commit()
        db.refresh(row)
        return row

    # --------------------------------------------------------------- source CRUD
    @staticmethod
    def list_sources(db: Session) -> List[McpSourceModel]:
        return db.query(McpSourceModel).order_by(McpSourceModel.name.asc()).all()

    @staticmethod
    def get_source(db: Session, source_id: str) -> Optional[McpSourceModel]:
        return db.query(McpSourceModel).filter(McpSourceModel.id == source_id).first()

    @staticmethod
    def create_source(
        db: Session,
        *,
        name: str,
        server_url: str,
        kind: str = "managed_functions",
        default_identity_mode: str = IDENTITY_OBO,
        created_by: Optional[str] = None,
    ) -> McpSourceModel:
        if not (name and server_url):
            raise ValueError("name and server_url are required")
        if db.query(McpSourceModel).filter(McpSourceModel.name == name).first():
            raise ValueError(f"A source named '{name}' already exists")
        if default_identity_mode not in (IDENTITY_SP, IDENTITY_OBO):
            raise ValueError("default_identity_mode must be 'sp' or 'obo'")
        source = McpSourceModel(
            id=str(uuid.uuid4()),
            name=name,
            server_url=server_url.strip(),
            kind=kind,
            enabled=True,
            default_identity_mode=default_identity_mode,
            created_by=created_by,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def update_source(db: Session, source_id: str, **fields: Any) -> McpSourceModel:
        source = ToolRegistryService.get_source(db, source_id)
        if not source:
            raise ValueError("Source not found")
        for key in ("name", "server_url", "kind", "enabled", "default_identity_mode"):
            if key in fields and fields[key] is not None:
                setattr(source, key, fields[key])
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def delete_source(db: Session, source_id: str) -> None:
        source = ToolRegistryService.get_source(db, source_id)
        if not source:
            raise ValueError("Source not found")
        # Remove the source's discovered tools, then the source itself.
        db.query(ToolRegistryModel).filter(ToolRegistryModel.source_id == source_id).delete()
        db.delete(source)
        db.commit()

    # ----------------------------------------------------------- MCP discovery
    @staticmethod
    def discover_source(
        db: Session, source_id: str, obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """List tools on a source and upsert them as ``origin='mcp'``.

        Lists On-Behalf-Of the calling admin when ``obo_token`` is provided (so
        AI-Gateway external MCP servers with Per-User OAuth — which the Service
        Principal gets 403 on — are discoverable), falling back to the SP.
        Newly-discovered tools default disabled and unassigned to any surface so
        nothing goes live until an admin opts in. Records sync status on the source.
        Returns ``{"ok", "count", "error"}``.
        """
        source = ToolRegistryService.get_source(db, source_id)
        if not source:
            raise ValueError("Source not found")

        from app.tools.external import mcp_client

        try:
            discovered = mcp_client.list_tools(source.server_url, obo_token=obo_token)
        except Exception as e:  # noqa: BLE001 - surface as a recorded sync failure
            source.last_synced_at = datetime.utcnow()
            source.last_sync_status = "error"
            source.last_sync_error = str(e)
            db.commit()
            logger.warning("ToolRegistry: discovery failed for %s: %s", source.name, e)
            return {"ok": False, "count": 0, "error": str(e)}

        existing = {
            r.tool_name: r
            for r in db.query(ToolRegistryModel)
            .filter(ToolRegistryModel.source_id == source_id)
            .all()
        }
        now = datetime.utcnow()
        for td in discovered:
            name = td.get("name")
            if not name:
                continue
            row = existing.get(name)
            if row is None:
                db.add(
                    ToolRegistryModel(
                        id=str(uuid.uuid4()),
                        tool_name=name,
                        origin=TOOL_ORIGIN_MCP,
                        source_id=source_id,
                        description=td.get("description", ""),
                        input_schema=td.get("input_schema"),
                        is_mutating=bool(td.get("is_mutating", False)),
                        side_effect_class=td.get("side_effect_class", "read"),
                        enabled=False,
                        enabled_for_main_agent=False,
                        enabled_for_workflow_agent=False,
                        enabled_for_workflow_execution=False,
                        allowed_roles=[],
                        identity_mode=source.default_identity_mode,
                        discovered_at=now,
                    )
                )
            else:
                # Refresh server-owned metadata; preserve admin gating.
                row.description = td.get("description", row.description)
                if td.get("input_schema") is not None:
                    row.input_schema = td.get("input_schema")
                row.discovered_at = now
        source.last_synced_at = now
        source.last_sync_status = "ok"
        source.last_sync_error = None
        source.last_tool_count = len(discovered)
        db.commit()
        logger.info("ToolRegistry: discovered %d tool(s) from %s", len(discovered), source.name)
        return {"ok": True, "count": len(discovered), "error": None}

    @staticmethod
    def quick_add_source(
        db: Session,
        *,
        name: str,
        server_url: str,
        kind: str = "managed_functions",
        default_identity_mode: str = IDENTITY_OBO,
        created_by: Optional[str] = None,
        auto_enable_read_only: bool = True,
        obo_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register an MCP server and bring it online in one shot.

        Creates the source, immediately discovers its tools, and (by default)
        enables the newly-discovered *read-only* tools for the main agent so the
        server is usable the moment it's added — no separate "now click Sync,
        then toggle each tool" steps. Mutating tools are left disabled so an admin
        explicitly opts into anything with side effects. Returns the created
        source, the discovery result, and how many tools were auto-enabled.
        """
        source = ToolRegistryService.create_source(
            db,
            name=name,
            server_url=server_url,
            kind=kind,
            default_identity_mode=default_identity_mode,
            created_by=created_by,
        )
        result = ToolRegistryService.discover_source(db, source.id, obo_token=obo_token)

        enabled_count = 0
        if result.get("ok") and auto_enable_read_only:
            rows = (
                db.query(ToolRegistryModel)
                .filter(ToolRegistryModel.source_id == source.id)
                .all()
            )
            for row in rows:
                # Read-only = no declared side effects. Anything mutating stays off
                # until an admin opts in (matches the discover_source default).
                if not row.is_mutating and row.side_effect_class == "read":
                    row.enabled = True
                    row.enabled_for_main_agent = True
                    enabled_count += 1
            if enabled_count:
                db.commit()
                logger.info(
                    "ToolRegistry: auto-enabled %d read-only tool(s) from %s for the main agent",
                    enabled_count, source.name,
                )

        db.refresh(source)
        return {
            "source": ToolRegistryService.source_to_dict(source),
            "discovery": result,
            "auto_enabled": enabled_count,
        }

    @staticmethod
    def list_workspace_mcp_candidates(
        db: Session, obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """MCP servers discoverable in the workspace via the SDK (for the picker).

        Lists On-Behalf-Of the caller when ``obo_token`` is provided so per-user
        connections/spaces show up. Flags candidates already registered (by
        matching ``server_url`` against existing sources) so the UI can
        annotate/disable them.

        Returns ``{sources, errors, identity}``: the discovery failures and the
        identity used travel with the results so an empty picker can explain
        itself instead of guessing at a cause.
        """
        from app.tools.external import mcp_client

        existing_urls = {
            (s.server_url or "").rstrip("/")
            for s in db.query(McpSourceModel.server_url).all()
        }
        found = mcp_client.list_workspace_mcp_servers(obo_token=obo_token)
        for c in found.sources:
            c["already_registered"] = (c.get("server_url") or "").rstrip("/") in existing_urls
        return {
            "sources": found.sources,
            "errors": found.errors,
            "identity": found.identity,
        }

    # ------------------------------------------------------ surface resolution
    @staticmethod
    def _user_allowed(allowed_roles: Optional[list], user) -> bool:
        if not allowed_roles:
            return True
        return any(user.has_role(r) for r in allowed_roles if r)

    # Accept the legacy surface aliases ("edh"/"workflow") plus the canonical names.
    _SURFACE_COLUMNS = {
        SURFACE_MAIN: ToolRegistryModel.enabled_for_main_agent,
        "edh": ToolRegistryModel.enabled_for_main_agent,
        SURFACE_WORKFLOW_AGENT: ToolRegistryModel.enabled_for_workflow_agent,
        "workflow": ToolRegistryModel.enabled_for_workflow_agent,
        SURFACE_WORKFLOW_EXECUTION: ToolRegistryModel.enabled_for_workflow_execution,
    }

    @staticmethod
    def tools_for_surface(db: Session, surface: str, user) -> List[ToolRegistryModel]:
        """Registry rows enabled for ``surface`` and permitted for ``user``."""
        ToolRegistryService.ensure_seeded(db)
        col = ToolRegistryService._SURFACE_COLUMNS.get(
            surface, ToolRegistryModel.enabled_for_main_agent
        )
        rows = (
            db.query(ToolRegistryModel)
            .filter(ToolRegistryModel.enabled.is_(True), col.is_(True))
            .all()
        )
        return [r for r in rows if ToolRegistryService._user_allowed(r.allowed_roles, user)]

    @staticmethod
    def resolve_tools_for_surface(db: Session, surface: str, user) -> List[Any]:
        """Executable tool objects (local ``McpTool`` + remote adapters) for a surface.

        Local + workflow rows resolve to the real in-process ``McpTool`` (via the
        unified catalog); MCP rows build a ``RemoteMcpTool`` adapter so all flow
        through the same ToolExecutor.
        """
        from app.tools import catalog

        rows = ToolRegistryService.tools_for_surface(db, surface, user)

        # Cache sources referenced by the enabled MCP rows.
        source_ids = {r.source_id for r in rows if r.origin == TOOL_ORIGIN_MCP and r.source_id}
        sources: Dict[str, McpSourceModel] = {}
        if source_ids:
            for s in (
                db.query(McpSourceModel).filter(McpSourceModel.id.in_(source_ids)).all()
            ):
                sources[s.id] = s

        resolved: List[Any] = []
        for row in rows:
            if row.origin in (TOOL_ORIGIN_LOCAL, TOOL_ORIGIN_WORKFLOW):
                tool = catalog.get_by_name(row.tool_name)
                if tool is not None:
                    resolved.append(tool)
                continue
            source = sources.get(row.source_id) if row.source_id else None
            if not source or not source.enabled:
                continue
            from app.tools.external.mcp_remote import RemoteMcpTool, mcp_server_label

            resolved.append(
                RemoteMcpTool(
                    name=row.tool_name,
                    server_url=source.server_url,
                    description=row.description or "",
                    input_schema=row.input_schema,
                    is_mutating=row.is_mutating,
                    side_effect_class=row.side_effect_class,
                    identity_mode=row.identity_mode,
                    success_predicate=getattr(row, "success_predicate", None),
                    # URL-derived label (NOT the admin-chosen source.name) so it
                    # matches Command Center's server-qualified tool ids.
                    server_label=mcp_server_label(source.server_url),
                )
            )
        return resolved

    # ------------------------------------------------------------- serializers
    @staticmethod
    def tool_to_dict(row: ToolRegistryModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "tool_name": row.tool_name,
            "origin": row.origin,
            "source_id": row.source_id,
            "description": row.description,
            "is_mutating": row.is_mutating,
            "side_effect_class": row.side_effect_class,
            "enabled": row.enabled,
            "enabled_for_main_agent": row.enabled_for_main_agent,
            "enabled_for_workflow_agent": row.enabled_for_workflow_agent,
            "enabled_for_workflow_execution": row.enabled_for_workflow_execution,
            "exposed_via_mcp": row.exposed_via_mcp,
            "allowed_roles": row.allowed_roles or [],
            "identity_mode": row.identity_mode,
            "success_predicate": getattr(row, "success_predicate", None),
            "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def source_to_dict(row: McpSourceModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "server_url": row.server_url,
            "kind": row.kind,
            "enabled": row.enabled,
            "default_identity_mode": row.default_identity_mode,
            "created_by": row.created_by,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            "last_sync_status": row.last_sync_status,
            "last_sync_error": row.last_sync_error,
            "last_tool_count": row.last_tool_count,
        }
