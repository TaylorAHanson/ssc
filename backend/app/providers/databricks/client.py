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
import asyncio
import time

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

    def get_workspace_client(self, token: Optional[str] = None) -> WorkspaceClient:
        """
        Get a WorkspaceClient instance.
        
        If a token is provided (e.g. OBO token), returns a new client using that token.
        Otherwise, returns the default client (Service Principal or default Token).
        
        Args:
            token: Optional OBO access token
            
        Returns:
            WorkspaceClient
        """
        if token:
            # Create a new client with the provided token
            # Note: We use auth_type="pat" as often required when passing token explicitly
            return WorkspaceClient(
                host=self.host,
                token=token,
                auth_type="pat"
            )
        return self.client

    
    @retry_on_retryable(max_attempts=3)
    async def execute_sql(self, query: str, warehouse: Optional[str] = None, timeout_seconds: int = 120) -> Dict[str, Any]:
        """
        Execute SQL query using Databricks SDK with async polling.
        
        Args:
            query: SQL query to execute
            warehouse: SQL warehouse ID (optional, falls back to default if configured)
            timeout_seconds: Max seconds to wait for completion (default 120)
            
        Returns:
            Dictionary with 'rows' and 'schema'
        """
        try:
            # use the statement execution API
            warehouse_id = warehouse or self.get_config("warehouse_id")
            
            if not warehouse_id:
                raise ValueError("warehouse_id is required for SQL execution")

            # Execute the statement in ASYNC mode (wait_timeout="0s")
            response = await asyncio.to_thread(
                self.client.statement_execution.execute_statement,
                statement=query,
                warehouse_id=warehouse_id,
                wait_timeout="0s" 
            )
            
            statement_id = response.statement_id
            
            # Polling loop
            start_time = time.time()
            final_response = None
            
            while True:
                # Check timeout
                if (time.time() - start_time) > timeout_seconds:
                    # Cancel query if timed out
                    try:
                        await asyncio.to_thread(self.client.statement_execution.cancel_execution, statement_id)
                    except:
                        pass
                    raise RetryableError(f"SQL execution timed out after {timeout_seconds}s")
                
                # Get status
                status_resp = await asyncio.to_thread(self.client.statement_execution.get_statement, statement_id)
                state = status_resp.status.state.value # Enum to string
                
                if state == "SUCCEEDED":
                    final_response = status_resp
                    break
                elif state in ["FAILED", "CANCELED", "CLOSED"]:
                    error_msg = f"SQL execution failed with state {state}"
                    if status_resp.status.error:
                        error_msg += f": {status_resp.status.error.message}"
                    raise RetryableError(error_msg)
                else:
                    # RUNNING, PENDING
                    await asyncio.sleep(1)
            
            # Simplified result parsing
            # Extract columns first
            columns = []
            if final_response.manifest and final_response.manifest.schema and final_response.manifest.schema.columns:
                columns = [col.name for col in final_response.manifest.schema.columns]
            elif final_response.result and hasattr(final_response.result, 'schema') and final_response.result.schema:
                columns = [col.name for col in final_response.result.schema.columns]

            # Map rows to columns
            rows = []
            if final_response.result and final_response.result.data_array:
                for row in final_response.result.data_array:
                    # Case 1: row is already a dict (unlikely based on error)
                    if isinstance(row, dict):
                        rows.append(row)
                    # Case 2: row is an object with as_dict (old SDK?)
                    elif hasattr(row, 'as_dict'):
                        rows.append(row.as_dict())
                    # Case 3: row is a list of values (common SQL API)
                    elif isinstance(row, list) and columns:
                        rows.append(dict(zip(columns, row)))
                    # Case 4: row is a list but no columns known
                    elif isinstance(row, list):
                        # Fallback to index keys if no schema
                        rows.append({f"col_{i}": v for i, v in enumerate(row)})
                    else:
                        # Fallback for unknown type
                        rows.append(row)
            
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

