"""
Workspace Provision state machine.
Uses Terraform GitOps provider.
"""
from typing import Dict, Any, List
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import has_fact, add_fact, get_latest_fact
from app.providers.terraform.client import TerraformProvider
from app.providers.terraform.volume_provider import VolumeGitOpsProvider
from app.core.config import settings
from app.core.exceptions import PermanentError
import logging

logger = logging.getLogger(__name__)


class WorkspaceProvisionStateMachine(BaseRequestStateMachine):

    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    training_pending = State("training_pending")

    # GitOps States
    terraform_planning = State("terraform_planning")
    awaiting_admin_approval = State("awaiting_admin_approval")

    # Transient state: entered when a platform admin edits parameters instead of approving.
    # The fact log records this transition as an immutable boundary. The state machine
    # immediately rebounds to terraform_planning to run a fresh plan with new parameters.
    parameters_updated = State("parameters_updated")

    terraform_applying = State("terraform_applying")

    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    # Transitions
    submit = pending.to(manager_approval, cond="has_request_submitted")

    # Manager Approval
    approve_manager = (
        manager_approval.to(training_pending, cond="has_manager_approval and requires_training") |
        manager_approval.to(terraform_planning, cond="has_manager_approval and not requires_training")
    )

    complete_training = training_pending.to(terraform_planning, cond="has_training_completed")

    # GitOps Flow
    finish_planning = terraform_planning.to(awaiting_admin_approval, cond="has_current_terraform_plan")

    # Admin edits parameters instead of approving: rebound through parameters_updated.
    # Triggered when a parameters_edited fact exists AND no subsequent platform_admin
    # approval has been recorded (i.e., the edit hasn't been actioned yet).
    edit_and_restart = awaiting_admin_approval.to(parameters_updated, cond="has_parameters_edited")

    # Immediate rebound from parameters_updated → terraform_planning
    relaunch_planning = parameters_updated.to(terraform_planning)

    approve_admin = awaiting_admin_approval.to(terraform_applying, cond="has_platform_admin_approval")
    finish_applying = terraform_applying.to(completed, cond="has_terraform_apply_success")

    # Apply can fail
    apply_failed = terraform_applying.to(failed, cond="has_terraform_apply_failed")

    # Rejection
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        training_pending.to(rejected, cond="has_request_rejected") |
        terraform_planning.to(rejected, cond="has_request_rejected") |
        awaiting_admin_approval.to(rejected, cond="has_request_rejected") |
        parameters_updated.to(rejected, cond="has_request_rejected") |
        terraform_applying.to(rejected, cond="has_request_rejected")
    )

    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        training_pending.to(failed) |
        terraform_planning.to(failed) |
        awaiting_admin_approval.to(failed) |
        parameters_updated.to(failed) |
        terraform_applying.to(failed)
    )

    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "awaiting_admin_approval": {
            "approval_type": "platform_admin",
            "name": "Platform Admin Approval (Review Plan)"
        }
    }

    def __init__(self, request, db_session):
        # Handle legacy states
        if request.current_state == "provisioning":
            request.current_state = "terraform_planning"
        super().__init__(request, db_session)

    def get_editable_states(self) -> List[str]:
        """States from which a platform_admin can edit parameters and restart.

        For WorkspaceProvision, editing is only meaningful at the admin approval gate,
        because that is where the Terraform plan is reviewed before execution.
        """
        return ["awaiting_admin_approval"]

    def on_enter_parameters_updated(self):
        """Synchronous hook: immediately rebound to terraform_planning.

        This state is transient — it exists solely to create an auditable
        state transition in the fact log. The rebound is synchronous so the
        poller sees terraform_planning on the next tick.
        """
        logger.info(
            f"[{self.request.id}] parameters_updated entered — "
            "rebounding to terraform_planning for fresh plan"
        )
        self.relaunch_planning()

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
        await self._send_notification(
            subject=f"Workspace Request Approved: {self.request.title}",
            body=f"Your request for workspace '{self.request.title}' has been approved. Provisioning changes now..."
        )
        await self._run_apply()

    async def on_enter_completed_async(self):
        """Execute async tasks for completed state."""
        await self._send_notification(
            subject=f"Workspace Created: {self.request.title}",
            body=f"Your workspace '{self.request.title}' has been successfully created and is ready for use."
        )

    async def _run_plan(self):
        """Trigger Terraform Plan — run-aware.

        Uses the `parameters_edited` fact as a temporal boundary. A prior
        `terraform_plan_started` fact is only treated as valid for the current
        run if it was recorded AFTER the latest `parameters_edited` fact.
        This means old execution facts are preserved in history but never
        block a fresh plan when parameters have been updated.
        """
        latest_edit = get_latest_fact(self.db, self.request.id, "parameters_edited")
        plan_started = get_latest_fact(self.db, self.request.id, "terraform_plan_started")

        if plan_started:
            if latest_edit is None or plan_started.created_at > latest_edit.created_at:
                logger.info(f"[{self.request.id}] Terraform plan already started for current run, skipping.")
                return
            else:
                logger.info(
                    f"[{self.request.id}] Prior plan predates parameter edit — "
                    "running fresh plan with updated parameters."
                )

        params = self.request.state_context or {}
        name = params.get("workspace_name")
        if not name and ":" in self.request.title:
            name = self.request.title.split(":")[-1].strip()
        if not name:
            name = self.request.title

        try:
            logger.info(f"Starting Workspace Plan for {name} ({self.request.id})")
            provider = self._get_provider()

            content = {
                "resource_type": "workspace",
                "name": name,
                "properties": params
            }
            target_file = f"resources/workspaces/{name}.yaml"

            await provider.plan(
                request_id=self.request.id,
                target_file=target_file,
                content=content,
                commit_message=f"Plan: Workspace {name}"
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

    # --------------------------------------------------------------------------
    # Fact Properties
    # --------------------------------------------------------------------------

    @property
    def has_current_terraform_plan(self) -> bool:
        """True if a terraform_plan_received fact exists for the CURRENT run.

        A plan is considered current if it was received AFTER the latest
        parameters_edited fact (or if no edits have ever occurred).
        """
        latest_edit = get_latest_fact(self.db, self.request.id, "parameters_edited")
        plan_received = get_latest_fact(self.db, self.request.id, "terraform_plan_received")
        if not plan_received:
            return False
        if latest_edit and plan_received.created_at <= latest_edit.created_at:
            return False  # Plan predates the most recent edit — stale
        return True

    @property
    def has_parameters_edited(self) -> bool:
        """True when a parameters_edited fact is newer than any subsequent platform_admin approval.

        This drives the edit_and_restart transition. Once the admin approves the
        new plan, this returns False so the SM does not re-enter parameters_updated.
        """
        latest_edit = get_latest_fact(self.db, self.request.id, "parameters_edited")
        if not latest_edit:
            return False
        # If the admin already approved after the edit, the edit has been actioned
        platform_approval = get_latest_fact(
            self.db, self.request.id, "approval_received", approval_type="platform_admin"
        )
        if platform_approval and platform_approval.created_at > latest_edit.created_at:
            return False
        return True

    @property
    def has_terraform_plan(self) -> bool:
        """Legacy alias — prefer has_current_terraform_plan for new logic."""
        return has_fact(self.db, self.request.id, "terraform_plan_received")

    @property
    def has_terraform_apply_success(self) -> bool:
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="success")

    @property
    def has_terraform_apply_failed(self) -> bool:
        return has_fact(self.db, self.request.id, "terraform_apply_received", status="failure")
