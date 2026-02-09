from typing import Dict, Any, List
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.models.request import RequestStatus
from app.core.exceptions import PermanentError
from app.providers.terraform.client import TerraformProvider
from app.providers.terraform.volume_provider import VolumeGitOpsProvider
from app.core.config import settings
from app.state_machines.facts import has_fact, add_fact
import logging
import yaml

logger = logging.getLogger(__name__)


class CreateCatalogSchemaStateMachine(BaseRequestStateMachine):
    """
    State machine for creating a Unity Catalog or Schema.
    Uses Terraform GitOps provider.
    """
    # States
    pending = State("pending", initial=True)
    terraform_planning = State("terraform_planning")
    awaiting_approval = State("awaiting_approval")
    terraform_applying = State("terraform_applying")
    
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    # Transitions
    submit = pending.to(terraform_planning, cond="has_request_submitted")
    
    # After plan is submitted, we wait for callback (plan received)
    finish_planning = terraform_planning.to(awaiting_approval, cond="has_terraform_plan")
    
    # After approval, we start applying
    approve_admin = awaiting_approval.to(terraform_applying, cond="has_platform_admin_approval")
    
    # After apply is triggered, we wait for callback (apply received)
    finish_applying = terraform_applying.to(completed, cond="has_terraform_apply_success")
    
    # Apply can fail
    apply_failed = terraform_applying.to(failed, cond="has_terraform_apply_failed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        terraform_planning.to(rejected, cond="has_request_rejected") |
        awaiting_approval.to(rejected, cond="has_request_rejected") |
        terraform_applying.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) | 
        terraform_planning.to(failed) | 
        awaiting_approval.to(failed) | 
        terraform_applying.to(failed)
    )

    # Approval node configuration
    # Note: Approval happens AFTER planning
    APPROVAL_NODES = {
        "awaiting_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval (Review Plan)"}
    }


    
    # ...

    def __init__(self, request, db_session):
        # Handle legacy states
        if request.current_state == "provisioning" or request.current_state == "manager_approval":
            request.current_state = "terraform_planning"
            
        super().__init__(request, db_session)
        
    def _get_provider(self):
        """Lazy load provider based on GITOPS_MODE setting."""
        gitops_mode = settings.GITOPS_MODE or "volume"
        
        if gitops_mode == "volume":
            # Volume-based GitOps (recommended - avoids IP allowlist issues)
            volume_path = settings.GITOPS_VOLUME_PATH
            if not volume_path:
                raise PermanentError("GITOPS_VOLUME_PATH not set for volume mode.")
            
            logger.info(f"Using VolumeGitOpsProvider with path: {volume_path}")
            return VolumeGitOpsProvider(
                volume_path=volume_path,
                config={
                    "environment": settings.DEFAULT_ENVIRONMENT or "dev",
                    "git_username": settings.GIT_USERNAME,
                    "git_email": settings.GIT_EMAIL,
                }
            )
        else:
            # Direct Git mode (requires network access to GitHub)
            repo_url = settings.INFRA_REPO_URL
            if not repo_url:
                logger.warning("INFRA_REPO_URL not set.")
                
            logger.info(f"Using TerraformProvider with repo: {repo_url}")
            return TerraformProvider(
                repo_url=repo_url,
                branch=settings.INFRA_REPO_BRANCH or "main",
                config={
                    "git_username": settings.GIT_USERNAME,
                    "git_email": settings.GIT_EMAIL,
                    "ssh_key_path": settings.GIT_SSH_KEY_PATH,
                    "git_token": settings.get_git_token(),
                    "github_app_id": settings.GITHUB_APP_ID,
                    "github_app_private_key": settings.get_github_app_private_key(),
                    "github_app_installation_id": settings.GITHUB_APP_INSTALLATION_ID,
                }
            )

    async def on_enter_terraform_planning_async(self):
        """Execute async tasks for terraform_planning state."""
        await self._run_plan()
        
    async def on_enter_terraform_applying_async(self):
        """Execute async tasks for terraform_applying state."""
        # Notify user: Approved
        await self._send_notification(
            subject=f"Catalog/Schema Request Approved: {self.request.title}",
            body=f"Your request '{self.request.title}' has been approved. Applying changes now..."
        )
        await self._run_apply()
            
    async def on_enter_completed_async(self):
        """Execute async tasks for completed state."""
        # Notify user: Success
        await self._send_notification(
            subject=f"Catalog/Schema Created: {self.request.title}",
            body=f"Your request '{self.request.title}' has been successfully completed."
        )
            
    async def _run_plan(self):
        """Trigger Terraform Plan mechanism."""
        # Check if already done (plan submitted)
        if has_fact(self.db, self.request.id, "terraform_plan_started"):
             # If we have started plan, we are just waiting for the callback
             # which adds "terraform_plan_received" fact
             return

        params = self.request.state_context or {}
        asset_type = params.get("type", "").lower()
        name = params.get("name")
        catalog = params.get("catalog", "main")
        environment = params.get("environment", settings.DEFAULT_ENVIRONMENT or "dev")
        
        if not name:
             raise PermanentError("Asset name is required")

        try:
            logger.info(f"Starting Terraform Plan for {self.request.id}")
            provider = self._get_provider()
            
            # Construct YAML content matching the Terraform repo format
            content = self._generate_yaml_spec(asset_type, name, catalog, params)
            
            # Path structure: envs/{env}/resources/{name}.yaml
            target_file = f"envs/{environment}/resources/{name}.yaml"
            
            # This creates the branch and pushes it
            await provider.plan(
                request_id=self.request.id,
                target_file=target_file,
                content=content,
                commit_message=f"Plan: {asset_type} {name} in {catalog}"
            )
            
            # Record fact that we started
            add_fact(self.db, self.request.id, "terraform_plan_started", {}, actor="system")
            logger.info(f"Terraform Plan started for {self.request.id}")
            
        except Exception as e:
            logger.error(f"Plan failed: {e}")
            raise e

    async def _run_apply(self):
        """Trigger Terraform Apply mechanism."""
        # Check if already done
        if has_fact(self.db, self.request.id, "terraform_apply_started"):
            return

        try:
            logger.info(f"Starting Terraform Apply for {self.request.id}")
            provider = self._get_provider()
            
            await provider.apply(request_id=self.request.id)
            
            add_fact(self.db, self.request.id, "terraform_apply_started", {}, actor="system")
            logger.info(f"Terraform Apply started for {self.request.id}")
            
        except Exception as e:
            logger.error(f"Apply failed: {e}")
            raise e

    def _generate_yaml_spec(self, asset_type: str, name: str, catalog: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate YAML content matching the Terraform repo format.
        
        Expected format for schema:
        resource_type: schema
        name: my_schema
        properties:
          catalog: my_catalog
          comment: "Description"
          grants:
            - principal: "user@example.com"
              privileges: [USE_SCHEMA, SELECT]
        """
        # Build properties
        properties = {
            "catalog": catalog,
            "comment": params.get("comment", f"Created via ATLAS request"),
        }
        
        # Add grants if specified
        grants = params.get("grants", [])
        if grants:
            properties["grants"] = grants
        elif params.get("requester_email"):
            # Default: grant requester basic permissions
            properties["grants"] = [
                {
                    "principal": params.get("requester_email"),
                    "privileges": ["USE_SCHEMA", "CREATE_TABLE", "SELECT"]
                }
            ]
        
        return {
            "resource_type": asset_type or "schema",
            "name": name,
            "properties": properties
        }

    @property
    def has_terraform_plan(self) -> bool:
        """Check if we received the plan callback."""
        return has_fact(self.db, self.request.id, "terraform_plan_received")
        
    @property
    def has_terraform_apply_success(self) -> bool:
        """Check if we received the apply callback with success."""
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="success")
    
    @property
    def has_terraform_apply_failed(self) -> bool:
        """Check if apply failed."""
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="failure")

    def on_enter_completed(self):
        pass
    
    def on_enter_failed(self):
        """Handle transition to failed state."""
        # Get the error from the fact
        from app.state_machines.facts import get_fact_data
        fact_data = get_fact_data(self.db, self.request.id, "terraform_apply_received", default={})
        error_msg = fact_data.get("error", "Unknown error")
        logger.error(f"[{self.request.id}] Terraform apply failed: {error_msg}")
