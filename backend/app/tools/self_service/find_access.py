"""
Tool to look up who holds what in the Access Rules ledger (ADR 0005 D9).
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool


_SPLIT = " Split a dotted name like 'catalog.schema.table' across catalog, schema_name and object_name."


class FindAccessInput(BaseModel):
    securable_type: Optional[str] = Field(
        None, description="Workbook spelling, e.g. 'catalog', 'schema', 'table', 'workspace', 'folder'.")
    catalog: Optional[str] = Field(None, description="Catalog name." + _SPLIT)
    schema_name: Optional[str] = Field(None, description="Schema name (inside the catalog).")
    object_name: Optional[str] = Field(None, description="Table, view, volume, folder or other object name.")
    workspace_name: Optional[str] = Field(None, description="Workspace name, for workspace securables.")
    principal_name: Optional[str] = Field(
        None, description="A group or service principal name (partial match) to list what it holds.")
    environment: Optional[str] = Field(None, description="Environment key or alias, e.g. 'dev', 'prd'.")
    limit: int = Field(default=25, ge=1, le=100, description="Max matches to return.")


_NOTE = (
    "via='parent' means the access comes from a catalog or schema rule above the securable. "
    "Approved changes reach Databricks at the next scheduled update, so mention timing only if the user asks "
    "whether access is live right now. Call these 'the access rules'; don't say 'desired state' or 'ledger'."
)

_TRY_SUGGESTIONS = (
    "Nothing matched as asked, but `suggestions` lists securables with that name at another level or in "
    "another environment (a name given as a catalog may be a schema or table). If one clearly fits, look it "
    "up now without asking and tell the user which one you used; if several fit, ask which they meant. "
    "A suggestion that is the securable asked about has rules, just none for the group or environment given."
)

_NOTHING_FOUND = (
    "No access rule mentions this name as a catalog, schema, table or workspace in any environment, so no group "
    "gets access to it through the access rules. Say so plainly and ask for the full name (catalog.schema.table) "
    "or another spelling."
)


@tool(
    name="find_access",
    description=(
        "Look up the access rules: who holds which privileges on a securable (including rules on its parent "
        "catalog or schema), or everything a group or service principal holds. Use it before "
        "preview_access_change to tell the user when a group already has what they're asking for. If you're "
        "unsure whether a name is a catalog, schema or table, pass your best guess: when nothing matches, the "
        "result suggests securables with that name at any level."
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
    out: Dict[str, Any] = {**result.model_dump(mode="json"), "count": len(result.matches), "note": _NOTE}
    if result.suggestions:
        out["next_step"] = _TRY_SUGGESTIONS
    elif not result.matches and any((catalog, schema_name, object_name, workspace_name)):
        out["next_step"] = _NOTHING_FOUND
    return out
