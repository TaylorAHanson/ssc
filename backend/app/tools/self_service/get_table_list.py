"""
Tool to list tables.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import fnmatch

class GetTableListInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    catalog_name: str = Field(..., description="Name of the parent catalog")
    schema_name: str = Field(..., description="Name of the schema to list tables for")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific table.")

@tool(
    name="get_table_list",
    description="Lists all tables within a specified catalog and schema in Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a table exists. NEXT STEP: If the user needs access, proceed to the 'Request Data Access' workflow.",
    args_schema=GetTableListInput
)
async def get_table_list(target_host: str, catalog_name: str, schema_name: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of tables for a schema along with their descriptions.
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
        
        tables = provider.client.tables.list(catalog_name=catalog_name, schema_name=schema_name)
        
        # Fetch tags for all tables in this schema in one query
        tags_by_table = {}
        try:
            query = f"""
            SELECT table_name, tag_name, tag_value 
            FROM system.information_schema.table_tags 
            WHERE catalog_name = '{catalog_name}' 
              AND schema_name = '{schema_name}'
            """
            tag_results = await provider.execute_sql(query)
            for row in tag_results.get("rows", []):
                if isinstance(row, dict):
                    t_name = row.get("table_name")
                    tag_name = row.get("tag_name")
                    tag_value = row.get("tag_value")
                    if t_name and tag_name and tag_value is not None:
                        if t_name not in tags_by_table:
                            tags_by_table[t_name] = {}
                        tags_by_table[t_name][tag_name] = str(tag_value)
        except Exception as tag_err:
            # Non-fatal if tags fail
            pass
        
        table_list = []
        for table in tables:
            if name_pattern and not fnmatch.fnmatch(table.name.lower(), name_pattern.lower()):
                continue
                
            table_tags = tags_by_table.get(table.name, {})
                
            table_list.append({
                "name": table.name,
                "catalog_name": table.catalog_name,
                "schema_name": table.schema_name,
                "table_type": table.table_type.value if hasattr(table.table_type, 'value') else str(table.table_type),
                "comment": table.comment or "No description provided",
                "owner": table.owner,
                "tags": table_tags,
                "properties": table.properties or {}
            })
        
        return {
            "catalog_name": catalog_name,
            "schema_name": schema_name,
            "count": len(table_list),
            "tables": table_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch table list for {catalog_name}.{schema_name}: {str(e)}")
