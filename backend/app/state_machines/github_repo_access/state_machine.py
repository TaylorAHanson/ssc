"""
GitHub Repo Access state machine.
Grants a user access to a GitHub repository.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
import logging

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.GITHUB_REPO_ACCESS, feature_flag="core")
class GithubRepoAccessStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(manager_approval, cond="has_request_submitted")
    approve_manager = manager_approval.to(data_owner_approval, cond="has_manager_approval")
    approve_owner = data_owner_approval.to(provisioning, cond="has_data_owner_approval")
    finish_provisioning = provisioning.to(completed, cond="has_provisioning_completed")
    
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        data_owner_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )
    
    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        data_owner_approval.to(failed) |
        provisioning.to(failed)
    )

    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "data_owner_approval": {"approval_type": "data_owner", "name": "Repo Owner Approval", "assignee_context_key": "repo_owner_email"}
    }
    
    @property
    def has_data_owner_approval(self) -> bool:
        return has_fact(self.db, self.request.id, "approval_received", approval_type="data_owner")
        
    @property
    def has_provisioning_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "provisioning_completed")

    async def on_enter_data_owner_approval_async(self):
        """Find the repo owner and assign the approval task."""
        ctx = self.request.state_context or {}
        repo_name = ctx.get("repo_name")
        
        if not ctx.get("repo_owner_email"):
            # TODO: Query GitHub API to find repo admins/owners
            # For now, fallback to a default or platform admin
            owner_email = settings.PLATFORM_ADMIN_EMAIL or "admin@example.com"
            
            ctx["repo_owner_email"] = owner_email
            self.request.state_context = ctx
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(self.request, "state_context")
            self.db.commit()
            
            # Backfill the approval row
            from app.db import ApprovalModel
            approval_row = (
                self.db.query(ApprovalModel)
                .filter(
                    ApprovalModel.request_id == self.request.id,
                    ApprovalModel.approval_type == "data_owner",
                    ApprovalModel.status == "pending",
                )
                .first()
            )
            if approval_row and not approval_row.assigned_to_email:
                approval_row.assigned_to_email = owner_email
                self.db.commit()

    async def on_enter_provisioning_async(self):
        if has_fact(self.db, self.request.id, "provisioning_started"):
            return
            
        try:
            add_fact(self.db, self.request.id, "provisioning_started", {}, actor="system")
            self.db.commit()
            
            ctx = self.request.state_context or {}
            repo_name = ctx.get("repo_name")
            github_username = ctx.get("github_username")
            permission = ctx.get("permission", "push")
            
            if not repo_name or not github_username:
                raise PermanentError("repo_name and github_username are required")
                
            from app.providers.github.client import GitHubProvider
            
            async with GitHubProvider(
                token=settings.GITHUB_TOKEN or settings.get_git_token(),
                org=settings.GITHUB_ORG
            ) as github:
                logger.info(f"[{self.request.id}] Granting {permission} access to {github_username} on {repo_name}")
                await github.set_permissions(repo_name, github_username, permission)
            
            add_fact(self.db, self.request.id, "provisioning_completed", {
                "repo_name": repo_name,
                "github_username": github_username,
                "permission": permission
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] GitHub access provisioning failed: {e}")
            raise RetryableError(f"Failed to grant GitHub access: {e}")
