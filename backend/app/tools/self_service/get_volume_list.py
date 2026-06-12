"""
Tool to list volumes.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import fnmatch

class GetVolumeListInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    catalog_name: str = Field(..., description="Name of the catalog")
    schema_name: str = Field(..., description="Name of the schema")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific volume.")

@tool(
    name="get_volume_list",
    description="Lists all volumes within a specified catalog and schema in Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a volume exists.",
    args_schema=GetVolumeListInput
)
def get_volume_list(target_host: str, catalog_name: str, schema_name: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of volumes for a schema along with their descriptions.
    """
    try:
        from app.core.workspaces import get_workspace_config
        ws_config = get_workspace_config(target_host)
        if not ws_config:
            raise ValueError(f"Target host {target_host} not found in configuration.")
            
        provider = DatabricksProvider(
            host=ws_config.host,
            token=ws_config.token,
            client_id=ws_config.client_id,
            client_secret=ws_config.client_secret,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        volumes = provider.client.volumes.list(catalog_name=catalog_name, schema_name=schema_name)
        
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
