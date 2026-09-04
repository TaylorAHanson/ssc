"""
Tool to list tables.
"""
from typing import Dict, Any, Optional
import asyncio
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.tools.sql_safety import quote_literal
from app.core.exceptions import RetryableError
import fnmatch

class GetTableListInput(BaseModel):
    target_host: str = Field(..., description="Workspace host for context only. Unity Catalog is account-global, so tables are always read from the local workspace regardless of this value.")
    catalog_name: str = Field(..., description="Name of the parent catalog")
    schema_name: str = Field(..., description="Name of the schema to list tables for")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific table.")

@tool(
    name="get_table_list",
    description="List tables within a specified catalog and schema in Unity Catalog. Supports filtering by exact name or glob pattern (e.g. '*orders*').",
    args_schema=GetTableListInput
)
async def get_table_list(target_host: str, catalog_name: str, schema_name: str, name_pattern: Optional[str] = None, _obo_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of tables for a schema along with their descriptions.
    """
    try:
        # Run On-Behalf-Of the user so the listing reflects the USER's own Unity
        # Catalog grants (a permission error here means the USER lacks access,
        # not the app). UC is metastore-global, so we always read from the
        # LOCAL/home workspace — never the target host, which may be network-
        # unreachable / fail cert validation. target_host is accepted for
        # context but intentionally not used to pick the connection.
        from app.core.workspaces import uc_client_for
        provider, client = uc_client_for(_obo_token)
        
        # tables.list() returns a lazy pager; materialize it in a worker thread so
        # the paging API calls don't block the event loop.
        tables = await asyncio.to_thread(
            lambda: list(client.tables.list(catalog_name=catalog_name, schema_name=schema_name))
        )
        
        # Fetch tags for all tables in this schema in one query
        tags_by_table = {}
        try:
            query = f"""
            SELECT table_name, tag_name, tag_value 
            FROM system.information_schema.table_tags 
            WHERE catalog_name = {quote_literal(catalog_name)} 
              AND schema_name = {quote_literal(schema_name)}
            """
            tag_results = await provider.execute_sql(query, obo_token=_obo_token)
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
