"""Tool Registry API (dynamic agent tool governance).

Admin CRUD for the data-driven tool registry: toggle each tool per usage context
(main chat agent, workflow-authoring chat, workflow execution), set allowed roles
+ SP/OBO identity, and register + sync Databricks MCP servers (discovered with the
Service Principal).

Per-tool gating writes require Platform/Governance Admin. MCP *source* management
and discovery (which touch SP/OBO + external connections) require Platform Admin.
Reads are available to those admin roles.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.db.tool_registry import MCP_SOURCE_KINDS
from app.models.user import User
from app.services.tool_registry_service import ToolRegistryService

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]
_READ_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("tool_registry"):
        raise HTTPException(status_code=404, detail="Tool registry is not enabled")


class ToolUpdate(BaseModel):
    enabled: Optional[bool] = None
    enabled_for_main_agent: Optional[bool] = None
    enabled_for_workflow_agent: Optional[bool] = None
    enabled_for_workflow_execution: Optional[bool] = None
    allowed_roles: Optional[List[str]] = None
    identity_mode: Optional[str] = Field(default=None, description="'sp' or 'obo'")
    is_mutating: Optional[bool] = None
    side_effect_class: Optional[str] = None
    success_predicate: Optional[Any] = Field(
        default=None,
        description=(
            "$-expression evaluated against {result} to decide tool success. "
            "Send null/empty to clear. e.g. {\"$eq\": [{\"$var\": \"result.state\"}, \"submitted\"]}"
        ),
    )


class SourceCreate(BaseModel):
    name: str
    server_url: str
    kind: str = "managed_functions"
    default_identity_mode: str = "obo"


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    server_url: Optional[str] = None
    kind: Optional[str] = None
    enabled: Optional[bool] = None
    default_identity_mode: Optional[str] = None


@router.get("")
def list_registry(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_READ_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Full registry view: every tool row + every MCP source."""
    ToolRegistryService.ensure_seeded(db)
    tools = [ToolRegistryService.tool_to_dict(t) for t in ToolRegistryService.list_tools(db)]
    sources = [ToolRegistryService.source_to_dict(s) for s in ToolRegistryService.list_sources(db)]
    return {"tools": tools, "sources": sources, "source_kinds": list(MCP_SOURCE_KINDS)}


@router.post("/sync-local")
def sync_local(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Re-seed registry rows for locally-defined tools (idempotent)."""
    inserted = ToolRegistryService.sync_local_tools(db)
    return {"ok": True, "inserted": inserted}


@router.post("/sources")
def create_source(
    *,
    db: Session = Depends(deps.get_db),
    body: SourceCreate,
    current_user: User = Depends(deps.require_role("Platform Admin")),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        source = ToolRegistryService.create_source(
            db,
            name=body.name,
            server_url=body.server_url,
            kind=body.kind,
            default_identity_mode=body.default_identity_mode,
            created_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ToolRegistryService.source_to_dict(source)


@router.put("/sources/{source_id}")
def update_source(
    *,
    source_id: str,
    db: Session = Depends(deps.get_db),
    body: SourceUpdate,
    current_user: User = Depends(deps.require_role("Platform Admin")),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        source = ToolRegistryService.update_source(db, source_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ToolRegistryService.source_to_dict(source)


@router.delete("/sources/{source_id}")
def delete_source(
    source_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        ToolRegistryService.delete_source(db, source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/sources/{source_id}/sync")
def sync_source(
    source_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_role("Platform Admin")),
    _: None = Depends(_require_feature),
) -> Any:
    """Discover tools on a source with the SP and upsert them into the registry."""
    try:
        result = ToolRegistryService.discover_source(db, source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.put("/{tool_id}")
def update_tool(
    *,
    tool_id: str,
    db: Session = Depends(deps.get_db),
    body: ToolUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        row = ToolRegistryService.update_tool(db, tool_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ToolRegistryService.tool_to_dict(row)
