"""
Volume-based GitOps Provider.

This provider writes infrastructure requests to a Unity Catalog Volume instead of
directly pushing to Git. A GitHub Actions workflow polls the volume and creates PRs.

This architecture avoids IP allowlist issues where the Databricks App cannot reach GitHub.

Note: Uses Databricks SDK Files API since UC Volumes are not FUSE-mounted in Databricks Apps.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.config import settings
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import copy
import json
import logging
import yaml
from datetime import datetime, timezone
from io import BytesIO

logger = logging.getLogger(__name__)


def get_workspace_client():
    """Get a WorkspaceClient instance."""
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


class VolumeGitOpsProvider(BaseProvider):
    """
    Volume-based GitOps provider using Databricks SDK Files API.
    
    Instead of pushing directly to Git, this provider:
    1. Writes request YAML files to a Unity Catalog Volume via SDK
    2. A GitHub Actions workflow polls the volume and creates PRs
    3. Status updates are written back to the volume
    
    Volume structure:
    /Volumes/{catalog}/{schema}/{volume}/
    ├── pending/           # New requests waiting to be processed
    │   └── req-123.yaml
    ├── processing/        # Requests being processed by GitHub Actions
    │   └── req-456.yaml
    ├── completed/         # Completed requests
    │   └── req-789.yaml
    └── status/            # Status updates (PR URL, apply result, etc.)
        └── req-123.json
    """
    
    def __init__(self, volume_path: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Volume GitOps Provider.
        
        Args:
            volume_path: Path to the Unity Catalog Volume (e.g., /Volumes/catalog/schema/volume)
            config: Additional configuration including:
                - environment: Target environment (dev, staging, prod)
                - git_username: Git commit author name (for metadata)
                - git_email: Git commit author email (for metadata)
        """
        super().__init__(config)
        self.volume_path = volume_path.rstrip('/')
        self.environment = self.config.get("environment", "dev")
        self.username = self.config.get("git_username", settings.GIT_USERNAME)
        self.email = self.config.get("git_email", settings.GIT_EMAIL)
        self._client = None

        logger.info(f"VolumeGitOpsProvider initialized: volume={self.volume_path}, env={self.environment}")

    @property
    def client(self):
        """Lazy-load the WorkspaceClient."""
        if self._client is None:
            self._client = get_workspace_client()
        return self._client
    
    def _write_file(self, path: str, content: str):
        """Write content to a file in the volume using Databricks SDK Files API."""
        try:
            self.client.files.upload(
                file_path=path,
                contents=BytesIO(content.encode('utf-8')),
                overwrite=True
            )
            logger.debug(f"Wrote file: {path}")
        except Exception as e:
            logger.error(f"Failed to write file {path}: {e}")
            raise
    
    def _read_file(self, path: str) -> Optional[str]:
        """Read content from a file in the volume using Databricks SDK Files API."""
        try:
            response = self.client.files.download(file_path=path)
            content = response.contents.read().decode('utf-8')
            return content
        except Exception as e:
            logger.debug(f"Failed to read file {path}: {e}")
            return None
    
    def _file_exists(self, path: str) -> bool:
        """Check if a file exists in the volume."""
        try:
            self.client.files.get_status(file_path=path)
            return True
        except Exception:
            return False
    
    def _list_files(self, directory: str) -> list:
        """List files in a directory in the volume."""
        try:
            result = self.client.files.list_directory_contents(directory_path=directory)
            return [f.path for f in result]
        except Exception as e:
            logger.debug(f"Failed to list directory {directory}: {e}")
            return []
    
    def _delete_file(self, path: str):
        """Delete a file from the volume."""
        try:
            self.client.files.delete(file_path=path)
            logger.debug(f"Deleted file: {path}")
        except Exception as e:
            logger.warning(f"Failed to delete file {path}: {e}")
    
    def _get_resource_name(self, content: Dict[str, Any]) -> str:
        """Extract resource name from content for YAML file naming."""
        return content.get("name", "unknown")
    
    def check_access(self, resource_name: str, principal: str = None) -> Dict[str, Any]:
        """
        Check existing grants for a resource.
        
        Args:
            resource_name: Name of the resource to check
            principal: Optional - if provided, check grants for this specific principal
            
        Returns:
            Dict with:
                - exists: bool - whether the resource exists
                - grants: list - list of grants on the resource
                - principal_grants: list - privileges for the specified principal (if provided)
        """
        existing = self._find_existing_yaml(resource_name)
        
        if not existing:
            return {
                "exists": False,
                "grants": [],
                "principal_grants": [],
                "message": f"Resource '{resource_name}' not found"
            }
        
        content = existing.get("content", {})
        grants = content.get("properties", {}).get("grants", [])
        
        result = {
            "exists": True,
            "grants": grants,
            "resource_name": resource_name,
            "catalog": content.get("properties", {}).get("catalog", "unknown")
        }
        
        if principal:
            principal_grant = next((g for g in grants if g.get("principal") == principal), None)
            result["principal_grants"] = principal_grant.get("privileges", []) if principal_grant else []
            result["has_grants"] = bool(principal_grant)
        
        return result
    
    def _find_existing_yaml(self, resource_name: str) -> Optional[Dict[str, Any]]:
        """
        Find existing YAML for a resource in pending/, processing/, or completed/ folders.
        
        Searches in two ways:
        1. By filename: {resource_name}.yaml (new naming convention)
        2. By content: looks for YAMLs where content.name == resource_name (old req-id naming)
        
        Returns the full YAML data if found, None otherwise.
        """
        # First, try direct filename match (new naming convention)
        for folder in ["pending", "processing", "completed"]:
            file_path = f"{self.volume_path}/{folder}/{resource_name}.yaml"
            content = self._read_file(file_path)
            if content:
                try:
                    data = yaml.safe_load(content)
                    data["_source_folder"] = folder
                    data["_source_path"] = file_path
                    logger.debug(f"Found existing YAML by filename: {file_path}")
                    return data
                except Exception:
                    continue
        
        # Second, search by content name (for old req-id.yaml naming convention)
        logger.debug(f"Searching for {resource_name} by content in all YAML files")
        for folder in ["pending", "processing", "completed"]:
            dir_path = f"{self.volume_path}/{folder}"
            try:
                files = self._list_files(dir_path)
                for file_path in files:
                    if not file_path.endswith('.yaml'):
                        continue
                    content = self._read_file(file_path)
                    if content:
                        try:
                            data = yaml.safe_load(content)
                            # Check if the content.name matches the resource we're looking for
                            yaml_content = data.get("content", {})
                            if yaml_content.get("name") == resource_name:
                                data["_source_folder"] = folder
                                data["_source_path"] = file_path
                                logger.info(f"Found existing YAML by content name: {file_path}")
                                return data
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Error listing {dir_path}: {e}")
                continue
        
        return None
    
    def _move_file(self, source: str, dest: str):
        """Move a file by copying then deleting."""
        content = self._read_file(source)
        if content:
            self._write_file(dest, content)
            self._delete_file(source)
    
    @retry_on_retryable(max_attempts=3)
    async def plan(self, request_id: str, target_file: str, content: Dict[str, Any], commit_message: str) -> Dict[str, Any]:
        """
        Submit infrastructure request for planning.
        
        Writes the request to the pending/ directory. GitHub Actions will:
        1. Pick up the request
        2. Create a branch in the Terraform repo
        3. Commit the YAML file
        4. Create a PR
        5. Write status back to status/ directory
        
        Args:
            request_id: Unique request identifier
            target_file: Path to YAML file relative to repo root (e.g., envs/dev/resources/my_schema.yaml)
            content: Dictionary content to write
            commit_message: Message for the git commit
        """
        try:
            resource_name = self._get_resource_name(content)
            
            # Check for existing YAML (for updates)
            existing = self._find_existing_yaml(resource_name)
            if existing:
                # Merge with existing content
                logger.info(f"Found existing YAML for {resource_name}, merging changes")
                existing_content = existing.get("content", {})
                # Merge properties (new values override old)
                merged_content = self._merge_content(existing_content, content)
                content = merged_content
                
                # If in completed/, we need to move back to pending
                if existing.get("_source_folder") == "completed":
                    self._delete_file(existing["_source_path"])
            
            # Create request file with metadata
            # Use resource name for file, not request ID
            request_data = {
                "request_id": request_id,
                "target_file": target_file,
                "content": content,
                "commit_message": commit_message,
                "environment": self.environment,
                "author": {
                    "name": self.username,
                    "email": self.email
                },
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "action": "plan"  # plan = create PR, apply = merge PR
            }
            
            # Write to pending directory using resource name (not request ID)
            pending_file = f"{self.volume_path}/pending/{resource_name}.yaml"
            yaml_content = yaml.dump(request_data, default_flow_style=False)
            self._write_file(pending_file, yaml_content)
            
            logger.info(f"Request {request_id} for resource {resource_name} written to {pending_file}")
            
            return {
                "success": True,
                "status": "pending",
                "message": "Request submitted. Waiting for GitHub Actions to create PR.",
                "pending_file": pending_file,
                "resource_name": resource_name
            }
            
        except Exception as e:
            logger.error(f"Failed to write request {request_id}: {e}")
            raise RetryableError(f"Failed to write request: {e}")
    
    def _merge_content(self, existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        """Merge new content into existing, handling nested properties."""
        result = existing.copy()
        
        for key, value in new.items():
            if key == "properties" and "properties" in result:
                # Merge properties dict
                result["properties"] = {**result.get("properties", {}), **value}
            else:
                result[key] = value
        
        return result
    
    @retry_on_retryable(max_attempts=3)
    async def apply(self, request_id: str) -> Dict[str, Any]:
        """
        Request to apply (merge) the PR.
        
        Updates the request status to indicate it should be merged.
        GitHub Actions will detect this and merge the PR.
        
        Args:
            request_id: Unique request identifier
        """
        try:
            # Check current status
            status = await self.get_status(request_id)
            if not status:
                raise PermanentError(f"No status found for request {request_id}")
            
            if status.get("pr_state") != "open":
                raise PermanentError(f"PR is not open, cannot apply. Current state: {status.get('pr_state')}")
            
            # Update status to request merge
            status["apply_requested"] = True
            status["apply_requested_at"] = datetime.now(timezone.utc).isoformat()
            
            status_file = f"{self.volume_path}/status/{request_id}.json"
            self._write_file(status_file, json.dumps(status, indent=2))
            
            logger.info(f"Apply requested for {request_id}")
            
            return {
                "success": True,
                "status": "apply_requested",
                "message": "Apply requested. GitHub Actions will merge the PR."
            }
            
        except PermanentError:
            raise
        except Exception as e:
            logger.error(f"Failed to request apply for {request_id}: {e}")
            raise RetryableError(f"Failed to request apply: {e}")
    
    async def get_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the current status of a request.
        
        Returns status including:
        - pr_url: URL of the created PR (if created)
        - pr_state: open, merged, closed
        - plan_output: Terraform plan output (if available)
        - apply_output: Terraform apply output (if available)
        - error: Error message (if failed)
        """
        status_file = f"{self.volume_path}/status/{request_id}.json"
        content = self._read_file(status_file)
        
        if not content:
            return None
        
        try:
            return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to parse status for {request_id}: {e}")
            return None
    
    async def get_resource(self, target_file: str) -> Optional[Dict[str, Any]]:
        """
        Get the content of a request file.
        
        This checks pending, processing, and completed directories.
        """
        for status_dir in ["pending", "processing", "completed"]:
            dir_path = f"{self.volume_path}/{status_dir}"
            files = self._list_files(dir_path)
            
            for file_path in files:
                if not file_path.endswith('.yaml'):
                    continue
                try:
                    content = self._read_file(file_path)
                    if content:
                        data = yaml.safe_load(content)
                        if data.get("target_file") == target_file:
                            return data.get("content")
                except Exception:
                    continue
        
        return None
    
    async def health_check(self) -> bool:
        """Check if the volume is accessible."""
        try:
            # Try to list the pending directory
            pending_dir = f"{self.volume_path}/pending"
            self._list_files(pending_dir)
            return True
        except Exception:
            return False
    
    def list_pending_requests(self) -> list:
        """List all pending request IDs."""
        pending_dir = f"{self.volume_path}/pending"
        files = self._list_files(pending_dir)
        
        # Extract request IDs from file paths
        request_ids = []
        for f in files:
            if f.endswith('.yaml'):
                # Extract filename without extension
                filename = f.split('/')[-1]
                request_id = filename.replace('.yaml', '')
                request_ids.append(request_id)
        
        return request_ids
    
    @retry_on_retryable(max_attempts=3)
    async def grant_access(
        self, 
        request_id: str, 
        resource_type: str,
        resource_name: str, 
        catalog: str,
        principal: str, 
        privileges: list,
        commit_message: str
    ) -> Dict[str, Any]:
        """
        Add grants to an existing resource.
        
        Finds the existing YAML, adds the grant, and moves to pending for apply.
        
        Args:
            request_id: Unique request identifier
            resource_type: Type of resource (schema, catalog, table)
            resource_name: Name of the resource
            catalog: Catalog containing the resource
            principal: User, group, or service principal to grant access to
            privileges: List of privileges to grant (e.g., ["USE_SCHEMA", "SELECT"])
            commit_message: Message for the git commit
        """
        try:
            # Find existing YAML
            existing = self._find_existing_yaml(resource_name)
            
            if not existing:
                # Resource doesn't exist yet - create new YAML with just grants
                logger.info(f"No existing YAML for {resource_name}, creating new with grants")
                content = {
                    "resource_type": resource_type,
                    "name": resource_name,
                    "properties": {
                        "catalog": catalog,
                        "grants": [{"principal": principal, "privileges": privileges}]
                    }
                }
            else:
                # Merge grants into existing content - preserve ALL existing properties
                logger.info(f"Found existing YAML for {resource_name}, adding grants")
                
                # Deep copy to avoid modifying the original
                content = copy.deepcopy(existing.get("content", {}))
                
                logger.debug(f"Existing content for {resource_name}: {content}")
                
                # Initialize properties if not present
                if "properties" not in content:
                    content["properties"] = {"catalog": catalog}
                
                # Ensure catalog is set (preserve existing or use provided)
                if "catalog" not in content["properties"]:
                    content["properties"]["catalog"] = catalog
                
                # Initialize grants list if not present
                if "grants" not in content["properties"]:
                    content["properties"]["grants"] = []
                
                # Check if principal already has grants
                grants = content["properties"]["grants"]
                existing_grant = next((g for g in grants if g.get("principal") == principal), None)
                
                if existing_grant:
                    # Check if all requested privileges already exist
                    existing_privs = set(existing_grant.get("privileges", []))
                    new_privs = set(privileges)
                    
                    if new_privs.issubset(existing_privs):
                        # All requested privileges already exist - no change needed
                        logger.info(f"All privileges {privileges} already exist for {principal} on {resource_name}")
                        return {
                            "success": True,
                            "status": "no_change",
                            "message": f"Access already exists: {principal} already has {list(new_privs)} on {resource_name}",
                            "resource_name": resource_name,
                            "existing_privileges": list(existing_privs)
                        }
                    
                    # Merge privileges (add new ones, keep existing)
                    existing_grant["privileges"] = list(existing_privs | new_privs)
                    added_privs = new_privs - existing_privs
                    logger.info(f"Adding new privileges {list(added_privs)} for {principal} (already had: {list(existing_privs)})")
                else:
                    # Add new grant entry
                    grants.append({"principal": principal, "privileges": privileges})
                    logger.info(f"Added new grant for {principal}: {privileges}")
                
                logger.debug(f"Final content for {resource_name}: {content}")
                
                # Remove from completed folder if needed
                if existing.get("_source_folder") == "completed":
                    self._delete_file(existing["_source_path"])
            
            # Create request data
            target_file = f"envs/{self.environment}/resources/pending/{resource_name}.yaml"
            request_data = {
                "request_id": request_id,
                "target_file": target_file,
                "content": content,
                "commit_message": commit_message,
                "environment": self.environment,
                "author": {"name": self.username, "email": self.email},
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "action": "plan"
            }
            
            # Write to pending
            pending_file = f"{self.volume_path}/pending/{resource_name}.yaml"
            self._write_file(pending_file, yaml.dump(request_data, default_flow_style=False))
            
            logger.info(f"Grant access for {resource_name} written to {pending_file}")
            
            return {
                "success": True,
                "status": "pending",
                "message": f"Grant access request submitted for {resource_name}",
                "resource_name": resource_name
            }
            
        except Exception as e:
            logger.error(f"Failed to grant access for {resource_name}: {e}")
            raise RetryableError(f"Failed to grant access: {e}")
    
    @retry_on_retryable(max_attempts=3)
    async def revoke_access(
        self, 
        request_id: str, 
        resource_type: str,
        resource_name: str, 
        catalog: str,
        principal: str, 
        privileges: list = None,
        commit_message: str = None
    ) -> Dict[str, Any]:
        """
        Revoke grants from an existing resource.
        
        Finds the existing YAML, removes the grant, and moves to pending for apply.
        
        Args:
            request_id: Unique request identifier
            resource_type: Type of resource (schema, catalog, table)
            resource_name: Name of the resource
            catalog: Catalog containing the resource
            principal: User, group, or service principal to revoke access from
            privileges: Specific privileges to revoke (None = revoke all)
            commit_message: Message for the git commit
        """
        try:
            # Find existing YAML
            existing = self._find_existing_yaml(resource_name)
            
            if not existing:
                raise PermanentError(f"No existing resource found for {resource_name}")
            
            # Deep copy to preserve all existing properties
            content = copy.deepcopy(existing.get("content", {}))
            
            logger.debug(f"Existing content for revoke on {resource_name}: {content}")
            
            if "properties" not in content or "grants" not in content.get("properties", {}):
                raise PermanentError(f"No grants found for {resource_name}")
            
            grants = content["properties"]["grants"]
            
            # Find the grant for this principal
            grant_idx = next((i for i, g in enumerate(grants) if g.get("principal") == principal), None)
            
            if grant_idx is None:
                raise PermanentError(f"No grants found for principal {principal} on {resource_name}")
            
            if privileges:
                # Remove specific privileges
                existing_privs = set(grants[grant_idx].get("privileges", []))
                privs_to_revoke = set(privileges)
                
                # Check if any of the requested privileges actually exist
                actual_revokes = existing_privs & privs_to_revoke
                if not actual_revokes:
                    logger.info(f"None of the privileges {privileges} exist for {principal} on {resource_name}")
                    return {
                        "success": True,
                        "status": "no_change",
                        "message": f"Privileges {list(privs_to_revoke)} don't exist for {principal} on {resource_name}",
                        "resource_name": resource_name,
                        "existing_privileges": list(existing_privs)
                    }
                
                remaining_privs = existing_privs - privs_to_revoke
                
                if remaining_privs:
                    grants[grant_idx]["privileges"] = list(remaining_privs)
                    logger.info(f"Revoking {list(actual_revokes)} from {principal}, remaining: {list(remaining_privs)}")
                else:
                    # No privileges left, remove the entire grant
                    del grants[grant_idx]
                    logger.info(f"Revoking all privileges from {principal} on {resource_name}")
            else:
                # Remove all privileges for this principal
                del grants[grant_idx]
                logger.info(f"Removing all grants for {principal} on {resource_name}")
            
            # Remove from completed folder if needed
            if existing.get("_source_folder") == "completed":
                self._delete_file(existing["_source_path"])
            
            # Create request data
            target_file = f"envs/{self.environment}/resources/pending/{resource_name}.yaml"
            request_data = {
                "request_id": request_id,
                "target_file": target_file,
                "content": content,
                "commit_message": commit_message or f"Revoke access from {principal} on {resource_name}",
                "environment": self.environment,
                "author": {"name": self.username, "email": self.email},
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "action": "plan"
            }
            
            # Write to pending
            pending_file = f"{self.volume_path}/pending/{resource_name}.yaml"
            self._write_file(pending_file, yaml.dump(request_data, default_flow_style=False))
            
            logger.info(f"Revoke access for {resource_name} written to {pending_file}")
            
            return {
                "success": True,
                "status": "pending", 
                "message": f"Revoke access request submitted for {resource_name}",
                "resource_name": resource_name
            }
            
        except PermanentError:
            raise
        except Exception as e:
            logger.error(f"Failed to revoke access for {resource_name}: {e}")
            raise RetryableError(f"Failed to revoke access: {e}")
