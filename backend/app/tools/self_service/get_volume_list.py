"""
Tool to list volumes.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.core.exceptions import RetryableError
import fnmatch

class GetVolumeListInput(BaseModel):
    target_host: str = Field(..., description="Workspace host for context only. Unity Catalog is account-global, so volumes are always read from the local workspace regardless of this value.")
    catalog_name: str = Field(..., description="Name of the catalog")
    schema_name: str = Field(..., description="Name of the schema")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific volume.")

@tool(
    name="get_volume_list",
    description="List volumes within a specified Unity Catalog schema, with optional name pattern filter.",
    args_schema=GetVolumeListInput
)
def get_volume_list(target_host: str, catalog_name: str, schema_name: str, name_pattern: Optional[str] = None, _obo_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of volumes for a schema along with their descriptions.
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
        
        volumes = client.volumes.list(catalog_name=catalog_name, schema_name=schema_name)
        
        volume_list = []
        for volume in volumes:
            if name_pattern and not fnmatch.fnmatch(volume.name.lower(), name_pattern.lower()):
                continue
                
            volume_list.append({
                "name": volume.name,
                "catalog_name": volume.catalog_name,
                "schema_name": volume.schema_name,
                "volume_type": volume.volume_type.value if hasattr(volume.volume_type, 'value') else str(volume.volume_type),
                "storage_location": volume.storage_location,
                "comment": volume.comment or "No description provided",
                "owner": volume.owner
            })
        
        return {
            "catalog_name": catalog_name,
            "schema_name": schema_name,
            "count": len(volume_list),
            "volumes": volume_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch volume list for {catalog_name}.{schema_name}: {str(e)}")
