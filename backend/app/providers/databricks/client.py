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


class DatabricksProvider(BaseProvider):
    """Databricks provider for workspace and catalog operations."""
    
    def __init__(self, host: str, token: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.host = host
        self.token = token
        
        # Validate that we have required credentials
        if not host:
            raise ValueError("DATABRICKS_HOST is required for DatabricksProvider")
        if not token:
            raise ValueError("DATABRICKS_TOKEN is required for DatabricksProvider")
        
        # Temporarily unset OAuth env vars to prevent SDK from auto-detecting them
        # We only want to use PAT (token) for workspace operations
        # OAuth credentials are used separately for Terraform MWS provisioning
        oauth_vars = {}
        for var in ['DATABRICKS_ACCOUNT_ID', 'DATABRICKS_CLIENT_ID', 'DATABRICKS_CLIENT_SECRET']:
            if var in os.environ:
                oauth_vars[var] = os.environ.pop(var)
        
        try:
            # Create WorkspaceClient with explicit Config that only uses PAT
            # Pass host and token directly to WorkspaceClient (not Config)
            # This prevents the SDK from auto-detecting OAuth from env vars
            self.client = WorkspaceClient(
                host=host,
                token=token
            )
        except Exception as e:
            # Restore OAuth env vars before re-raising
            for var, value in oauth_vars.items():
                os.environ[var] = value
            # Provide clearer error message
            if "cannot configure default credentials" in str(e):
                raise ValueError(
                    f"Databricks authentication failed. "
                    f"Please ensure DATABRICKS_HOST and DATABRICKS_TOKEN are set correctly. "
                    f"Host: {'set' if host else 'NOT SET'}, Token: {'set' if token else 'NOT SET'}"
                ) from e
            raise
        finally:
            # Restore OAuth env vars after creating the client (if not already restored)
            for var, value in oauth_vars.items():
                if var not in os.environ:
                    os.environ[var] = value
    
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

