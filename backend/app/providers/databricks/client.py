"""
Databricks provider client.
"""
from typing import Dict, Any, List, Optional
import os
import uuid
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError, AuthenticationError
from app.core.retry import retry_on_retryable
from app.providers.databricks.compute import ComputeSpec
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
import logging
import asyncio
import time
import base64
from databricks.sdk.service import jobs, workspace, compute as compute_svc

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
    async def execute_sql(self, query: str, warehouse: Optional[str] = None, timeout_seconds: int = 120, obo_token: Optional[str] = None, require_obo: bool = False) -> Dict[str, Any]:
        """
        Execute SQL query using Databricks SDK with async polling.
        
        Args:
            query: SQL query to execute
            warehouse: SQL warehouse ID (optional, falls back to default if configured)
            timeout_seconds: Max seconds to wait for completion (default 120)
            obo_token: Optional user On-Behalf-Of access token. When provided, the
                statement runs as the USER (their Unity Catalog / system-table
                permissions apply) instead of the app's service principal. Use
                this for read tools that should reflect the caller's own access
                (e.g. querying system.* tables the SP can't read).
            
        Returns:
            Dictionary with 'rows' and 'schema'
        """
        try:
            # Guard against a *silent* OBO -> SP downgrade. When a caller declares
            # the statement must run as the user (require_obo) but no token was
            # forwarded, refuse on any deployed target rather than quietly running
            # as the app service principal (which would return rows under the wrong
            # identity / the SP's grants). Only true local dev may fall back.
            if require_obo and not obo_token:
                from app.providers.databricks_mcp import sp_fallback_allowed

                if not sp_fallback_allowed():
                    raise PermissionError(
                        "This query must run On-Behalf-Of the user, but no OBO token "
                        "was forwarded. Refusing to fall back to the app service "
                        "principal (it would expose data under the wrong identity). "
                        "Ensure the request forwards X-Forwarded-Access-Token and the "
                        "bundle's user_api_scopes cover the needed SQL/warehouse access."
                    )
                logger.warning(
                    "execute_sql: require_obo set but no OBO token; using SP fallback "
                    "(allowed only in local dev)."
                )

            # Run as the user (OBO) when a token is supplied, else the default
            # service-principal client.
            client = self.get_workspace_client(token=obo_token) if obo_token else self.client

            # use the statement execution API
            warehouse_id = warehouse or self.get_config("warehouse_id")
            
            if not warehouse_id:
                raise ValueError("warehouse_id is required for SQL execution")

            # Execute the statement in ASYNC mode (wait_timeout="0s")
            response = await asyncio.to_thread(
                client.statement_execution.execute_statement,
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
                        await asyncio.to_thread(client.statement_execution.cancel_execution, statement_id=statement_id)
                    except:
                        pass
                    raise RetryableError(f"SQL execution timed out after {timeout_seconds}s")
                
                # Get status
                status_resp = await asyncio.to_thread(client.statement_execution.get_statement, statement_id=statement_id)
                state = status_resp.status.state.value # Enum to string
                
                if state == "SUCCEEDED":
                    final_response = status_resp
                    break
                elif state in ("FAILED", "CANCELED", "CLOSED"):
                    error_msg = f"SQL execution failed with state {state}"
                    if status_resp.status.error:
                        error_msg += f": {status_resp.status.error.message}"
                    raise RetryableError(error_msg)
                else:
                    # Wait before polling again
                    await asyncio.sleep(2)
            
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

        except PermanentError:
            # Already classified non-retryable upstream (e.g. the OBO guard). Don't
            # let the catch-all below re-wrap it as a RetryableError.
            raise
        except Exception as e:
            msg = str(e)
            # Non-retryable: workspace IP access-list blocks, auth/permission
            # denials won't succeed on retry. Fail fast so we don't burn 3 backoff
            # attempts (and emit a stack trace) on every scheduled run.
            if (
                "is blocked by Databricks IP" in msg
                or "IP ACL" in msg
                or type(e).__name__ in ("PermissionDenied", "Unauthenticated")
            ):
                raise AuthenticationError(f"SQL execution denied (not retryable): {msg}")
            # Classify errors
            if "TEMPORARILY_UNAVAILABLE" in msg:
                raise RetryableError(f"SQL execution temporarily unavailable: {msg}")
            raise RetryableError(f"SQL execution failed: {msg}")

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
            # Map asset_type to SecurableType. Views are exposed via the TABLE
            # securable type in Unity Catalog (no separate SecurableType.VIEW).
            securable_type_map = {
                "catalog": SecurableType.CATALOG,
                "schema": SecurableType.SCHEMA,
                "table": SecurableType.TABLE,
                "view": SecurableType.TABLE,
                "volume": SecurableType.VOLUME,
            }

            securable_type = securable_type_map.get(asset_type.lower())
            if not securable_type:
                raise PermanentError(f"Invalid asset_type: {asset_type}. Must be one of: catalog, schema, table, view, volume")

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

            # Use SQL GRANT statements for reliable cross-type support
            grant_statements = self._build_grant_sql(asset_type.lower(), asset_name, principal, access_level.lower())

            for sql in grant_statements:
                logger.info(f"Executing: {sql}")
                await self.execute_sql(sql)

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
        elif asset_type.lower() in ["table", "view", "volume"]:
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
        elif asset_type.lower() == "view":
            # Views are not directly writable in Unity Catalog; downgrade to SELECT.
            return [Privilege.SELECT]
        elif asset_type.lower() == "volume":
            return [Privilege.READ_VOLUME, Privilege.WRITE_VOLUME]
        return [Privilege.SELECT, Privilege.MODIFY]

    def _build_grant_sql(self, asset_type: str, asset_name: str, principal: str, access_level: str) -> list:
        """
        Build SQL GRANT statements for Unity Catalog access.

        For schema access, this grants USE CATALOG on parent catalog first,
        then grants schema-level permissions.

        Args:
            asset_type: catalog, schema, table, or volume
            asset_name: Full name (e.g., "catalog.schema" or "catalog.schema.table")
            principal: User email or group name
            access_level: read, write, or manage

        Returns:
            List of SQL GRANT statements to execute in order
        """
        statements = []
        parts = asset_name.split(".")

        # Privilege mapping
        privilege_map = {
            "catalog": {
                "read": "USE CATALOG",
                "write": "USE CATALOG, CREATE SCHEMA",
                "manage": "ALL PRIVILEGES",
            },
            "schema": {
                # Aligns with the UC "Read group" (SELECT, EXECUTE, READ VOLUME)
                # plus the USE SCHEMA prerequisite. Granted at schema level,
                # SELECT/MODIFY/READ VOLUME/etc. cascade to all child objects.
                "read": "USE SCHEMA, SELECT, EXECUTE, READ VOLUME",
                # Read group + UC "Edit group" (MODIFY, REFRESH, WRITE VOLUME).
                # NOTE: previously included CREATE VIEW which is not a valid
                # schema-level privilege (CREATE MATERIALIZED VIEW is, but is
                # only supported on newer metastores). If you need create
                # rights on a schema, request "manage".
                "write": "USE SCHEMA, SELECT, EXECUTE, READ VOLUME, MODIFY, REFRESH, WRITE VOLUME",
                "manage": "ALL PRIVILEGES",
            },
            "table": {
                "read": "SELECT",
                "write": "SELECT, MODIFY",
                "manage": "ALL PRIVILEGES",
            },
            "view": {
                "read": "SELECT",
                # Views are read-only; "write" maps to SELECT.
                "write": "SELECT",
                "manage": "ALL PRIVILEGES",
            },
            "volume": {
                "read": "READ VOLUME",
                "write": "READ VOLUME, WRITE VOLUME",
                "manage": "ALL PRIVILEGES",
            },
        }

        if asset_type == "catalog":
            # Just grant on catalog
            privileges = privilege_map["catalog"].get(access_level, "USE CATALOG")
            statements.append(f"GRANT {privileges} ON CATALOG `{asset_name}` TO `{principal}`")

        elif asset_type == "schema":
            # First grant USE CATALOG on parent catalog, then schema permissions
            if len(parts) >= 2:
                catalog_name = parts[0]
                schema_name = parts[1]  # Just the schema name without catalog prefix
                statements.append(f"GRANT USE CATALOG ON CATALOG `{catalog_name}` TO `{principal}`")
                privileges = privilege_map["schema"].get(access_level, "USE SCHEMA")
                # Use catalog.schema format for schema grants
                statements.append(f"GRANT {privileges} ON SCHEMA `{catalog_name}`.`{schema_name}` TO `{principal}`")
            else:
                # Fallback if parts parsing fails
                privileges = privilege_map["schema"].get(access_level, "USE SCHEMA")
                statements.append(f"GRANT {privileges} ON SCHEMA `{asset_name}` TO `{principal}`")

        elif asset_type == "table":
            # Grant USE CATALOG, USE SCHEMA, then table permissions
            if len(parts) >= 3:
                catalog_name = parts[0]
                schema_name = parts[1]
                table_name = parts[2]
                statements.append(f"GRANT USE CATALOG ON CATALOG `{catalog_name}` TO `{principal}`")
                statements.append(f"GRANT USE SCHEMA ON SCHEMA `{catalog_name}`.`{schema_name}` TO `{principal}`")
                privileges = privilege_map["table"].get(access_level, "SELECT")
                # Use catalog.schema.table format for table grants
                statements.append(f"GRANT {privileges} ON TABLE `{catalog_name}`.`{schema_name}`.`{table_name}` TO `{principal}`")
            else:
                # Fallback if parts parsing fails
                privileges = privilege_map["table"].get(access_level, "SELECT")
                statements.append(f"GRANT {privileges} ON TABLE `{asset_name}` TO `{principal}`")

        elif asset_type == "volume":
            # Grant USE CATALOG, USE SCHEMA, then volume permissions
            if len(parts) >= 3:
                catalog_name = parts[0]
                schema_name = parts[1]
                volume_name = parts[2]
                statements.append(f"GRANT USE CATALOG ON CATALOG `{catalog_name}` TO `{principal}`")
                statements.append(f"GRANT USE SCHEMA ON SCHEMA `{catalog_name}`.`{schema_name}` TO `{principal}`")
                privileges = privilege_map["volume"].get(access_level, "READ VOLUME")
                # Use catalog.schema.volume format for volume grants
                statements.append(f"GRANT {privileges} ON VOLUME `{catalog_name}`.`{schema_name}`.`{volume_name}` TO `{principal}`")
            else:
                # Fallback if parts parsing fails
                privileges = privilege_map["volume"].get(access_level, "READ VOLUME")
                statements.append(f"GRANT {privileges} ON VOLUME `{asset_name}` TO `{principal}`")

        elif asset_type == "view":
            # Grant USE CATALOG, USE SCHEMA, then view permissions
            if len(parts) >= 3:
                catalog_name = parts[0]
                schema_name = parts[1]
                view_name = parts[2]
                statements.append(f"GRANT USE CATALOG ON CATALOG `{catalog_name}` TO `{principal}`")
                statements.append(f"GRANT USE SCHEMA ON SCHEMA `{catalog_name}`.`{schema_name}` TO `{principal}`")
                privileges = privilege_map["view"].get(access_level, "SELECT")
                statements.append(f"GRANT {privileges} ON VIEW `{catalog_name}`.`{schema_name}`.`{view_name}` TO `{principal}`")
            else:
                privileges = privilege_map["view"].get(access_level, "SELECT")
                statements.append(f"GRANT {privileges} ON VIEW `{asset_name}` TO `{principal}`")

        return statements

    async def get_user_email_from_id(self, user_id: str) -> Optional[str]:
        """
        Get user email from Databricks user ID.

        Args:
            user_id: Databricks user ID (UUID)

        Returns:
            User email or None if not found
        """
        try:
            # Use the SDK to get user details
            user = await asyncio.to_thread(self.client.users.get, user_id)
            if user and hasattr(user, 'emails') and user.emails:
                # emails is a list of EmailObject, get the primary one
                for email in user.emails:
                    if hasattr(email, 'primary') and email.primary:
                        return email.value
                # If no primary, return the first email
                return user.emails[0].value if user.emails else None
            elif user and hasattr(user, 'user_name'):
                # Fallback to username if emails not available
                return user.user_name
            return None
        except Exception as e:
            logger.warning(f"Failed to get user email for ID {user_id}: {str(e)}")
            return None

    async def get_asset_tags(self, asset_type: str, asset_name: str, tag_names: List[str]) -> Dict[str, str]:
        """
        Get specific tags for a Unity Catalog asset.

        Args:
            asset_type: Type of asset (catalog, schema, table, volume)
            asset_name: Full name of the asset (e.g., "catalog.schema.table")
            tag_names: List of tag names to fetch

        Returns:
            Dictionary mapping tag names to their values. Missing tags are omitted.
        """
        try:
            asset_type_lower = asset_type.lower()
            parts = asset_name.split(".")
            
            if asset_type_lower not in ("table", "view"):
                logger.warning(f"Fetching tags is currently only supported for tables and views. Got: {asset_type_lower}")
                return {}
                
            if len(parts) < 3:
                logger.warning(f"Invalid {asset_type_lower} name format: {asset_name}. Expected catalog.schema.{asset_type_lower}")
                return {}
                
            catalog, schema, table = parts[0], parts[1], parts[2]
            
            # Format tag names for SQL IN clause
            tags_list_str = ", ".join([f"'{t}'" for t in tag_names])
            
            query = f"""
            SELECT tag_name, tag_value 
            FROM system.information_schema.table_tags 
            WHERE catalog_name = '{catalog}' 
              AND schema_name = '{schema}' 
              AND table_name = '{table}'
              AND tag_name IN ({tags_list_str})
            """
            
            logger.info(f"Fetching tags for {asset_name}: {tag_names}")
            result = await self.execute_sql(query)
            
            tags = {}
            for row in result.get("rows", []):
                if isinstance(row, dict):
                    tag_name = row.get("tag_name")
                    tag_value = row.get("tag_value")
                    if tag_name and tag_value is not None:
                        tags[tag_name] = str(tag_value)
                        
            return tags
            
        except Exception as e:
            logger.error(f"Failed to get tags for {asset_type} '{asset_name}': {str(e)}")
            return {}

    async def get_asset_owner(self, asset_type: str, asset_name: str) -> Optional[str]:
        """
        Get the owner of a Unity Catalog asset.

        Args:
            asset_type: Type of asset (catalog, schema, table, volume)
            asset_name: Full name of the asset (e.g., "catalog.schema" for schema, "catalog.schema.table" for table)

        Returns:
            Owner email/username or None if not found
        """
        try:
            asset_type_lower = asset_type.lower()

            # For schema/table/volume, split the name to properly qualify the DESCRIBE query
            parts = asset_name.split(".")

            if asset_type_lower == "catalog":
                # Catalog: just the catalog name
                query = f"DESCRIBE CATALOG EXTENDED `{asset_name}`"
            elif asset_type_lower == "schema":
                # Schema: should be catalog.schema format
                # DESCRIBE SCHEMA needs: DESCRIBE SCHEMA catalog.schema
                if len(parts) >= 2:
                    catalog = parts[0]
                    schema = parts[1]
                    query = f"DESCRIBE SCHEMA EXTENDED {catalog}.{schema}"
                else:
                    logger.warning(f"Invalid schema name format: {asset_name}. Expected catalog.schema")
                    return None
            elif asset_type_lower in ("table", "view"):
                # Table/view: should be catalog.schema.object format. Views are
                # exposed via DESCRIBE TABLE EXTENDED in Unity Catalog.
                if len(parts) >= 3:
                    catalog = parts[0]
                    schema = parts[1]
                    object_name = parts[2]
                    query = f"DESCRIBE TABLE EXTENDED {catalog}.{schema}.{object_name}"
                else:
                    logger.warning(f"Invalid {asset_type_lower} name format: {asset_name}. Expected catalog.schema.{asset_type_lower}")
                    return None
            elif asset_type_lower == "volume":
                # Volume: should be catalog.schema.volume format
                if len(parts) >= 3:
                    catalog = parts[0]
                    schema = parts[1]
                    volume = parts[2]
                    query = f"DESCRIBE VOLUME EXTENDED {catalog}.{schema}.{volume}"
                else:
                    logger.warning(f"Invalid volume name format: {asset_name}. Expected catalog.schema.volume")
                    return None
            else:
                logger.warning(f"Unknown asset_type for owner lookup: {asset_type}")
                return None

            logger.info(f"Fetching owner for {asset_type} '{asset_name}' using query: {query}")
            result = await self.execute_sql(query)

            # Parse the result to find Owner field
            # DESCRIBE EXTENDED returns rows in different formats depending on asset type:
            # - Tables: ["col_name", "data_type", "comment"]
            # - Schemas/Catalogs: ["database_description_item", "database_description_value"]
            # Look for a row where the first column is "Owner"
            for row in result.get("rows", []):
                if isinstance(row, dict):
                    # Try different column name patterns
                    col_name = (row.get("col_name") or
                               row.get("database_description_item") or
                               row.get("key") or
                               row.get("name"))

                    data_value = (row.get("data_type") or
                                 row.get("database_description_value") or
                                 row.get("value"))

                    if col_name and str(col_name).strip().lower() == "owner":
                        owner = str(data_value).strip()
                        logger.info(f"Found owner for {asset_name}: {owner}")
                        return owner if owner else None

            logger.warning(f"Owner not found in DESCRIBE output for {asset_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to get owner for {asset_type} '{asset_name}': {str(e)}")
            return None

    async def find_object_owner(self, object_type: str, object_name: str) -> Dict[str, Any]:
        """
        Find the owner of a Databricks object.

        Args:
            object_type: Type of object (catalog, schema, table, job, dashboard, notebook, genie_space)
            object_name: Full name or ID of the object

        Returns:
            Dictionary with owner information and status
        """
        try:
            if object_type == "catalog":
                return await self._find_catalog_owner(object_name)
            elif object_type == "schema":
                return await self._find_schema_owner(object_name)
            elif object_type == "table":
                return await self._find_table_owner(object_name)
            elif object_type == "job":
                return await self._find_job_owner(object_name)
            elif object_type == "dashboard":
                return await self._find_dashboard_owner(object_name)
            elif object_type == "notebook":
                return await self._find_notebook_owner(object_name)
            elif object_type == "genie_space":
                return await self._find_genie_space_owner(object_name)
            else:
                return {
                    "found": False,
                    "message": f"Finding owner for '{object_type}' is not yet implemented. Supported types: catalog, schema, table, job, dashboard, notebook, genie_space.",
                    "object_type": object_type,
                    "object_name": object_name
                }
        except Exception as e:
            return {
                "found": False,
                "message": f"Failed to find owner for {object_type} '{object_name}': {str(e)}",
                "object_type": object_type,
                "object_name": object_name
            }

    async def _find_catalog_owner(self, name: str) -> Dict[str, Any]:
        """Find owner of a catalog."""
        try:
            cat = await asyncio.to_thread(self.client.catalogs.get, name)
            return {
                "found": True,
                "owner": cat.owner or "Unknown",
                "object_type": "catalog",
                "object_name": name
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Catalog not found: {str(e)}",
                "object_type": "catalog",
                "object_name": name
            }

    async def _find_schema_owner(self, full_name: str) -> Dict[str, Any]:
        """Find owner of a schema."""
        try:
            schema = await asyncio.to_thread(self.client.schemas.get, full_name)
            return {
                "found": True,
                "owner": schema.owner or "Unknown",
                "object_type": "schema",
                "object_name": full_name
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Schema not found: {str(e)}",
                "object_type": "schema",
                "object_name": full_name
            }

    async def _find_table_owner(self, full_name: str) -> Dict[str, Any]:
        """Find owner and relevant tags of a table."""
        try:
            table = await asyncio.to_thread(self.client.tables.get, full_name)
            result = {
                "found": True,
                "owner": table.owner or "Unknown",
                "object_type": "table",
                "object_name": full_name
            }
            
            # Fetch tags if possible
            try:
                tags = await self.get_asset_tags("table", full_name, ["approver_group", "access_group"])
                if tags:
                    if "approver_group" in tags:
                        result["approver_group"] = tags["approver_group"]
                    if "access_group" in tags:
                        result["access_group"] = tags["access_group"]
            except Exception as tag_err:
                logger.warning(f"Could not fetch tags for table {full_name}: {tag_err}")
                
            return result
        except Exception as e:
            return {
                "found": False,
                "message": f"Table not found: {str(e)}",
                "object_type": "table",
                "object_name": full_name
            }

    async def _find_job_owner(self, job_id: str) -> Dict[str, Any]:
        """Find owner of a job."""
        try:
            job_id_int = int(job_id)
            job = await asyncio.to_thread(self.client.jobs.get, job_id_int)
            owner = job.creator_user_name or "Unknown"
            return {
                "found": True,
                "owner": owner,
                "object_type": "job",
                "object_name": job_id,
                "details": {"name": job.settings.name if job.settings else "Unknown"}
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Job not found or error: {str(e)}",
                "object_type": "job",
                "object_name": job_id
            }

    async def _find_dashboard_owner(self, dashboard_id: str) -> Dict[str, Any]:
        """Find owner of a dashboard."""
        try:
            dash = await asyncio.to_thread(self.client.lakeview.get, dashboard_id)
            return {
                "found": True,
                "owner": "Unknown (Dashboard found)",
                "object_type": "dashboard",
                "object_name": dashboard_id,
                "details": {"display_name": dash.display_name}
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Dashboard not found: {str(e)}",
                "object_type": "dashboard",
                "object_name": dashboard_id
            }

    async def _find_notebook_owner(self, path: str) -> Dict[str, Any]:
        """Find owner of a notebook."""
        try:
            info = await asyncio.to_thread(self.client.workspace.get_status, path)

            # Heuristic: /Users/<email>/...
            if path.startswith("/Users/"):
                parts = path.split('/')
                if len(parts) > 2:
                    return {
                        "found": True,
                        "owner": parts[2],
                        "object_type": "notebook",
                        "object_name": path,
                        "details": {"heuristic": "Path-based"}
                    }

            return {
                "found": True,
                "owner": "Unknown (Shared Path)",
                "object_type": "notebook",
                "object_name": path,
                "message": "Located in shared path, owner cannot be determined by path alone."
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Notebook not found: {str(e)}",
                "object_type": "notebook",
                "object_name": path
            }

    async def _find_genie_space_owner(self, space_id: str) -> Dict[str, Any]:
        """Find owner of a Genie space."""
        try:
            space = await asyncio.to_thread(self.client.genie.spaces.get, space_id)
            return {
                "found": True,
                "owner": "Unknown (Genie Space found)",
                "object_type": "genie_space",
                "object_name": space_id,
                "details": {"name": getattr(space, "name", "Unknown")}
            }
        except Exception as e:
            return {
                "found": False,
                "message": f"Genie Space not found: {str(e)}",
                "object_type": "genie_space",
                "object_name": space_id
            }

    async def health_check(self) -> bool:
        """Check if Databricks is accessible."""
        try:
            # Simple health check
            return self.client is not None
        except:
            return False

    async def import_notebook(self, local_path: str, remote_path: str) -> bool:
        """
        Import a local Python file as a Databricks Notebook.
        
        Args:
            local_path: Path to the local .py file
            remote_path: Target path in Databricks workspace (e.g. /Shared/jobs/my_notebook)
            
        Returns:
            True if successful
        """
        try:
            with open(local_path, "r") as f:
                content = f.read()
            
            # Encode content as base64 for the SDK
            content_base64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            
            logger.info(f"Importing notebook: {local_path} -> {remote_path}")
            
            # Ensure parent directory exists
            remote_dir = os.path.dirname(remote_path)
            if remote_dir and remote_dir != "/":
                await asyncio.to_thread(self.client.workspace.mkdirs, remote_dir)
                
            await asyncio.to_thread(
                self.client.workspace.import_,
                path=remote_path,
                format=workspace.ImportFormat.SOURCE,
                language=workspace.Language.PYTHON,
                content=content_base64,
                overwrite=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to import notebook: {str(e)}")
            raise RetryableError(f"Failed to import notebook: {str(e)}")

    # ------------------------------------------------------------------
    # Unified Job Submission
    # ------------------------------------------------------------------
    #
    # `submit_job` is the single primitive every workflow should use to run
    # arbitrary code on a Databricks cluster. It accepts either an inline
    # `jobs.SubmitTask` (advanced) or a pair of high-level builders
    # (`notebook_task=` / `spark_python_task=`) and stamps the chosen
    # compute target onto it.
    #
    # The two historical helpers (`submit_notebook_job`, `submit_python_job`)
    # are now thin shims that delegate to `submit_job` so the codebase has
    # exactly one place that talks to `client.jobs.submit`.
    # ------------------------------------------------------------------

    async def submit_job(
        self,
        *,
        notebook_task: Optional[Dict[str, Any]] = None,
        spark_python_task: Optional[Dict[str, Any]] = None,
        submit_task: Optional[jobs.SubmitTask] = None,
        run_name: str = "One-time Job Run",
        compute: Optional[ComputeSpec] = None,
        timeout_seconds: Optional[int] = None,
        task_key: str = "main",
    ) -> str:
        """Submit a one-shot Databricks job and return its run_id.

        Exactly one of ``notebook_task``, ``spark_python_task`` or
        ``submit_task`` must be provided. ``compute`` selects the runtime
        target; ``None`` means serverless.

        ``notebook_task`` shape: ``{"notebook_path": str, "base_parameters": dict}``
        ``spark_python_task`` shape: ``{"python_file": str, "parameters": list[str]}``

        Args:
            notebook_task: Builder for a notebook task. See shape above.
            spark_python_task: Builder for an inline Python script task.
            submit_task: Pre-built ``jobs.SubmitTask`` for advanced callers
                that need full control (e.g. multi-task chains). When provided
                the ``compute`` argument is ignored — bake the cluster into
                the task yourself.
            run_name: Display name for the run in the Databricks UI.
            compute: Where to run. ``None`` = serverless.
            timeout_seconds: Optional job-level timeout (forwarded to SDK).
            task_key: Task key for the single-task case. Ignored when
                ``submit_task`` is provided.

        Returns:
            run_id as a string.
        """
        provided = [bool(notebook_task), bool(spark_python_task), bool(submit_task)]
        if sum(provided) != 1:
            raise ValueError(
                "submit_job requires exactly one of notebook_task, "
                "spark_python_task, or submit_task."
            )

        if submit_task is None:
            task_kwargs: Dict[str, Any] = {"task_key": task_key}

            if notebook_task is not None:
                task_kwargs["notebook_task"] = jobs.NotebookTask(
                    notebook_path=notebook_task["notebook_path"],
                    base_parameters=notebook_task.get("base_parameters") or {},
                )
            else:
                task_kwargs["spark_python_task"] = jobs.SparkPythonTask(
                    python_file=spark_python_task["python_file"],
                    parameters=spark_python_task.get("parameters") or [],
                )

            task_kwargs.update(self._build_compute_fields(compute))
            submit_task = jobs.SubmitTask(**task_kwargs)

        submit_kwargs: Dict[str, Any] = {
            "run_name": run_name,
            "tasks": [submit_task],
        }
        if timeout_seconds is not None:
            submit_kwargs["timeout_seconds"] = timeout_seconds

        try:
            logger.info(
                f"Submitting job '{run_name}' "
                f"(compute={'serverless' if compute is None or compute.is_serverless else 'classic'})"
            )
            run = await asyncio.to_thread(self.client.jobs.submit, **submit_kwargs)
            logger.info(f"Submitted job run: {run.run_id}")
            return str(run.run_id)
        except Exception as e:
            error_msg = f"Failed to submit job '{run_name}': {str(e)}"
            logger.error(error_msg)
            raise RetryableError(error_msg)

    def _build_compute_fields(self, compute: Optional[ComputeSpec]) -> Dict[str, Any]:
        """Translate a ``ComputeSpec`` into ``jobs.SubmitTask`` kwargs.

        Converts the plain-dict cluster spec on ``ComputeSpec`` into the SDK's
        typed wrappers (``compute.ClusterSpec`` and ``compute.Library``) at the
        last possible moment to keep ``compute.py`` SDK-free and unit-testable.
        """
        if compute is None or compute.is_serverless:
            return {}

        fields = compute.to_submit_task_fields()
        out: Dict[str, Any] = {}

        if "existing_cluster_id" in fields:
            out["existing_cluster_id"] = fields["existing_cluster_id"]

        if "new_cluster" in fields:
            out["new_cluster"] = compute_svc.ClusterSpec.from_dict(fields["new_cluster"])

        if "libraries" in fields:
            out["libraries"] = [compute_svc.Library.from_dict(lib) for lib in fields["libraries"]]

        return out

    async def upload_python_script(
        self,
        python_code: str,
        remote_path: Optional[str] = None,
    ) -> str:
        """Upload an inline Python script to the workspace and return its path.

        ``remote_path`` defaults to a unique scratch location under
        ``/tmp/agent_jobs/``. Returns the path *with* the ``Workspace`` prefix
        required by ``SparkPythonTask.python_file``.
        """
        target_path = remote_path or f"/tmp/agent_jobs/job_{uuid.uuid4().hex}.py"
        remote_dir = os.path.dirname(target_path)
        if remote_dir and remote_dir != "/":
            await asyncio.to_thread(self.client.workspace.mkdirs, remote_dir)

        content_b64 = base64.b64encode(python_code.encode("utf-8")).decode("utf-8")
        await asyncio.to_thread(
            self.client.workspace.import_,
            path=target_path,
            format=workspace.ImportFormat.AUTO,
            language=workspace.Language.PYTHON,
            content=content_b64,
            overwrite=True,
        )
        logger.debug(f"Uploaded inline Python script to {target_path}")
        return f"Workspace{target_path}"

    async def submit_notebook_job(
        self,
        notebook_path: str,
        parameters: Dict[str, str],
        run_name: str = "One-time Job Run",
        compute: Optional[ComputeSpec] = None,
    ) -> str:
        """Submit a one-time notebook job (back-compat shim over ``submit_job``).

        New code should call ``submit_job`` directly. This shim preserves the
        original signature for existing callers (and for the dedup integration
        test that mocks this method by name).
        """
        return await self.submit_job(
            notebook_task={
                "notebook_path": notebook_path,
                "base_parameters": parameters,
            },
            run_name=run_name,
            compute=compute,
        )

    async def submit_python_job(
        self,
        python_code: str,
        parameters: List[str],
        run_name: str = "One-time Python Job",
        compute: Optional[ComputeSpec] = None,
    ) -> str:
        """Submit an inline Python script (back-compat shim over ``submit_job``).

        Uploads ``python_code`` to a scratch workspace path, then calls
        ``submit_job`` with a ``spark_python_task``.
        """
        python_file = await self.upload_python_script(python_code)
        return await self.submit_job(
            spark_python_task={
                "python_file": python_file,
                "parameters": parameters,
            },
            run_name=run_name,
            compute=compute,
        )

    async def get_run_output(
        self,
        run_id: str,
        task_key: str = "main",
    ) -> Dict[str, Any]:
        """Fetch terminal output (notebook return value, logs, task values).

        Returns ``{}`` if the run has no output yet. Raises ``PermanentError``
        if the run does not exist.
        """
        try:
            run_id_int = int(run_id)
            run = await asyncio.to_thread(self.client.jobs.get_run, run_id_int)
            task = next((t for t in (run.tasks or []) if t.task_key == task_key), None)
            if task is None or task.run_id is None:
                return {}

            output = await asyncio.to_thread(self.client.jobs.get_run_output, task.run_id)
            result: Dict[str, Any] = {}
            if getattr(output, "notebook_output", None) is not None:
                result["notebook_result"] = output.notebook_output.result
                result["truncated"] = output.notebook_output.truncated
            if getattr(output, "logs", None) is not None:
                result["logs"] = output.logs
            if getattr(output, "logs_truncated", None) is not None:
                result["logs_truncated"] = output.logs_truncated
            if getattr(output, "error", None) is not None:
                result["error"] = output.error
            if getattr(output, "error_trace", None) is not None:
                result["error_trace"] = output.error_trace
            return result
        except Exception as e:
            error_msg = str(e)
            if "does not exist" in error_msg:
                raise PermanentError(f"Run {run_id} does not exist.")
            logger.error(f"Failed to get run output for {run_id}: {error_msg}")
            raise RetryableError(f"Failed to get run output: {error_msg}")

    async def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """
        Get the status of a Databricks job run.
        
        Args:
            run_id: The ID of the job run
            
        Returns:
            Dictionary with state and error information
        """
        try:
            run_id_int = int(run_id)
            run = await asyncio.to_thread(self.client.jobs.get_run, run_id_int)
            
            state = run.state
            status = {
                "life_cycle_state": state.life_cycle_state.value,
                "result_state": state.result_state.value if state.result_state else None,
                "state_message": state.state_message,
                "is_active": state.life_cycle_state in [
                    jobs.RunLifeCycleState.PENDING,
                    jobs.RunLifeCycleState.RUNNING,
                    jobs.RunLifeCycleState.BLOCKED,
                    jobs.RunLifeCycleState.QUEUED
                ],
                "is_completed": state.life_cycle_state == jobs.RunLifeCycleState.TERMINATED,
                "is_successful": state.result_state == jobs.RunResultState.SUCCESS if state.result_state else False
            }
            
            return status
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to get run status for {run_id}: {error_msg}")
            if "does not exist" in error_msg:
                raise PermanentError(f"Run {run_id} does not exist.")
            raise RetryableError(f"Failed to get run status: {error_msg}")

