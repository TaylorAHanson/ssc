"""
Service Principal state machine.
Uses Terraform GitOps provider.
"""
from typing import Dict, Any
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.providers.terraform.client import TerraformProvider
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
        super().__init__(request, db_session)
        
    def _get_provider(self):
        """Lazy load provider."""
        repo_url = settings.INFRA_REPO_URL
        if not repo_url:
            logger.warning("INFRA_REPO_URL not set.")
            
        return TerraformProvider(
            repo_url=repo_url,
            branch=settings.INFRA_REPO_BRANCH or "main",
            config={
                "git_username": settings.GIT_USERNAME,
                "git_email": settings.GIT_EMAIL,
                "ssh_key_path": settings.GIT_SSH_KEY_PATH
            }
        )

    async def execute_tasks(self):
        """Execute async tasks for the current state."""
        if self.current_state.id == "terraform_planning":
            await self._run_plan()
        elif self.current_state.id == "terraform_applying":
            await self._run_apply()
            
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
        return has_fact(self.db, self.request.id, "terraform_apply_received")
