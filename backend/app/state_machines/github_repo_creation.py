"""
GitHub Repository Creation state machine.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)


class GithubRepoCreationStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    submit = pending.to(provisioning, cond="has_request_submitted")
    finish_provisioning = provisioning.to(completed, cond="has_repo_created")
    reject = pending.to(rejected, cond="has_request_rejected")
    
    # Approval node configuration
    APPROVAL_NODES = {}
    
    @property
    def has_repo_created(self) -> bool:
        """Check if GitHub repository has been created."""
        return has_fact(self.db, self.request.id, "repo_created")
    
    async def execute_tasks(self):
        """Execute repository creation tasks."""
        if self.current_state.id == "provisioning":
            if not self.has_repo_created:
                from app.tools.github import ScaffoldGitHubRepoTool
                from datetime import datetime
                
                # Mark provisioning as started if not already marked
                if not has_fact(self.db, self.request.id, "provisioning_started"):
                    add_fact(self.db, self.request.id, "provisioning_started", {"started_at": datetime.utcnow().isoformat()}, actor="system")
                    self.db.commit()
                
                logger.info(f"[{self.request.id}] Executing ScaffoldGitHubRepoTool...")
                tool = ScaffoldGitHubRepoTool()
                result = await tool.execute(**(self.request.state_context or {}))
                
                if result.get("status") == "completed":
                    add_fact(self.db, self.request.id, "repo_created", {
                        "repo_url": result.get("repo_url"),
                        "repo_name": result.get("repo_name")
                    }, actor="system")
                    self.db.commit()
                    logger.info(f"[{self.request.id}] Repository created successfully: {result.get('repo_url')}")

    def _process_current_state(self) -> bool:
        """
        Override to handle repo creation facts instead of workspace creation.
        """
        changed = super()._process_current_state()
        
        # Handle provisioning state - check if repo already exists
        if self.current_state.id == "provisioning":
            if has_fact(self.db, self.request.id, "repo_created"):
                # Repo already exists, mark provisioning as completed
                if not has_fact(self.db, self.request.id, "provisioning_completed"):
                    logger.info(f"Repository already exists for request {self.request.id}, marking complete")
                    add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
                    # Will reconcile to completed on next tick
        
        return changed
