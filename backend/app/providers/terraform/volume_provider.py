"""
Volume-based GitOps Provider.

This provider writes infrastructure requests to a Unity Catalog Volume instead of
directly pushing to Git. A GitHub Actions workflow polls the volume and creates PRs.

This architecture avoids IP allowlist issues where the Databricks App cannot reach GitHub.

Note: Uses Databricks SDK Files API since UC Volumes are not FUSE-mounted in Databricks Apps.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import json
import logging
import yaml
from datetime import datetime
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
        self.username = self.config.get("git_username", "ATLAS Bot")
        self.email = self.config.get("git_email", "atlas-bot@databricks.com")
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
            # Create request file with metadata
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
                "submitted_at": datetime.utcnow().isoformat(),
                "action": "plan"  # plan = create PR, apply = merge PR
            }
            
            # Write to pending directory using SDK
            pending_file = f"{self.volume_path}/pending/{request_id}.yaml"
            yaml_content = yaml.dump(request_data, default_flow_style=False)
            self._write_file(pending_file, yaml_content)
            
            logger.info(f"Request {request_id} written to {pending_file}")
            
            return {
                "success": True,
                "status": "pending",
                "message": "Request submitted. Waiting for GitHub Actions to create PR.",
                "pending_file": pending_file
            }
            
        except Exception as e:
            logger.error(f"Failed to write request {request_id}: {e}")
            raise RetryableError(f"Failed to write request: {e}")
    
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
            status["apply_requested_at"] = datetime.utcnow().isoformat()
            
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
