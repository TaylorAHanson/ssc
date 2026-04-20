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
        "sentinel_evaluation": "evaluation_completed",
        "admin_review": "approval_received",
        "sme_review": "approval_received",
        "completed": "certification_applied",
        "rejected": "request_rejected"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "sentinel_evaluation": ["evaluation_completed"],
        "admin_review": ["approval_received", "request_rejected"],
        "sme_review": ["approval_received", "request_rejected"],
        "completed": ["certification_applied"],
        "rejected": ["request_rejected"]
    }

    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "sentinel_evaluation": RequestStatus.PENDING,
        "admin_review": RequestStatus.MANAGER_APPROVAL,
        "sme_review": RequestStatus.DATA_OWNER_APPROVAL,
        "completed": RequestStatus.COMPLETED,
        "rejected": RequestStatus.REJECTED
    }

    APPROVAL_NODES = {
        "admin_review": {"approval_type": "governance_admin", "name": "Governance Admin Review"},
        "sme_review": {"approval_type": "data_owner", "name": "Data SME Review"}
    }

    pending = State("pending", initial=True)
    sentinel_evaluation = State("sentinel_evaluation")
    admin_review = State("admin_review")
    sme_review = State("sme_review")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    # Transitions
    start_review = pending.to(sentinel_evaluation, cond="has_request_submitted")
    
    evaluation_pass = sentinel_evaluation.to(admin_review, cond="has_evaluation_passed")
    evaluation_fail = sentinel_evaluation.to(rejected, cond="has_evaluation_failed")
    
    admin_approve = admin_review.to(sme_review, cond="has_admin_approved")
    admin_reject = admin_review.to(rejected, cond="has_request_rejected")

    sme_approve = sme_review.to(completed, cond="has_sme_approved")
    sme_reject = sme_review.to(rejected, cond="has_request_rejected")

    @property
    def has_evaluation_passed(self) -> bool:
        from app.state_machines.facts import get_fact
        fact = get_fact(self.db, self.request.id, "evaluation_completed")
        return fact and fact.details.get("passed", False)
        
    @property
    def has_evaluation_failed(self) -> bool:
        from app.state_machines.facts import get_fact
        fact = get_fact(self.db, self.request.id, "evaluation_completed")
        return fact and not fact.details.get("passed", True)

    @property
    def has_admin_approved(self) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, "approval_received", approval_type="governance_admin")

    @property
    def has_sme_approved(self) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, "approval_received", approval_type="data_owner")

    def has_fact(self, fact_type: str) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, fact_type)

    async def on_enter_sentinel_evaluation_async(self):
        """Evaluate the Data Contract and its tables against the OPA policy."""
        if self.has_fact("evaluation_completed"):
            return
            
        dataset_ids = self.request.state_context.get("dataset_ids")
        if not dataset_ids:
            dataset_id = self.request.state_context.get("dataset_id")
            if dataset_id:
                dataset_ids = [dataset_id]
            else:
                add_fact(self.db, self.request.id, "evaluation_completed", {"passed": False, "error": "No dataset_ids provided"})
                self.evaluation_fail()
                return

        odcs_yaml = self.request.state_context.get("odcs_yaml", "")
        
        from app.providers.opa.client import OpaProvider
        from app.providers.databricks.client import DatabricksProvider
        from app.providers.databricks.handlers import DatasetResourceHandler
        import os
        from datetime import datetime
        
        try:
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST, 
                client_id=settings.DATABRICKS_CLIENT_ID, 
                client_secret=settings.DATABRICKS_CLIENT_SECRET,
                config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
            )
            workspace_client = provider.client
            handler = DatasetResourceHandler(workspace_client)
            
            opa_provider = OpaProvider(settings.opa_provider_config())
            policy_path = os.path.join("policies", "data_certification.rego")
            query = "data.databricks.governance.data_certification"
            
            import yaml
            
            # Parse ODCS YAML to extract per-table thresholds
            parsed_yaml = {}
            try:
                parsed_yaml = yaml.safe_load(odcs_yaml) or {}
            except Exception as e:
                logger.warning(f"Failed to parse ODCS YAML: {e}")
                
            schemas = parsed_yaml.get("schema", [])
            
            all_passed = True
            all_violations = []
            
            # Extract resources from Databricks to get current TDQ/BDQ/Tags
            for ds_id in dataset_ids:
                try:
                    parts = ds_id.split(".")
                    physical_table = parts[-1] if len(parts) == 3 else ds_id
                    
                    this_schema = next((s for s in schemas if s.get("physicalName") == physical_table), schemas[0] if schemas else {})
                    quality_rules = this_schema.get("quality", [])
                    tdq_threshold = 100
                    bdq_threshold = 100
                    for rule in quality_rules:
                        if rule.get("id") == "technical_dq_threshold":
                            tdq_threshold = rule.get("mustBe", 100)
                        elif rule.get("id") == "business_dq_threshold":
                            bdq_threshold = rule.get("mustBe", 100)
                            
                    table_info = workspace_client.tables.get(full_name=ds_id)
                    tdq_score = "N/A"
                    bdq_score = "N/A"
                    
                    if settings.DATABRICKS_WAREHOUSE_ID:
                        sql_query = f"SELECT tdq_score, bdq_score FROM {settings.DATABRICKS_DATA_QUALITY_TABLE} WHERE dataset_id = '{ds_id}' ORDER BY run_date DESC LIMIT 1"
                        try:
                            response = workspace_client.statement_execution.execute_statement(
                                statement=sql_query,
                                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                                wait_timeout="30s"
                            )
                            if response.result and response.result.data_array and len(response.result.data_array) > 0:
                                tdq_score = float(response.result.data_array[0][0])
                                bdq_score = float(response.result.data_array[0][1])
                        except Exception: pass
                        
                    # Fetch Unity Catalog tags to check if it's eligible
                    is_eligible = False
                    try:
                        uc_tags = workspace_client.entity_tag_assignments.list(entity_type='tables', entity_name=ds_id)
                        for tag_assign in uc_tags:
                            if tag_assign.tag_key == "certification_eligible":
                                is_eligible = str(tag_assign.tag_value).lower() == "true"
                    except Exception: pass
                        
                    resource = {
                        "id": ds_id,
                        "type": "table",
                        "description": table_info.comment or "",
                        "certification_eligible": is_eligible,
                        "tdq_score": tdq_score,
                        "bdq_score": bdq_score,
                        "tdq_threshold": tdq_threshold,
                        "bdq_threshold": bdq_threshold,
                        "odcs_yaml": odcs_yaml
                    }
                    
                    input_data = {
                        "workspace": {"name": "ws-enterprise-prod"},
                        "resource": resource,
                        "request_time": datetime.utcnow().isoformat()
                    }
                    
                    result = await opa_provider.evaluate(
                        policy_path=policy_path,
                        query=query,
                        input_data=input_data
                    )
                    
                    is_violation = result.get("is_violation")
                    if is_violation:
                        all_passed = False
                        all_violations.extend(result.get("violation_reasons", []))
                        
                except Exception as e:
                    all_passed = False
                    all_violations.append(f"Failed to fetch or evaluate {ds_id}: {e}")
                    
            if all_passed:
                add_fact(self.db, self.request.id, "evaluation_completed", {"passed": True})
                self.evaluation_pass()
            else:
                ctx = dict(self.request.state_context or {})
                ctx["evaluation_violations"] = all_violations
                self.request.state_context = ctx
                add_fact(self.db, self.request.id, "evaluation_completed", {"passed": False, "violations": all_violations})
                self.evaluation_fail()
                
        except Exception as e:
            logger.error(f"Sentinel evaluation failed: {e}")
            add_fact(self.db, self.request.id, "evaluation_completed", {"passed": False, "error": str(e)})
            self.evaluation_fail()

    def on_enter_admin_review(self):
        """Open a review request for Governance Admins."""
        self.request.status = RequestStatus.MANAGER_APPROVAL # Or generic PENDING/APPROVAL
        self.create_approval_task("governance_admin")
        self.db.add(self.request)
        self.db.commit()
        # In a real system, send email/slack to governance team here

    def on_enter_sme_review(self):
        """Open a review request for the Data SME / Owner."""
        self.request.status = RequestStatus.DATA_OWNER_APPROVAL
        self.create_approval_task("data_owner")
        self.db.add(self.request)
        self.db.commit()
        # In a real system, send email/slack to the SME/owner here

    async def on_enter_completed_async(self):
        """Apply the certified tag in Databricks and create the finalized Data Contract record."""
        if self.has_fact("certification_applied"):
            return
            
        dataset_ids = self.request.state_context.get("dataset_ids")
        if not dataset_ids:
            dataset_id = self.request.state_context.get("dataset_id")
            if dataset_id:
                dataset_ids = [dataset_id]
            else:
                logger.error("No dataset_ids provided in DATA_CERTIFICATION request.")
                return
                
        odcs_yaml = self.request.state_context.get("odcs_yaml")
        
        try:
            # 1. Apply the system.certification_status = 'certified' tag in Databricks
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST, 
                client_id=settings.DATABRICKS_CLIENT_ID, 
                client_secret=settings.DATABRICKS_CLIENT_SECRET,
                config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
            )
            handler = DatasetResourceHandler(provider.client)
            
            from app.db.data_contract import DataContractModel
            from app.db.data_asset import DataAssetModel
            import uuid
            from datetime import datetime
            
            for ds_id in dataset_ids:
                success = await handler.certify(ds_id)
                if not success:
                    logger.error(f"Databricks SQL failed to certify dataset {ds_id}")
                    continue
                logger.info(f"Successfully certified dataset {ds_id} in Databricks.")
                
                # 2. Record the finalized Data Contract in the Lakebase DB
                if odcs_yaml:
                    # Check for existing latest version
                    latest = self.db.query(DataContractModel).filter(
                        DataContractModel.dataset_id == ds_id
                    ).order_by(DataContractModel.version.desc()).first()

                    new_version = (latest.version + 1) if latest else 1

                    if latest:
                        latest.is_active = False
                        self.db.add(latest)

                    new_contract = DataContractModel(
                        id=str(uuid.uuid4()),
                        dataset_id=ds_id,
                        yaml_content=odcs_yaml,
                        version=new_version,
                        is_active=True,
                        created_at=datetime.utcnow()
                    )
                    self.db.add(new_contract)
                    
                    # Also update DataAsset model if it exists
                    asset = self.db.query(DataAssetModel).filter(DataAssetModel.id == ds_id).first()
                    if asset:
                        asset.contract_url = f"/governance/certification?dataset={ds_id}"
                        asset.certified = True
                        self.db.add(asset)
                        
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to apply certification: {e}")
            self.db.rollback()
            # If this was robust, we'd transition to an error state or raise
            raise e

        add_fact(self.db, self.request.id, "certification_applied", {})
        self.request.status = RequestStatus.COMPLETED
        self.db.add(self.request)
        self.db.commit()
