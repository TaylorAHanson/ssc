"""
Data Access state machine.
Handles requests for access to Unity Catalog assets (catalogs, schemas, tables, volumes).
Uses Databricks SDK to grant permissions via Unity Catalog Grants API.
"""
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.models.request import RequestStatus
from app.state_machines.facts import has_fact, add_fact
from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
import logging

logger = logging.getLogger(__name__)


class DataAccessStateMachine(BaseRequestStateMachine):
    """
    State machine for requesting access to Unity Catalog assets.

    Flow:
        pending -> data_owner_approval -> provisioning -> completed

    The data owner must approve the request before access is granted.
    Access is provisioned using the Databricks Unity Catalog Grants API.

    NOTE: Manager approval is commented out for now. To enable it, uncomment
    the manager_approval state and related transitions below.
    """

    # Override completion facts mapping for UI state tracking
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "manager_approval": "approval_received",
        "data_owner_approval": "approval_received",
        "provisioning": "access_granted",  # Data access uses access_granted, not provisioning_completed
        "rejected": "request_rejected"
    }

    # Override log facts for UI display
    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "manager_approval": ["approval_received"],
        "data_owner_approval": ["approval_received"],
        "provisioning": ["access_grant_started", "access_granted", "access_grant_failed"],
        "rejected": ["request_rejected"]
    }
    
    # Override status mapping for data owner approval
    STATUS_MAPPING = {
        **BaseRequestStateMachine.STATUS_MAPPING,
        "data_owner_approval": RequestStatus.DATA_OWNER_APPROVAL
    }

    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    # Transitions
    # 1. Submit -> Manager Approval
    submit = pending.to(manager_approval, cond="has_request_submitted")

    # 2. Manager Approval -> Data Owner Approval
    # Note: The transition source (manager_approval) already ensures we're in the right state
    approve_manager = manager_approval.to(data_owner_approval, cond="has_manager_approval")

    # 3. Data Owner Approval -> Provisioning
    # Note: The transition source (data_owner_approval) already ensures we're in the right state
    approve_owner = data_owner_approval.to(provisioning, cond="has_data_owner_approval")

    # 4. Provisioning -> Completed
    finish_provisioning = provisioning.to(completed, cond="has_access_granted")

    # Rejection from any non-final state
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        manager_approval.to(rejected, cond="has_request_rejected") |
        data_owner_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )

    # Failure transitions
    mark_failed = (
        pending.to(failed) |
        manager_approval.to(failed) |
        data_owner_approval.to(failed) |
        provisioning.to(failed)
    )

    # Approval node configuration
    APPROVAL_NODES = {
        "manager_approval": {"approval_type": "manager", "name": "Manager Approval"},
        "data_owner_approval": {"approval_type": "data_owner", "name": "Data Owner Approval"}
    }

    # --------------------------------------------------------------------------
    # Provider
    # --------------------------------------------------------------------------

    def _get_provider(self):
        """Lazy load Databricks provider."""
        from app.providers.databricks.client import DatabricksProvider

        host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
        if not host:
            raise PermanentError("DATABRICKS_HOST is required for data access provisioning")

        return DatabricksProvider(
            host=host,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

    # --------------------------------------------------------------------------
    # State Entry Handlers (Async)
    # --------------------------------------------------------------------------

    async def on_enter_manager_approval_async(self):
        """Execute async tasks when entering manager_approval state."""
        # Notification is handled by base class approval creation
        pass

    async def on_enter_data_owner_approval_async(self):
        """Execute async tasks when entering data_owner_approval state."""
        # Notification is handled by base class approval creation
        pass

    async def on_enter_provisioning_async(self):
        """Execute async tasks for provisioning state."""
        # Notify user: Approved, provisioning access (with idempotency check)
        if not has_fact(self.db, self.request.id, "provisioning_notification_sent"):
            await self._send_notification(
                subject=f"Data Access Request Approved: {self.request.title}",
                body=f"Your data access request '{self.request.title}' has been approved by the data owner. Access is being provisioned."
            )
            add_fact(self.db, self.request.id, "provisioning_notification_sent", {}, actor="system")
        # Run the actual provisioning
        await self._grant_access()

    async def on_enter_completed_async(self):
        """Execute async tasks for completed state."""
        # Notify user: Success (with idempotency check)
        if not has_fact(self.db, self.request.id, "completed_notification_sent"):
            ctx = self.request.state_context or {}
            asset_name = ctx.get("asset_name", "the requested resource")
            access_level = ctx.get("access_level", "access")

            await self._send_notification(
                subject=f"Data Access Granted: {self.request.title}",
                body=f"Your data access request '{self.request.title}' has been successfully completed. "
                     f"You now have {access_level} access to {asset_name}."
            )
            add_fact(self.db, self.request.id, "completed_notification_sent", {}, actor="system")

    # --------------------------------------------------------------------------
    # Provisioning Logic
    # --------------------------------------------------------------------------

    async def _grant_access(self):
        """Grant access to the Unity Catalog asset using Databricks SDK."""
        # Idempotency check
        if has_fact(self.db, self.request.id, "access_grant_started"):
            logger.info(f"[{self.request.id}] Access grant already started, skipping")
            return

        # Extract parameters from request context
        ctx = self.request.state_context or {}
        asset_type = ctx.get("asset_type")  # catalog, schema, table, volume
        asset_name = ctx.get("asset_name")  # e.g., "my_catalog.my_schema.my_table"
        access_level = ctx.get("access_level")  # read, write, manage
        principal = ctx.get("requested_by_email")  # User email to grant access to

        # Validate required parameters
        if not asset_type:
            raise PermanentError("asset_type is required (catalog, schema, table, or volume)")
        if not asset_name:
            raise PermanentError("asset_name is required")
        if not access_level:
            raise PermanentError("access_level is required (read, write, or manage)")
        if not principal:
            raise PermanentError("requested_by_email is required to grant access")

        try:
            logger.info(f"[{self.request.id}] Granting {access_level} access to {asset_type} '{asset_name}' for {principal}")

            # Record that we've started the grant process
            add_fact(self.db, self.request.id, "access_grant_started", {
                "asset_type": asset_type,
                "asset_name": asset_name,
                "access_level": access_level,
                "principal": principal
            }, actor="system")

            # Get provider and grant access
            provider = self._get_provider()
            result = await provider.grant_access(
                asset_type=asset_type,
                asset_name=asset_name,
                principal=principal,
                access_level=access_level
            )

            # Record success
            add_fact(self.db, self.request.id, "access_granted", {
                "asset_type": asset_type,
                "asset_name": asset_name,
                "access_level": access_level,
                "principal": principal,
                "result": result
            }, actor="system")

            logger.info(f"[{self.request.id}] Successfully granted access to {asset_name}")

        except PermanentError as e:
            logger.error(f"[{self.request.id}] Permanent error granting access: {e}")
            add_fact(self.db, self.request.id, "access_grant_failed", {
                "error": str(e),
                "permanent": True
            }, actor="system")
            raise

        except Exception as e:
            logger.error(f"[{self.request.id}] Error granting access: {e}")
            add_fact(self.db, self.request.id, "access_grant_failed", {
                "error": str(e),
                "permanent": False
            }, actor="system")
            raise RetryableError(f"Failed to grant access: {e}")

    # --------------------------------------------------------------------------
    # Fact Properties (Used in transitions)
    # --------------------------------------------------------------------------

    @property
    def has_manager_approval(self) -> bool:
        """Check if manager has approved the request."""
        res = has_fact(self.db, self.request.id, "approval_received", approval_type="manager")
        if res:
            logger.info(f"[{self.request.id}] DEBUG: has_manager_approval is TRUE")
        return res

    @property
    def has_access_granted(self) -> bool:
        """Check if access has been successfully granted."""
        return has_fact(self.db, self.request.id, "access_granted")

    # --------------------------------------------------------------------------
    # UI Display Overrides
    # --------------------------------------------------------------------------

    def _get_state_display_name(self, state_id: str) -> str:
        """Override display names for data access-specific states."""
        display_names = {
            "pending": "Created",
            "manager_approval": "Manager Approval",
            "data_owner_approval": "Data Owner Approval",
            "provisioning": "Granting Access",
            "completed": "Access Granted",
            "rejected": "Rejected",
            "failed": "Failed"
        }
        return display_names.get(state_id, super()._get_state_display_name(state_id))
