"""
Data Certification State Machine.
Proactive workflow where an AI auto-generates a Data Contract,
Governance Admins review it, and SMEs finalize it.
"""

import logging
from statemachine import State

from app.core.config import settings
from app.models.request import RequestType, RequestStatus
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import add_fact
from app.providers.databricks.handlers import DatasetResourceHandler
from app.providers.databricks.client import DatabricksProvider

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.DATA_CERTIFICATION, feature_flag="governance")
class DataCertificationStateMachine(BaseRequestStateMachine):
    """
    State machine for human-in-the-loop Data Certification review.
    Assumes the AI auto-generation of the ODCS has already been performed by the Enforcement Sentinel
    or an external trigger before instantiating this state machine.
    """

    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "admin_review": "admin_approved",
        "sme_review": "sme_approved",
        "completed": "certification_applied",
        "rejected": "request_rejected"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "admin_review": ["admin_approved", "request_rejected"],
        "sme_review": ["sme_approved", "request_rejected"],
        "completed": ["certification_applied"],
        "rejected": ["request_rejected"]
    }

    pending = State("pending", initial=True)
    admin_review = State("admin_review")
    sme_review = State("sme_review")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    # Transitions
    start_review = pending.to(admin_review, cond="has_request_submitted")
    
    admin_approve = admin_review.to(sme_review, cond="has_admin_approved")
    admin_reject = admin_review.to(rejected, cond="has_request_rejected")

    sme_approve = sme_review.to(completed, cond="has_sme_approved")
    sme_reject = sme_review.to(rejected, cond="has_request_rejected")

    @property
    def has_admin_approved(self) -> bool:
        return self.has_fact("admin_approved")

    @property
    def has_sme_approved(self) -> bool:
        return self.has_fact("sme_approved")

    def has_fact(self, fact_type: str) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, fact_type)

    def on_enter_admin_review(self):
        """Open a review request for Governance Admins."""
        self.request.status = RequestStatus.MANAGER_APPROVAL # Or generic PENDING/APPROVAL
        self.db.add(self.request)
        self.db.commit()
        # In a real system, send email/slack to governance team here

    def on_enter_sme_review(self):
        """Open a review request for the Data SME / Owner."""
        self.request.status = RequestStatus.DATA_OWNER_APPROVAL
        self.db.add(self.request)
        self.db.commit()
        # In a real system, send email/slack to the SME/owner here

    async def on_enter_completed_async(self):
        """Apply the certified tag in Databricks and create the finalized Data Contract record."""
        if self.has_fact("certification_applied"):
            return
            
        dataset_id = self.request.state_context.get("dataset_id")
        odcs_yaml = self.request.state_context.get("odcs_yaml")
        
        if not dataset_id:
            logger.error("No dataset_id provided in DATA_CERTIFICATION request.")
            return
            
        try:
            # 1. Apply the system.certification_status = 'certified' tag in Databricks
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST, 
                client_id=settings.DATABRICKS_CLIENT_ID, 
                client_secret=settings.DATABRICKS_CLIENT_SECRET
            )
            handler = DatasetResourceHandler(provider.client)
            await handler.certify(dataset_id)
            logger.info(f"Successfully certified dataset {dataset_id} in Databricks.")
            
            # 2. Record the finalized Data Contract in the Lakebase DB
            if odcs_yaml:
                from app.db.data_contract import DataContractModel
                import uuid
                from datetime import datetime
                
                # Check for existing latest version
                latest = self.db.query(DataContractModel).filter(
                    DataContractModel.dataset_id == dataset_id
                ).order_by(DataContractModel.version.desc()).first()

                new_version = (latest.version + 1) if latest else 1

                if latest:
                    latest.is_active = False
                    self.db.add(latest)

                new_contract = DataContractModel(
                    id=str(uuid.uuid4()),
                    dataset_id=dataset_id,
                    yaml_content=odcs_yaml,
                    version=new_version,
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                self.db.add(new_contract)
                
                # Also update DataAsset model if it exists
                from app.db.data_asset import DataAssetModel
                asset = self.db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
                if asset:
                    asset.contract_url = f"/governance/certification?dataset={dataset_id}"
                    self.db.add(asset)
                    
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to apply certification for {dataset_id}: {e}")
            self.db.rollback()
            # If this was robust, we'd transition to an error state or raise
            raise e

        add_fact(self.db, self.request.id, "certification_applied", {})
        self.request.status = RequestStatus.COMPLETED
        self.db.add(self.request)
        self.db.commit()
