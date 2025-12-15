"""
Databricks provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config


class DatabricksProvider(BaseProvider):
    """Databricks provider for workspace and catalog operations."""
    
    def __init__(self, host: str, token: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.host = host
        self.token = token
        self.client = WorkspaceClient(
            host=host,
            token=token
        )
    
    @retry_on_retryable(max_attempts=3)
    async def execute_sql(self, query: str, warehouse: Optional[str] = None) -> Dict[str, Any]:
        """Execute SQL query."""
        try:
            # TODO: Implement SQL execution via Databricks SQL API
            # This would use the Databricks SQL API or SQL connector
            raise NotImplementedError("SQL execution not yet implemented")
        except Exception as e:
            raise RetryableError(f"SQL execution failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def create_workspace(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create workspace (via API)."""
        try:
            # TODO: Implement workspace creation via Databricks API
            raise NotImplementedError("Workspace creation not yet implemented")
        except Exception as e:
            raise RetryableError(f"Workspace creation failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def create_catalog(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create Unity Catalog catalog."""
        try:
            # TODO: Implement catalog creation via Unity Catalog API
            raise NotImplementedError("Catalog creation not yet implemented")
        except Exception as e:
            raise RetryableError(f"Catalog creation failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def grant_access(self, principal: str, resource: str, permissions: list) -> bool:
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

