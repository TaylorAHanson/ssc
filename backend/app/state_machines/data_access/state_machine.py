"""
Data Access state machine.
Handles requests for access to Unity Catalog assets (catalogs, schemas, tables, volumes).
Uses Databricks SDK to grant permissions via Unity Catalog Grants API.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.models.request import RequestStatus
from app.state_machines.facts import has_fact, add_fact
from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
import logging

logger = logging.getLogger(__name__)


@workflow(request_types=[RequestType.CATALOG_SCHEMA_TABLE_ACCESS, RequestType.BATCH_DATA_ACCESS, RequestType.DATA_ACCESS_REQUEST], feature_flag="core")
class DataAccessStateMachine(BaseRequestStateMachine):
    """
    State machine for requesting access to Unity Catalog assets
    (catalogs, schemas, tables, views, volumes).

    Flow:
        pending -> manager_approval -> data_owner_approval -> provisioning -> completed

    Both the requester's manager and the asset's data owner must approve
    before access is granted. Manager email is collected from the requester
    via the agent; data owner is auto-resolved by querying Unity Catalog.
    Access is provisioned by executing SQL GRANT statements through a
    serverless SQL warehouse.
    """

    # Override completion facts mapping for UI state tracking
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "data_owner_approval": "approval_received",
        "provisioning": "access_granted",  # Data access uses access_granted, not provisioning_completed
        "rejected": "request_rejected"
    }

    # Override log facts for UI display
    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
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
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    # Transitions
    # 1. Submit -> Data Owner Approval
    submit = pending.to(data_owner_approval, cond="has_request_submitted")

    # 2. Data Owner Approval -> Provisioning
    # Note: The transition source (data_owner_approval) already ensures we're in the right state
    approve_owner = data_owner_approval.to(provisioning, cond="has_data_owner_approval")

    # 3. Provisioning -> Completed
    finish_provisioning = provisioning.to(completed, cond="has_access_granted")

    # Rejection from any non-final state
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        data_owner_approval.to(rejected, cond="has_request_rejected") |
        provisioning.to(rejected, cond="has_request_rejected")
    )

    # Failure transitions
    mark_failed = (
        pending.to(failed) |
        data_owner_approval.to(failed) |
        provisioning.to(failed)
    )

    # Approval node configuration
    APPROVAL_NODES = {
        "data_owner_approval": {"approval_type": "data_owner", "name": "Data Owner Approval", "assignee_context_key": "data_owner_email", "assignee_role_key": "data_owner_role"}
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
    # State Entry Handlers (Sync and Async)
    # --------------------------------------------------------------------------

    def on_enter_data_owner_approval(self):
        """Execute sync tasks when entering data_owner_approval state."""
        # Create approval task
        self.create_approval_task("data_owner")

    async def on_enter_data_owner_approval_async(self):
        """Execute async tasks when entering data_owner_approval state."""
        # TODO: Get object owner after the design is complete
        logger.info(f"[{self.request.id}] on_enter_data_owner_approval_async CALLED")
        # Fetch data owner from Unity Catalog and store in context
        ctx = self.request.state_context or {}

        # Only fetch if not already set
        if not ctx.get("data_owners"):
            from app.core.config import settings
            from app.providers.databricks.client import DatabricksProvider

            # Support both single asset and multiple assets
            assets = ctx.get("assets", [])
            if not assets and ctx.get("asset_name"):
                assets = [{"asset_name": ctx.get("asset_name"), "asset_type": ctx.get("asset_type")}]

            if assets:
                try:
                    provider = DatabricksProvider(
                        host=settings.DATABRICKS_HOST,
                        token=settings.DATABRICKS_TOKEN,
                        client_id=settings.DATABRICKS_CLIENT_ID,
                        client_secret=settings.DATABRICKS_CLIENT_SECRET,
                        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
                    )

                    all_owners = set()
                    
                    for asset in assets:
                        asset_name = asset.get("asset_name")
                        asset_type = asset.get("asset_type")
                        
                        # If asset_type not provided, infer from asset_name format
                        if asset_name and not asset_type:
                            parts = asset_name.split(".")
                            if len(parts) == 1:
                                asset_type = "catalog"
                            elif len(parts) == 2:
                                asset_type = "schema"
                            elif len(parts) == 3:
                                asset_type = "table"
                                
                        if asset_name and asset_type:
                            logger.info(f"[{self.request.id}] Fetching approver_group tag for {asset_type} '{asset_name}'")
                            tags = await provider.get_asset_tags(asset_type, asset_name, ["approver_group"])
                            approver_group = tags.get("approver_group")
                            
                            if approver_group:
                                all_owners.add(approver_group)
                            else:
                                logger.warning(f"[{self.request.id}] Could not determine approver_group for {asset_name}, falling back to owner")
                                owner = await provider.get_asset_owner(asset_type, asset_name)
                                if owner:
                                    all_owners.add(owner)
                                else:
                                    logger.warning(f"[{self.request.id}] Could not determine owner for {asset_name} either")

                    owners = list(all_owners)
                    if owners:
                        logger.info(f"[{self.request.id}] Found unique data owner(s): {owners}")
                        ctx["data_owners"] = owners
                        self.request.state_context = ctx

                        # Force SQLAlchemy to detect JSON field changes
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(self.request, "state_context")
                        self.db.commit()
                        self.db.refresh(self.request)
                        logger.info(f"[{self.request.id}] Persisted data_owners to database")

                        # We need to create an approval row for each owner, replacing the single pending one
                        from app.db import ApprovalModel
                        
                        # Delete the placeholder approval row created by the sync hook
                        self.db.query(ApprovalModel).filter(
                            ApprovalModel.request_id == self.request.id,
                            ApprovalModel.approval_type == "data_owner",
                            ApprovalModel.status == "pending"
                        ).delete()
                        
                        # Create a new approval row for each owner
                        from datetime import datetime, timezone
                        for idx, owner in enumerate(owners):
                            assigned_to_email = owner if "@" in owner else None
                            assigned_to_role = owner if "@" not in owner else None
                            
                            new_approval = ApprovalModel(
                                id=f"app-{datetime.now(timezone.utc).timestamp()}-{idx}",
                                request_id=self.request.id,
                                approval_type="data_owner",
                                requested_by=ctx.get("requested_by", "system"),
                                requested_by_email=ctx.get("requested_by_email", ""),
                                assigned_to_email=assigned_to_email,
                                assigned_to_role=assigned_to_role,
                                status="pending",
                                created_at=datetime.now(timezone.utc)
                            )
                            self.db.add(new_approval)
                            
                            # Send notification to data owner (with idempotency check)
                            fact_key = f"data_owner_notified_{owner}"
                            if not has_fact(self.db, self.request.id, fact_key):
                                await self._send_data_owner_notification(owner)
                                add_fact(self.db, self.request.id, fact_key, {"owner": owner}, actor="system")
                                
                        self.db.commit()
                        logger.info(f"[{self.request.id}] Created {len(owners)} data_owner approval tasks")
                    else:
                        logger.warning(f"[{self.request.id}] Could not determine any data owners for the requested assets")

                except Exception as e:
                    logger.error(f"[{self.request.id}] Error fetching data owner: {str(e)}")

        # Notification is handled by base class approval creation

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
            
            assets = ctx.get("assets", [])
            if not assets and ctx.get("asset_name"):
                assets = [{"asset_name": ctx.get("asset_name"), "asset_type": ctx.get("asset_type")}]
                
            if len(assets) == 1:
                asset_desc = assets[0].get("asset_name", "the requested resource")
            elif len(assets) > 1:
                asset_desc = f"{len(assets)} requested resources"
            else:
                asset_desc = "the requested resource"
                
            access_level = ctx.get("access_level", "access")

            await self._send_notification(
                subject=f"Data Access Granted: {self.request.title}",
                body=f"Your data access request '{self.request.title}' has been successfully completed. "
                     f"You now have {access_level} access to {asset_desc}."
            )
            add_fact(self.db, self.request.id, "completed_notification_sent", {}, actor="system")

    # --------------------------------------------------------------------------
    # Provisioning Logic
    # --------------------------------------------------------------------------

    async def _grant_access(self):
        """Grant access to the Unity Catalog asset(s) using Databricks SDK."""
        # Idempotency check
        if has_fact(self.db, self.request.id, "access_grant_started"):
            logger.info(f"[{self.request.id}] Access grant already started, skipping")
            return

        # Extract parameters from request context
        ctx = self.request.state_context or {}
        access_level = ctx.get("access_level")  # read, write, manage
        principal = ctx.get("requested_by_email")  # User email to grant access to
        
        # Support both single asset and multiple assets
        assets = ctx.get("assets", [])
        if not assets and ctx.get("asset_name"):
            assets = [{"asset_name": ctx.get("asset_name"), "asset_type": ctx.get("asset_type")}]

        if not assets:
            raise PermanentError("No assets specified in request context")
        if not access_level:
            raise PermanentError("access_level is required (read, write, or manage)")
        if not principal:
            raise PermanentError("requested_by_email is required to grant access")

        # Record that we've started the grant process
        add_fact(self.db, self.request.id, "access_grant_started", {
            "assets": assets,
            "access_level": access_level,
            "principal": principal
        }, actor="system")

        provider = self._get_provider()
        results = []

        try:
            for asset in assets:
                asset_type = asset.get("asset_type")
                asset_name = asset.get("asset_name")
                
                # Validate required parameters for this asset
                if not asset_type:
                    raise PermanentError("asset_type is required (schema, table, view, or volume)")
                if asset_type.lower() == "catalog":
                    raise PermanentError(
                        "Catalog-level access is not permitted. Please request access to a "
                        "specific schema, table, view, or volume."
                    )
                if not asset_name:
                    raise PermanentError("asset_name is required")

                logger.info(f"[{self.request.id}] Granting {access_level} access to {asset_type} '{asset_name}' for {principal}")

                # Fetch the access_group tag
                tags = await provider.get_asset_tags(asset_type, asset_name, ["access_group"])
                access_group = tags.get("access_group")
                
                if access_group:
                    logger.info(f"[{self.request.id}] Found access_group tag: {access_group}. Adding {principal} to Entra ID group.")
                    
                    from app.providers.entra_id.client import EntraIdProvider
                    from app.core.config import settings
                    
                    entra_provider = EntraIdProvider(
                        tenant_id=getattr(settings, "ENTRA_ID_TENANT_ID", "mock-tenant-id"),
                        client_id=getattr(settings, "ENTRA_ID_CLIENT_ID", "mock-client-id"),
                        client_secret=getattr(settings, "ENTRA_ID_CLIENT_SECRET", "mock-client-secret")
                    )
                    
                    async with entra_provider:
                        # Find the user ID
                        user_search = await entra_provider.search_users(principal)
                        if not user_search.get("results"):
                            raise PermanentError(f"User {principal} not found in Entra ID")
                        user_id = user_search["results"][0]["id"]
                        
                        # Find the group ID
                        group_search = await entra_provider.search_groups(access_group)
                        if not group_search.get("results"):
                            raise PermanentError(f"Group {access_group} not found in Entra ID")
                        
                        # Find exact match just in case search returns multiple
                        group_id = None
                        for g in group_search["results"]:
                            if g["name"].lower() == access_group.lower():
                                group_id = g["id"]
                                break
                        
                        if not group_id:
                            group_id = group_search["results"][0]["id"]
                            
                        # Add user to group
                        await entra_provider.add_to_group(user_id=user_id, group_id=group_id)
                        
                    result = {"success": True, "granted_group": access_group, "asset_name": asset_name}
                else:
                    logger.warning(f"[{self.request.id}] No access_group tag found for {asset_name}. Falling back to direct grant.")
                    # Fallback to direct grant if tag is missing
                    result = await provider.grant_access(
                        asset_type=asset_type,
                        asset_name=asset_name,
                        principal=principal,
                        access_level=access_level
                    )
                    
                results.append({"asset_name": asset_name, "result": result})

            # Record success for all assets
            add_fact(self.db, self.request.id, "access_granted", {
                "assets": assets,
                "access_level": access_level,
                "principal": principal,
                "results": results
            }, actor="system")

            logger.info(f"[{self.request.id}] Successfully granted access to all requested assets")

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
    def has_data_owner_approval(self) -> bool:
        """Check if all data owners have approved the request."""
        # Get all data owner approval tasks for this request
        from app.db import ApprovalModel
        approval_tasks = self.db.query(ApprovalModel).filter(
            ApprovalModel.request_id == self.request.id,
            ApprovalModel.approval_type == "data_owner"
        ).all()
        
        if not approval_tasks:
            # If no tasks exist, we can't be approved
            return False
            
        # Check if all tasks are approved
        all_approved = all(task.status == "approved" for task in approval_tasks)
        if all_approved:
            logger.info(f"[{self.request.id}] DEBUG: has_data_owner_approval is TRUE (all {len(approval_tasks)} owners approved)")
        return all_approved

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
            "data_owner_approval": "Data Owner Approval",
            "provisioning": "Granting Access",
            "completed": "Access Granted",
            "rejected": "Rejected",
            "failed": "Failed"
        }
        return display_names.get(state_id, super()._get_state_display_name(state_id))

    async def _send_data_owner_notification(self, owner_email: str):
        """Send notification to data owner about pending approval request."""
        ctx = self.request.state_context or {}
        requester = ctx.get("requested_by", "Unknown")
        requester_email = ctx.get("requested_by_email", "")
        
        assets = ctx.get("assets", [])
        if not assets and ctx.get("asset_name"):
            assets = [{"asset_name": ctx.get("asset_name"), "asset_type": ctx.get("asset_type")}]
            
        if len(assets) == 1:
            asset_desc = f"{assets[0].get('asset_type', 'asset')} '{assets[0].get('asset_name', 'Unknown')}'"
            subject = f"Data Access Approval Required: {assets[0].get('asset_name', 'Unknown')}"
        elif len(assets) > 1:
            asset_desc = f"multiple assets ({len(assets)} items)"
            subject = f"Data Access Approval Required: Multiple Assets"
        else:
            asset_desc = "Unknown asset"
            subject = "Data Access Approval Required"
            
        access_level = ctx.get("access_level", "access")
        justification = ctx.get("justification", "No justification provided")

        body = f"""Hello,

A data access request requires your approval as the owner of {asset_desc}.

Request Details:
- Requested by: {requester} ({requester_email})
- Assets: {', '.join(a.get('asset_name', 'Unknown') for a in assets)}
- Access Level: {access_level}
- Justification: {justification}

Please review and approve or reject this request in the ATLAS portal.

Request ID: {self.request.id}
"""

        logger.info(f"[{self.request.id}] Sending approval notification to data owner: {owner_email}")
        await self._send_notification(subject=subject, body=body, to_email=owner_email)
