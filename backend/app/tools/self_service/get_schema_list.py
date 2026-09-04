"""
Tool to list schemas.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.core.exceptions import RetryableError
import fnmatch

class GetSchemaListInput(BaseModel):
    target_host: str = Field(..., description="Workspace host for context only. Unity Catalog is account-global, so schemas are always read from the local workspace regardless of this value.")
    catalog_name: str = Field(..., description="Name of the catalog to list schemas for")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific schema.")

@tool(
    name="get_schema_list",
    description="List schemas within a specified Unity Catalog catalog. Supports filtering by exact name or glob pattern (e.g. '*sales*').",
    args_schema=GetSchemaListInput
)
def get_schema_list(target_host: str, catalog_name: str, name_pattern: Optional[str] = None, _obo_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of schemas for a catalog along with their descriptions.
    """
    try:
        # Run On-Behalf-Of the user so the listing reflects the USER's own Unity
        # Catalog grants (a permission error here means the USER lacks access,
        # not the app). UC is metastore-global, so we always read from the
        # LOCAL/home workspace — never the target host, which may be network-
        # unreachable / fail cert validation. target_host is accepted for
        # context but intentionally not used to pick the connection.
        from app.core.workspaces import uc_client_for
        _provider, client = uc_client_for(_obo_token)
        
        schemas = client.schemas.list(catalog_name=catalog_name)
        
        schema_list = []
        for schema in schemas:
            if name_pattern and not fnmatch.fnmatch(schema.name.lower(), name_pattern.lower()):
                continue
                
            schema_list.append({
                "name": schema.name,
                "catalog_name": schema.catalog_name,
                "comment": schema.comment or "No description provided",
                "owner": schema.owner,
                "properties": schema.properties or {}
            })
        
        return {
            "catalog_name": catalog_name,
            "count": len(schema_list),
            "schemas": schema_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch schema list for catalog '{catalog_name}': {str(e)}")
