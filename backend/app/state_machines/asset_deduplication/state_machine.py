"""
Asset Deduplication state machine.
Automated Governance Pipeline for detecting near-duplicate assets in Unity Catalog.

This pipeline compares a target catalog/schema against a certified reference catalog
and scores every asset pair using metadata-first signals (schema, descriptions,
lineage, volume, and Delta history).

### Providers used

#### DatabricksProvider (`app/providers/databricks/client.py`)
**Existing Methods:**
*   `execute_sql`: Query `information_schema` and system tables to collect column
    metadata, table properties, and Delta history snapshots.
*   `find_object_owner`: Route match notifications to the correct asset owner.

**Required Methods to Implement:**
*   `list_lineage_upstreams(full_name)`: Return upstream object list for a given table/view.
*   `describe_detail(full_name)`: Return `DESCRIBE DETAIL` output (sizeInBytes, numFiles, etc.).
*   `describe_history(full_name)`: Return `DESCRIBE HISTORY` output to detect CLONE operations.

#### NotificationProvider (`app/providers/notifications/client.py`)
**Existing Methods:**
*   `send_email(to, subject, body, is_html=True)`: Dispatch the HTML similarity report
    to the specified `notify` recipients or dynamically discovered asset owners.
"""

import logging
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine

logger = logging.getLogger(__name__)


import logging
import os
from datetime import datetime
from statemachine import State
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.facts import add_fact, get_latest_fact, has_fact
from app.providers.databricks.client import DatabricksProvider
from app.models.request import RequestStatus

from app.core.config import settings

logger = logging.getLogger(__name__)


class AssetDeduplicationStateMachine(BaseRequestStateMachine):
    """
    State machine for the Near-Duplicate Asset Detection governance pipeline.
    
    This version offloads the work to a Databricks Job.
    States: pending → job_submitted → job_complete → completed
    """

    # --- States ---
    pending = State("pending", initial=True)
    job_submitted = State("job_submitted")
    job_complete = State("job_complete")
    completed = State("completed", final=True)
    failed = State("failed", final=True)

    # --- Mappings ---
    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "job_submitted": RequestStatus.PROVISIONING,
        "job_complete": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
        "failed": RequestStatus.FAILED
    }

    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "job_submitted": "run_id_created",  # Consider it "provisioned" once the job is in orbit
        "job_complete": "job_completed"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "job_submitted": ["job_submitted", "run_id_created"],
        "job_complete": ["job_completed", "results_summary"]
    }

    # --- Transitions ---
    submit = pending.to(job_submitted, cond="has_request_submitted")
    finish_job = job_submitted.to(job_complete, cond="has_job_completed")
    complete_request = job_complete.to(completed)

    mark_failed = (
        pending.to(failed)
        | job_submitted.to(failed)
        | job_complete.to(failed)
    )

    # --- Logic ---

    async def on_enter_job_submitted_async(self):
        """
        Idempotent: Uploads/Submits the job if not already done, otherwise polls for status.
        """
        # 1. Initialize Provider
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        # 2. Check if already submitted
        # 2. Check if already submitted
        submission_fact = get_latest_fact(self.db, self.request.id, "run_id_created")
        
        if not submission_fact:
            try:
                # First time: Upload and Submit
                logger.info(f"[{self.request.id}] First time entering job_submitted. Uploading notebook...")
                
                local_notebook_path = os.path.join(
                    os.path.dirname(__file__), 
                    "asset_deduplication_job.py"
                )
                remote_notebook_path = f"/Shared/agents/asset_deduplication_{self.request.id}"
                
                await provider.import_notebook(local_notebook_path, remote_notebook_path)
                
                ctx = self.request.state_context or {}
                params = {
                    "target_catalog": ctx.get("target_catalog", ""),
                    "reference_catalog": ctx.get("reference_catalog", ""),
                    "run_id": self.request.id
                }
                
                run_id = await provider.submit_notebook_job(
                    notebook_path=remote_notebook_path,
                    parameters=params,
                    run_name=f"Asset Deduplication: {self.request.id}"
                )
                
                add_fact(self.db, self.request.id, "run_id_created", {
                    "run_id": run_id,
                    "remote_path": remote_notebook_path,
                    "submitted_at": datetime.utcnow().isoformat()
                }, actor="system")
                
                logger.info(f"[{self.request.id}] Job submitted successfully: RunID={run_id}")
            except Exception as e:
                logger.error(f"[{self.request.id}] Failed to submit job: {str(e)}")
                add_fact(self.db, self.request.id, "job_submission_failed", {"error": str(e)}, actor="system")
                self.mark_failed()
                return

        else:
            # Already submitted: Poll for status
            run_id = submission_fact.event_data.get("run_id")
            logger.debug(f"[{self.request.id}] Polling status for run_id: {run_id}")
            
            try:
                status = await provider.get_run_status(run_id)
                
                if status["is_completed"]:
                    if status["is_successful"]:
                        logger.info(f"[{self.request.id}] Job {run_id} completed successfully.")
                        add_fact(self.db, self.request.id, "job_completed", {
                            "run_id": run_id,
                            "status": "success",
                            "completed_at": datetime.utcnow().isoformat()
                        }, actor="system")
                    else:
                        logger.error(f"[{self.request.id}] Job {run_id} failed: {status['state_message']}")
                        add_fact(self.db, self.request.id, "job_failed", {
                            "run_id": run_id,
                            "error": status["state_message"],
                            "failed_at": datetime.utcnow().isoformat()
                        }, actor="system")
                        self.mark_failed()
            except Exception as e:
                logger.error(f"[{self.request.id}] Error polling job status: {str(e)}")
                # We leave it in job_submitted to retry polling later

    def _process_current_state(self) -> bool:
        """
        Custom tick logic to handle polling transitions.
        """
        changed = super()._process_current_state()
        
        # Check for job completion to advance
        if self.current_state.id == "job_submitted" and self.has_job_completed:
            if hasattr(self, "finish_job"):
                self.finish_job()
                changed = True

        if self.current_state.id == "job_complete":
            # Auto-advance to completed after reporting
             self.complete_request()
             changed = True
             
        return changed

    @property
    def has_job_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "job_completed")

    async def on_enter_job_complete_async(self):
        """
        Fetch results (optional) and prepare the final report summary.
        """
        add_fact(self.db, self.request.id, "results_summary", {
            "message": "Deduplication job finished. Matches are available in governance.uc_similarity_matches."
        }, actor="system")
