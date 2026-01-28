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

    @retry_on_retryable(max_attempts=3)
    async def grant_access(
        self,
        asset_type: str,
        asset_name: str,
        principal: str,
        access_level: str
    ) -> Dict[str, Any]:
        """
        Grant access to a Unity Catalog asset using the Grants API.

        Args:
            asset_type: Type of asset (catalog, schema, table, volume)
            asset_name: Full name of the asset (e.g., "my_catalog.my_schema.my_table")
            principal: User or group to grant access to (email or group name)
            access_level: Level of access (read, write, manage)

        Returns:
            Dictionary with grant result details
        """
        from databricks.sdk.service.catalog import SecurableType, PermissionsChange, Privilege

        try:
            # Map asset_type to SecurableType
            securable_type_map = {
                "catalog": SecurableType.CATALOG,
                "schema": SecurableType.SCHEMA,
                "table": SecurableType.TABLE,
                "volume": SecurableType.VOLUME,
            }

            securable_type = securable_type_map.get(asset_type.lower())
            if not securable_type:
                raise PermanentError(f"Invalid asset_type: {asset_type}. Must be one of: catalog, schema, table, volume")

            # Map access_level to privileges
            # Unity Catalog privilege hierarchy:
            # - read: SELECT (for tables), USE_CATALOG/USE_SCHEMA (for catalogs/schemas)
            # - write: SELECT + MODIFY
            # - manage: ALL_PRIVILEGES
            privilege_map = {
                "read": self._get_read_privileges(asset_type),
                "write": self._get_write_privileges(asset_type),
                "manage": [Privilege.ALL_PRIVILEGES],
            }

            privileges = privilege_map.get(access_level.lower())
            if not privileges:
                raise PermanentError(f"Invalid access_level: {access_level}. Must be one of: read, write, manage")

            logger.info(f"Granting {access_level} access on {asset_type} '{asset_name}' to {principal}")

            # Build the permission change
            changes = [
                PermissionsChange(
                    add=privileges,
                    principal=principal
                )
            ]

            # Update permissions using the grants API
            self.client.grants.update(
                securable_type=securable_type,
                full_name=asset_name,
                changes=changes
            )

            logger.info(f"Successfully granted {access_level} access on {asset_name} to {principal}")

            return {
                "success": True,
                "asset_type": asset_type,
                "asset_name": asset_name,
                "principal": principal,
                "access_level": access_level,
                "privileges_granted": [str(p) for p in privileges]
            }

        except PermanentError:
            raise
        except Exception as e:
            error_str = str(e)
            # Classify errors
            if "NOT_FOUND" in error_str:
                raise PermanentError(f"Asset not found: {asset_name}")
            if "PERMISSION_DENIED" in error_str:
                raise PermanentError(f"Permission denied to grant access on {asset_name}: {error_str}")
            if "INVALID_PARAMETER" in error_str or "INVALID_STATE" in error_str:
                raise PermanentError(f"Invalid grant request: {error_str}")
            if "TEMPORARILY_UNAVAILABLE" in error_str or "RATE_LIMIT" in error_str:
                raise RetryableError(f"Temporarily unavailable: {error_str}")
            raise RetryableError(f"Failed to grant access: {error_str}")

    def _get_read_privileges(self, asset_type: str) -> list:
        """Get the privileges needed for read access based on asset type."""
        from databricks.sdk.service.catalog import Privilege

        if asset_type.lower() == "catalog":
            return [Privilege.USE_CATALOG]
        elif asset_type.lower() == "schema":
            return [Privilege.USE_SCHEMA]
        elif asset_type.lower() in ["table", "volume"]:
            return [Privilege.SELECT]
        return [Privilege.SELECT]

    def _get_write_privileges(self, asset_type: str) -> list:
        """Get the privileges needed for write access based on asset type."""
        from databricks.sdk.service.catalog import Privilege

        if asset_type.lower() == "catalog":
            return [Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA]
        elif asset_type.lower() == "schema":
            return [Privilege.USE_SCHEMA, Privilege.CREATE_TABLE, Privilege.CREATE_VOLUME]
        elif asset_type.lower() == "table":
            return [Privilege.SELECT, Privilege.MODIFY]
        elif asset_type.lower() == "volume":
            return [Privilege.READ_VOLUME, Privilege.WRITE_VOLUME]
        return [Privilege.SELECT, Privilege.MODIFY]

    async def health_check(self) -> bool:
        """Check if Databricks is accessible."""
        try:
            # Simple health check
            return self.client is not None
        except:
            return False

