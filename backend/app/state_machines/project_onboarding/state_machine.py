"""
Project Onboarding compound state machine.
Orchestrates Workspace Provisioning and GitHub Repo Creation.
"""
from statemachine import State
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
from app.models.request import RequestType
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.PROJECT_ONBOARDING, feature_flag="core")
class ProjectOnboardingStateMachine(BaseRequestStateMachine):
    
    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    
    # Transitions
    submit = pending.to(manager_approval, cond="has_request_submitted")
    
    # After manager approval, we go straight to provisioning (onboarding always implies this)
    approve_manager = manager_approval.to(provisioning, cond="has_manager_approval")
    
    # We complete when ALL child requests are done
    finish_provisioning = provisioning.to(completed, cond="all_children_completed")
    
    # Rejection
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )
    
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"}
    }
    
    async def on_enter_provisioning_async(self):
        """
        Handle spawning of child workflows when entering provisioning.
        """
        # Check if we've already kicked off the child requests
        if not has_fact(self.db, self.request.id, "children_spawned"):
            logger.info(f"[{self.request.id}] Onboarding entering provisioning - spawning child tasks")
            
            state_context = self.request.state_context or {}
            project_name = state_context.get("project_name")
            
            # 1. Spawn Workspace Provisioning
            workspace_payload = {
                "workspace_name": f"{project_name}-workspace",
                "environment": state_context.get("environment", "dev"),
                "cost_center": state_context.get("cost_center")
            }
            self.spawn_child_request(
                request_type=RequestType.WORKSPACE_PROVISION,
                payload=workspace_payload,
                title=f"Workspace for Project: {project_name}"
            )
            
            # 2. Spawn GitHub Repo Creation
            repo_payload = {
                "repo_name": state_context.get("repo_name") or f"{project_name}-repo",
                "project_name": project_name
            }
            self.spawn_child_request(
                request_type=RequestType.GITHUB_REPO_CREATION,
                payload=repo_payload,
                title=f"GitHub Repo for Project: {project_name}"
            )
            
            # Record that we spawned children
            add_fact(self.db, self.request.id, "children_spawned", {
                "timestamp": datetime.utcnow().isoformat(),
                "child_types": ["workspace_provision", "github_repo_creation"]
            }, actor="system")
            self.db.commit()
            
        # Note: We don't need to do anything else here. 
        # The poller will pick up the child requests, and tick() will
        # move parent to completed once all_children_completed is true.
