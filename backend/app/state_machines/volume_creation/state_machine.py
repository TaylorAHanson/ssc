"""
Volume Creation state machine.
Uses Terraform GitOps provider.
"""
from typing import Dict, Any
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.providers.terraform.client import TerraformProvider
from app.providers.terraform.volume_provider import VolumeGitOpsProvider
from app.core.config import settings
from app.core.exceptions import PermanentError
import logging

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.VOLUME_CREATION, feature_flag="core")
class VolumeCreationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    terraform_planning = State("terraform_planning")
    awaiting_approval = State("awaiting_approval")
    terraform_applying = State("terraform_applying")
    
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(terraform_planning, cond="has_manager_approval")
    finish_planning = terraform_planning.to(awaiting_approval, cond="has_terraform_plan")
    approve_admin = awaiting_approval.to(terraform_applying, cond="has_platform_admin_approval")
    finish_applying = terraform_applying.to(completed, cond="has_terraform_apply_success")
    apply_failed = terraform_applying.to(failed, cond="has_terraform_apply_failed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        terraform_planning.to(rejected, cond="has_request_rejected") |
        awaiting_approval.to(rejected, cond="has_request_rejected") |
        terraform_applying.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) | 
        manager_approval.to(failed) |
        terraform_planning.to(failed) | 
        awaiting_approval.to(failed) | 
        terraform_applying.to(failed)
    )

    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "awaiting_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval (Review Plan)"}
    }

    def _get_provider(self):
        gitops_mode = settings.GITOPS_MODE or "volume"
        if gitops_mode == "volume":
            volume_path = settings.GITOPS_VOLUME_PATH
            if not volume_path:
                raise PermanentError("GITOPS_VOLUME_PATH not set for volume mode.")
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
        await self._run_plan()
        
    async def on_enter_terraform_applying_async(self):
        await self._send_notification(
            subject=f"Volume Request Approved: {self.request.title}",
            body=f"Your request '{self.request.title}' has been approved. Applying changes now..."
        )
        await self._run_apply()
            
    async def on_enter_completed_async(self):
        await self._send_notification(
            subject=f"Volume Created: {self.request.title}",
            body=f"Your request '{self.request.title}' has been successfully completed."
        )
            
    async def _run_plan(self):
        if has_fact(self.db, self.request.id, "terraform_plan_started"):
             return

        params = self.request.state_context or {}
        name = params.get("name") or params.get("volume_name")
        catalog = params.get("catalog")
        schema = params.get("schema")
        environment = params.get("environment", settings.DEFAULT_ENVIRONMENT or "dev")
        
        if not name or not catalog or not schema:
             raise PermanentError("Volume name, catalog, and schema are required")

        try:
            logger.info(f"Starting Terraform Plan for Volume {name} ({self.request.id})")
            provider = self._get_provider()
            
            content = {
                "resource_type": "volume",
                "name": name,
                "properties": {
                    "catalog": catalog,
                    "schema": schema,
                    "comment": params.get("comment", f"Created via ATLAS request"),
                    "volume_type": params.get("volume_type", "MANAGED")
                }
            }
            
            target_file = f"envs/{environment}/resources/{name}.yaml"
            
            await provider.plan(
                request_id=self.request.id,
                target_file=target_file,
                content=content,
                commit_message=f"Plan: Volume {name} in {catalog}.{schema}"
            )
            
            add_fact(self.db, self.request.id, "terraform_plan_started", {}, actor="system")
            
        except Exception as e:
            logger.error(f"Plan failed: {e}")
            raise e

    async def _run_apply(self):
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
