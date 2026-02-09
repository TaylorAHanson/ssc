"""
Service Principal state machine.
Uses Terraform GitOps provider.
"""
from typing import Dict, Any
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.providers.terraform.client import TerraformProvider
from app.providers.terraform.volume_provider import VolumeGitOpsProvider
from app.core.config import settings
from app.core.exceptions import PermanentError
import logging
import yaml

logger = logging.getLogger(__name__)


class ServicePrincipalStateMachine(BaseRequestStateMachine):
    """
    State machine for creating a Service Principal via Terraform.
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
    
    # Planning -> Approval
    finish_planning = terraform_planning.to(awaiting_approval, cond="has_terraform_plan")
    
    # Approval -> Applying
    approve_admin = awaiting_approval.to(terraform_applying, cond="has_platform_admin_approval")
    
    # Applying -> Completed
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
    APPROVAL_NODES = {
        "awaiting_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval (Review Plan)"}
    }

    def __init__(self, request, db_session):
        # Handle legacy states
        if request.current_state == "provisioning":
            request.current_state = "terraform_planning"
            
        super().__init__(request, db_session)
        
    def _get_provider(self):
        """Lazy load provider based on GITOPS_MODE setting."""
        gitops_mode = settings.GITOPS_MODE or "volume"
        
        if gitops_mode == "volume":
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
        # Notify user: Approval received, applying changes
        await self._send_notification(
            subject=f"Service Principal Approved: {self.request.title}",
            body=f"Your request for service principal '{self.request.title}' has been approved. Provisioning changes now..."
        )
        await self._run_apply()
        
    async def on_enter_completed_async(self):
        """Execute async tasks for completed state."""
        # Notify user: Success
        await self._send_notification(
            subject=f"Service Principal Created: {self.request.title}",
            body=f"Your service principal '{self.request.title}' has been successfully created."
        )
            
    async def _run_plan(self):
        """Trigger Terraform Plan."""
        if has_fact(self.db, self.request.id, "terraform_plan_started"):
             return

        params = self.request.state_context or {}
        name = params.get("name") or self.request.title
        
        if not name:
             raise PermanentError("Service Principal name is required")

        try:
            logger.info(f"Starting Terraform Plan for SP {name} ({self.request.id})")
            provider = self._get_provider()
            
            # Construct YAML spec for Service Principal
            content = {
                "resource_type": "service_principal",
                "name": name,
                "properties": params
            }
            target_file = f"resources/service_principals/{name}.yaml"
            
            await provider.plan(
                request_id=self.request.id,
                target_file=target_file,
                content=content,
                commit_message=f"Plan: Service Principal {name}"
            )
            
            add_fact(self.db, self.request.id, "terraform_plan_started", {}, actor="system")
            
        except Exception as e:
            logger.error(f"Plan failed: {e}")
            raise e

    async def _run_apply(self):
        """Trigger Terraform Apply."""
        if has_fact(self.db, self.request.id, "terraform_apply_started"):
            return

        try:
            logger.info(f"Starting Terraform Apply for {self.request.id}")
            provider = self._get_provider()
            await provider.apply(request_id=self.request.id)
            add_fact(self.db, self.request.id, "terraform_apply_started", {}, actor="system")
            
        except Exception as e:
            logger.error(f"Apply failed: {e}")
            raise e
            
    @property
    def has_terraform_plan(self) -> bool:
        return has_fact(self.db, self.request.id, "terraform_plan_received")
        
    @property
    def has_terraform_apply_success(self) -> bool:
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="success")
    
    @property
    def has_terraform_apply_failed(self) -> bool:
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="failure")
