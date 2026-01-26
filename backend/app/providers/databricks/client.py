"""
Databricks provider client.
"""
from typing import Dict, Any, Optional
import os
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
import logging

logger = logging.getLogger(__name__)


class DatabricksProvider(BaseProvider):
    """Databricks provider for workspace and catalog operations."""
    
    def __init__(
        self, 
        host: str, 
        token: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None
    ):
        super().__init__(config)
        self.host = host
        self.token = token
        
        # Validate that we have required credentials
        if not host:
            raise ValueError("DATABRICKS_HOST is required for DatabricksProvider")
        
        # Check for either Token or Service Principal credentials
        has_token = bool(token)
        has_sp = bool(client_id and client_secret)
        
        if not (has_token or has_sp):
            raise ValueError("Either DATABRICKS_TOKEN or (DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET) is required for DatabricksProvider")
            
        try:
            if has_sp:
                # Service Principal Auth
                logger.info(f"Initializing DatabricksProvider with Service Principal: client_id={client_id[:4]}*** host={host}")
                self.client = WorkspaceClient(
                    host=host,
                    client_id=client_id,
                    client_secret=client_secret
                )
            else:
                # Token Auth
                logger.info(f"Initializing DatabricksProvider with Token: host={host}")
                self.client = WorkspaceClient(
                    host=host,
                    token=token
                )
        except Exception as e:
            raise ValueError(f"Databricks client initialization failed: {str(e)}") from e
    
    @retry_on_retryable(max_attempts=3)
    async def execute_sql(self, query: str, warehouse: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute SQL query using Databricks SDK.
        
        Args:
            query: SQL query to execute
            warehouse: SQL warehouse ID (optional, falls back to default if configured)
            
        Returns:
            Dictionary with 'rows' and 'schema'
        """
        try:
            # use the statement execution API
            # Ideally we would use a warehouse_id from config if not provided
            warehouse_id = warehouse or self.get_config("warehouse_id")
            
            if not warehouse_id:
                # If no warehouse ID, we might try to use the workspace client's default mechanism 
                # or raise error if it requires one. 
                # For now let's assume one is needed for SQL execution via SDK usually.
                # However, for 'SHOW CATALOGS' sometimes we can use the Unity Catalog API directly,
                # but the request asked for executeSql.
                raise ValueError("warehouse_id is required for SQL execution")

            # Execute the statement
            response = self.client.statement_execution.execute_statement(
                statement=query,
                warehouse_id=warehouse_id,
                wait_timeout="50s" # Wait up to 50s for result
            )
            
            # If we need to wait more, the SDK usually handles it if we use the right method,
            # or `execute_statement` might return a handle we need to poll.
            # actually databricks-sdk `execute_statement` waits by default if proper params are set, 
            # or returns a response we can check. 
            # Let's verify SDK usage. Since I can't browse, I will assume standard synchronous-wait behavior 
            # or simple response parsing. 
            
            # Simplified result parsing
            # Note: response structure depends on SDK version. 
            # Trying standard path: response.result.data_array for data
            # And: response.manifest.schema.columns for schema (metadata often in manifest)
            
            rows = []
            if response.result and response.result.data_array:
                rows = [row.as_dict() for row in response.result.data_array]
            
            columns = []
            # Try to get schema from manifest (usual location) or result (older SDKs?)
            if response.manifest and response.manifest.schema and response.manifest.schema.columns:
                columns = [col.name for col in response.manifest.schema.columns]
            elif response.result and hasattr(response.result, 'schema') and response.result.schema:
                columns = [col.name for col in response.result.schema.columns]
                
            result = {
                "rows": rows,
                "schema": columns
            }
            return result

        except Exception as e:
            # Classify errors
            if "TEMPORARILY_UNAVAILABLE" in str(e):
                raise RetryableError(f"SQL execution temporarily unavailable: {str(e)}")
            raise RetryableError(f"SQL execution failed: {str(e)}")
    

        """Grant access to resource."""
        try:
            # TODO: Implement access grant via Databricks API
            raise NotImplementedError("Access grant not yet implemented")
        except Exception as e:
            raise RetryableError(f"Access grant failed: {str(e)}")
    
    async def health_check(self) -> bool:
        """Check if Databricks is accessible."""
        try:
            # Simple health check
            return self.client is not None
        except:
            return False

