"""
Volume-based GitOps Provider.

This provider writes infrastructure requests to a Unity Catalog Volume instead of
directly pushing to Git. A GitHub Actions workflow polls the volume and creates PRs.

This architecture avoids IP allowlist issues where the Databricks App cannot reach GitHub.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import os
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class VolumeGitOpsProvider(BaseProvider):
    """
    Volume-based GitOps provider.
    
    Instead of pushing directly to Git, this provider:
    1. Writes request YAML files to a Unity Catalog Volume
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
        
        # Ensure directories exist
        self._ensure_directories()
        
        logger.info(f"VolumeGitOpsProvider initialized: volume={self.volume_path}, env={self.environment}")
    
    def _ensure_directories(self):
        """Create the directory structure if it doesn't exist."""
        dirs = ["pending", "processing", "completed", "status"]
        for d in dirs:
            dir_path = Path(self.volume_path) / d
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Could not create directory {dir_path}: {e}")
    
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
            
            # Write to pending directory
            pending_file = Path(self.volume_path) / "pending" / f"{request_id}.yaml"
            with open(pending_file, "w") as f:
                yaml.dump(request_data, f, default_flow_style=False)
            
            logger.info(f"Request {request_id} written to {pending_file}")
            
            return {
                "success": True,
                "status": "pending",
                "message": "Request submitted. Waiting for GitHub Actions to create PR.",
                "pending_file": str(pending_file)
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
            
            status_file = Path(self.volume_path) / "status" / f"{request_id}.json"
            with open(status_file, "w") as f:
                json.dump(status, f, indent=2)
            
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
        status_file = Path(self.volume_path) / "status" / f"{request_id}.json"
        
        if not status_file.exists():
            return None
        
        try:
            with open(status_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read status for {request_id}: {e}")
            return None
    
    async def get_resource(self, target_file: str) -> Optional[Dict[str, Any]]:
        """
        Get the content of a request file.
        
        This checks pending, processing, and completed directories.
        """
        for status_dir in ["pending", "processing", "completed"]:
            # Try to find by target_file in the request content
            dir_path = Path(self.volume_path) / status_dir
            if not dir_path.exists():
                continue
                
            for file_path in dir_path.glob("*.yaml"):
                try:
                    with open(file_path, "r") as f:
                        data = yaml.safe_load(f)
                        if data.get("target_file") == target_file:
                            return data.get("content")
                except Exception:
                    continue
        
        return None
    
    async def health_check(self) -> bool:
        """Check if the volume is accessible."""
        try:
            pending_dir = Path(self.volume_path) / "pending"
            return pending_dir.exists()
        except Exception:
            return False
    
    def list_pending_requests(self) -> list:
        """List all pending request IDs."""
        pending_dir = Path(self.volume_path) / "pending"
        if not pending_dir.exists():
            return []
        
        return [f.stem for f in pending_dir.glob("*.yaml")]
