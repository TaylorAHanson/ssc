"""
Tool to list catalogs.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.core.exceptions import RetryableError
import fnmatch

class GetCatalogListInput(BaseModel):
    target_host: str = Field(..., description="Workspace host for context only. Unity Catalog is account-global, so catalogs are always read from the local workspace regardless of this value.")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter catalogs.")

@tool(
    name="get_catalog_list",
    description="Lists all available catalogs in the Databricks Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a catalog exists. NEXT STEP: Use 'get_schema_list' to explore a specific catalog.",
    args_schema=GetCatalogListInput
)
def get_catalog_list(target_host: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of catalogs along with their descriptions.
    """
    try:
        # Unity Catalog is metastore-global (account-level), so always read it
        # from the LOCAL/home workspace — never the target host, which may be
        # network-unreachable / fail cert validation from here. target_host is
        # accepted for context but intentionally not used to pick the connection.
        from app.core.workspaces import get_uc_provider
        provider = get_uc_provider()
        
        catalogs = provider.client.catalogs.list()
        
        catalog_list = []
        for catalog in catalogs:
            if name_pattern and not fnmatch.fnmatch(catalog.name.lower(), name_pattern.lower()):
                continue
                
            catalog_list.append({
                "name": catalog.name,
                "comment": catalog.comment or "No description provided",
                "catalog_type": catalog.catalog_type.value if hasattr(catalog.catalog_type, 'value') else str(catalog.catalog_type),
                "owner": catalog.owner,
                "properties": catalog.properties or {}
            })
        
        return {
            "count": len(catalog_list),
            "catalogs": catalog_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch catalog list: {str(e)}")
