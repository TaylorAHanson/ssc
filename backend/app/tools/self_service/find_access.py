"""
Tool to look up who holds what in the Access Rules ledger (ADR 0005 D9).
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool


class FindAccessInput(BaseModel):
    securable_type: Optional[str] = Field(
        None, description="Workbook spelling, e.g. 'catalog', 'schema', 'table', 'workspace', 'folder'.")
    catalog: Optional[str] = Field(None, description="Catalog name.")
    schema_name: Optional[str] = Field(None, description="Schema name (inside the catalog).")
    object_name: Optional[str] = Field(None, description="Table, view, volume, folder or other object name.")
    workspace_name: Optional[str] = Field(None, description="Workspace name, for workspace securables.")
    principal_name: Optional[str] = Field(
        None, description="A group or service principal name (partial match) to list what it holds.")
    environment: Optional[str] = Field(None, description="Environment key or alias, e.g. 'dev', 'prd'.")
    limit: int = Field(default=25, ge=1, le=100, description="Max matches to return.")


_NOTE = (
    "These are the Access Rules (the desired state the CSV export publishes), not live grants. "
    "via='parent' means the access comes from a catalog or schema row above the securable."
)


@tool(
    name="find_access",
    description=(
        "Look up the Access Rules ledger: who holds which privileges on a securable (including rows on "
        "its parent catalog or schema), or everything a group or service principal holds. Use it before "
        "preview_access_change to tell the user when a group already has what they're asking for. "
        "Results are the ledger's desired state, not live Databricks grants."
    ),
    args_schema=FindAccessInput,
    feature_flag="rbac",
    friendly_label="Looking up access rules",
    friendly_completion_label="Looked up access rules",
)
def find_access(
    securable_type: Optional[str] = None,
    catalog: Optional[str] = None,
    schema_name: Optional[str] = None,
    object_name: Optional[str] = None,
    workspace_name: Optional[str] = None,
    principal_name: Optional[str] = None,
    environment: Optional[str] = None,
    limit: int = 25,
    **kwargs,
) -> Dict[str, Any]:
    from app.db.session import get_lakebase_session
    from app.services.rbac import ledger

    db = get_lakebase_session()
    try:
        result = ledger.find_access(
            db,
            securable_type=securable_type,
            catalog=catalog,
            schema_name=schema_name,
            object_name=object_name,
            workspace_name=workspace_name,
            principal_name=principal_name,
            environment=environment,
            limit=limit,
        )
    except ledger.LedgerError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()
    return {**result.model_dump(mode="json"), "count": len(result.matches), "note": _NOTE}
