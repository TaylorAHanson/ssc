"""
GitHub Repository Creation state machine.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact
import logging

logger = logging.getLogger(__name__)


@workflow(request_types=RequestType.GITHUB_REPO_CREATION, feature_flag="core")
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
    
    # NOTE: By not defining facts, we can rely on the base class to handle them

    @property
    def has_repo_created(self) -> bool:
        """Check if GitHub repository has been created."""
        return has_fact(self.db, self.request.id, "repo_created")
    
    async def on_enter_provisioning_async(self):
        """Execute repository creation tasks."""
        if has_fact(self.db, self.request.id, "repo_created"):
            return

        from app.providers.github.client import GitHubProvider
        from app.core.config import settings
        from datetime import datetime, timezone
        import traceback
        
        # Mark provisioning as started if not already marked
        if not has_fact(self.db, self.request.id, "provisioning_started"):
            add_fact(self.db, self.request.id, "provisioning_started", {"started_at": datetime.now(timezone.utc).isoformat()}, actor="system")
            self.db.commit()
        
        logger.info(f"[{self.request.id}] Provisioning GitHub repository...")
        
        try:
            ctx = self.request.state_context or {}
            repo_name = ctx.get("repo_name")
            description = ctx.get("description", "")
            visibility = ctx.get("visibility", "private")
            template = ctx.get("template")
            
            if not repo_name:
                raise ValueError("repo_name is required in state_context")

            # Use GitHubProvider directly
            async with GitHubProvider(
                token=settings.GITHUB_TOKEN or settings.get_git_token(),
                org=settings.GITHUB_ORG
            ) as github:
                
                config = {
                    "description": description,
                    "private": visibility == "private"
                }
                
                if template and template.lower() != "none":
                    logger.info(f"[{self.request.id}] Creating repo '{repo_name}' from template '{template}'")
                    result = await github.create_from_template(template, repo_name, config)
                else:
                    logger.info(f"[{self.request.id}] Creating blank repo '{repo_name}'")
                    result = await github.create_repo(repo_name, config)
                
                if result:
                    add_fact(self.db, self.request.id, "repo_created", {
                        "repo_url": result.get("html_url"),
                        "repo_name": repo_name,
                        "full_name": result.get("full_name")
                    }, actor="system")
                    
                    add_fact(self.db, self.request.id, "provisioning_completed", {
                        "completed_at": datetime.now(timezone.utc).isoformat()
                    }, actor="system")
                    
                    logger.info(f"[{self.request.id}] Repository created successfully: {result.get('html_url')}")
        
        except Exception as e:
            logger.error(f"[{self.request.id}] GitHub provisioning failed: {str(e)}")
            logger.error(traceback.format_exc())
            add_fact(self.db, self.request.id, "provisioning_failed", {
                "error": str(e),
                "failed_at": datetime.now(timezone.utc).isoformat()
            }, actor="system")
            raise e
