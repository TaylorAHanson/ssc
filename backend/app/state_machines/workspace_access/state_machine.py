"""
Workspace Access state machine.
"""
import re
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.databricks_job_step import DatabricksJobStepMixin


@workflow(request_types=RequestType.WORKSPACE_ACCESS, feature_flag="core")
class WorkspaceAccessStateMachine(DatabricksJobStepMixin, BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    submit_domain = pending.to(manager_approval, cond=["has_request_submitted", "requires_manager_approval"])
    submit_enterprise = pending.to(provisioning, cond=["has_request_submitted", "is_auto_approve"])
    approve_manager = manager_approval.to(provisioning, cond="has_manager_approval")
    finish_provisioning = provisioning.to(completed, cond="has_access_granted")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval", "assignee_context_key": "manager_email"}
    }

    STATE_COMPLETION_FACTS = {
        **BaseRequestStateMachine.STATE_COMPLETION_FACTS,
        "provisioning": "access_granted",
    }

    STATE_LOG_FACTS = {
        **BaseRequestStateMachine.STATE_LOG_FACTS,
        "provisioning": ["access_granted"],
    }

    @property
    def is_auto_approve(self) -> bool:
        """Check if the workspace access request is auto-approved (Enterprise Prod)."""
        ctx = self.request.state_context or {}
        ws_type = ctx.get("workspace_type", "").lower()
        env = ctx.get("environment", "").lower()
        return ws_type == "enterprise" and env == "prd"

    @property
    def requires_manager_approval(self) -> bool:
        """Check if the workspace access request requires manager approval."""
        return not self.is_auto_approve

    @property
    def has_access_granted(self) -> bool:
        """Check if access has been granted."""
        from app.state_machines.facts import has_fact
        return has_fact(self.db, self.request.id, "access_granted")

    @staticmethod
    def _lmws_step_id(list_name: str) -> str:
        """Stable, fact-safe step id for the LMWS membership add."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", list_name).strip("_").lower()
        return f"lmws_add_{slug}"

    async def on_enter_manager_approval_async(self):
        """Resolve the approver (manager) for the request.

        NOTE: Manager / org-hierarchy lookup is an Entra ID / Graph capability
        that LMWS does not provide (LMWS manages list membership, not the HR
        reporting tree). Until a dedicated manager-resolution source is wired
        in, the manager email is taken from context if present, otherwise
        derived as a placeholder. This is intentionally NOT an LMWS call.
        """
        import logging
        logger = logging.getLogger(__name__)

        ctx = self.request.state_context or {}
        user_email = ctx.get("requested_by_email")

        if not user_email:
            logger.warning(f"[{self.request.id}] No requested_by_email in context. Cannot resolve manager.")
            return

        # Prefer an explicitly supplied manager; otherwise fall back to a placeholder.
        manager_email = ctx.get("manager_email") or f"manager-of-{user_email.split('@')[0]}@example.com"
        logger.info(f"[{self.request.id}] Manager approver resolved to: {manager_email}")

        ctx["manager_email"] = manager_email
        self.request.state_context = ctx
        self.db.commit()

        # Update the pending approval task with the manager's email
        from app.db import ApprovalModel
        pending_approval = self.db.query(ApprovalModel).filter(
            ApprovalModel.request_id == self.request.id,
            ApprovalModel.approval_type == "manager",
            ApprovalModel.status == "pending"
        ).first()
        if pending_approval:
            pending_approval.assigned_to_email = manager_email
            self.db.commit()

    async def on_enter_provisioning_async(self):
        """Grant workspace access by adding the user to the target LMWS list.

        Runs as a Databricks job step (classic compute) via
        ``DatabricksJobStepMixin``. Re-entrant: this hook re-runs each poller
        tick while in ``provisioning`` — the mixin submits once then polls until
        the LMWS ``list_members_add`` job completes, at which point we record
        ``access_granted`` to advance to ``completed``.
        """
        import logging
        from datetime import datetime, timezone
        from app.state_machines.facts import add_fact
        from app.core.exceptions import PermanentError
        from app.providers.lmws import LmwsProvider, LmwsAction
        logger = logging.getLogger(__name__)

        ctx = self.request.state_context or {}
        workspace_id = ctx.get("workspace_id")
        workspace_name = ctx.get("workspace_name")
        user_email = ctx.get("requested_by_email")

        if not user_email:
            raise PermanentError("requested_by_email is required to grant workspace access")

        # Target LMWS list for this workspace. Prefer an explicit mapping;
        # fall back to a naming convention keyed on the workspace name.
        target_list = ctx.get("target_group") or ctx.get("target_group_id") or f"group-for-{workspace_name}"
        step_id = self._lmws_step_id(target_list)

        logger.info(
            f"[{self.request.id}] LMWS add {user_email} -> list '{target_list}' "
            f"for workspace {workspace_id} (step {step_id})"
        )

        provider = LmwsProvider()
        await self.run_databricks_job_step(
            **provider.build_step_kwargs(
                LmwsAction.LIST_MEMBERS_ADD,
                step_id=step_id,
                list_name=target_list,
                members=[user_email],
                justification=ctx.get("justification") or f"Workspace access: {workspace_name}",
                run_name=f"LMWS add {user_email} -> {target_list}: {self.request.id}",
            )
        )

        if self.step_failed(step_id):
            error = self.get_step_error(step_id)
            logger.error(f"[{self.request.id}] LMWS membership failed: {error}")
            add_fact(self.db, self.request.id, "access_grant_failed", {
                "list_name": target_list,
                "error": error,
            }, actor="system")
            raise PermanentError(f"Failed to add {user_email} to LMWS list {target_list}: {error}")

        if not self.step_completed(step_id):
            return  # job still running; next tick will poll

        add_fact(self.db, self.request.id, "access_granted", {
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "user_email": user_email,
            "lmws_list": target_list,
            "granted_at": datetime.now(timezone.utc).isoformat(),
        }, actor="system")
        self.db.commit()
