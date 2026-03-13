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
from app.core.exceptions import PermanentError, RetryableError

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
    notifying = State("notifying")
    completed = State("completed", final=True)
    failed = State("failed", final=True)

    # --- Mappings ---
    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "job_submitted": RequestStatus.PROVISIONING,
        "job_complete": RequestStatus.PROVISIONING,
        "notifying": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
        "failed": RequestStatus.FAILED
    }

    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "job_submitted": "run_id_created",  # Consider it "provisioned" once the job is in orbit
        "job_complete": "job_completed",
        "notifying": "notification_sent"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "job_submitted": ["job_submitted", "run_id_created"],
        "job_complete": ["job_completed", "results_summary"],
        "notifying": ["notification_sent", "notification_skipped"]
    }

    # --- Transitions ---
    submit = pending.to(job_submitted, cond="has_request_submitted")
    finish_job = job_submitted.to(job_complete, cond="has_job_completed")
    notify = job_complete.to(notifying, cond="has_results_fetched")
    complete_request = notifying.to(completed)

    mark_failed = (
        pending.to(failed)
        | job_submitted.to(failed)
        | job_complete.to(failed)
        | notifying.to(failed)
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
                # Set default results table if not provided
                results_table = ctx.get("results_table", f"main.governance.uc_similarity_matches")
                
                params = {
                    "target_catalog": ctx.get("target_catalog", ""),
                    "reference_catalog": ctx.get("reference_catalog", ""),
                    "run_id": self.request.id,
                    "results_table": results_table
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
            except PermanentError as e:
                logger.error(f"[{self.request.id}] Permanent error polling job status: {str(e)}")
                add_fact(self.db, self.request.id, "job_failed", {
                    "run_id": run_id,
                    "error": str(e),
                    "failed_at": datetime.utcnow().isoformat()
                }, actor="system")
                self.mark_failed()
            except RetryableError as e:
                logger.error(f"[{self.request.id}] Retryable error polling job status: {str(e)}")
                # We leave it in job_submitted to retry polling later
                raise
            except Exception as e:
                logger.error(f"[{self.request.id}] Unexpected error polling job status: {str(e)}")
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

        if self.current_state.id == "job_complete" and self.has_results_fetched:
            # Auto-advance to notifying after fetching results
             self.notify()
             changed = True

        if self.current_state.id == "notifying":
             # Auto-advance to completed after notification attempt
             self.complete_request()
             changed = True
             
        return changed

    @property
    def has_job_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "job_completed")

    @property
    def has_results_fetched(self) -> bool:
        return has_fact(self.db, self.request.id, "results_fetched")

    async def on_enter_job_complete_async(self):
        """
        Fetch results and prepare the final report summary.
        """
        try:
            # 1. Initialize Provider
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
                token=settings.DATABRICKS_TOKEN,
                client_id=settings.DATABRICKS_CLIENT_ID,
                client_secret=settings.DATABRICKS_CLIENT_SECRET,
                config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
            )

            # 2. Fetch matches from governance table
            ctx = self.request.state_context or {}
            target_catalog = ctx.get("target_catalog", "main")
            results_table = ctx.get("results_table", f"main.governance.uc_similarity_matches")
            
            query = f"""
                SELECT target_full_name, reference_full_name, similarity, policy_class, explanation
                FROM {results_table}
                WHERE run_id = '{self.request.id}'
                ORDER BY similarity DESC
                LIMIT 50
            """
            
            logger.info(f"[{self.request.id}] Fetching deduplication results from {results_table}...")
            results = await provider.execute_sql(query)
            matches = results.get("rows", [])
            
            add_fact(self.db, self.request.id, "results_fetched", {
                "match_count": len(matches),
                "top_matches": matches[:5] # Store top 5 in fact for quick access
            }, actor="system")

            add_fact(self.db, self.request.id, "results_summary", {
                "message": f"Deduplication job finished. Identified {len(matches)} potential duplicates.",
                "target_catalog": target_catalog
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to fetch results: {str(e)}")
            add_fact(self.db, self.request.id, "results_fetch_failed", {"error": str(e)}, actor="system")
            # If fetching fails, we might still want to proceed to notifying (reporting failure) or fail.
            # For now, let's mark failed to be safe.
            self.mark_failed()

    async def on_enter_notifying_async(self):
        """
        Send email notification with the results.
        """
        try:
            ctx = self.request.state_context or {}
            # User requested 'email' as an optional field
            recipient = ctx.get("email")
            
            if not recipient:
                logger.info(f"[{self.request.id}] No 'email' field in context. Skipping notification.")
                add_fact(self.db, self.request.id, "notification_skipped", {
                    "reason": "no_email_provided"
                }, actor="system")
                return

            # Retrieve results from facts
            results_fact = get_latest_fact(self.db, self.request.id, "results_fetched")
            if not results_fact:
                logger.warning(f"[{self.request.id}] No results found to notify about.")
                return

            data = results_fact.event_data
            match_count = data.get("match_count", 0)
            top_matches = data.get("top_matches", [])
            target_catalog = ctx.get("target_catalog", "Unknown")
            reference_catalog = ctx.get("reference_catalog", "Unknown")
            results_table = ctx.get("results_table", "main.governance.uc_similarity_matches")

            # Build email body
            body = f"""
            <h2>Asset Deduplication Report</h2>
            <p><strong>Target Catalog:</strong> {target_catalog}</p>
            <p><strong>Reference Catalog:</strong> {reference_catalog}</p>
            <p><strong>Total Potential Duplicates:</strong> {match_count}</p>
            """

            if top_matches:
                body += "<h3>Top High-Similarity Matches</h3><ul>"
                for match in top_matches:
                    target = match.get('target_full_name', 'N/A')
                    ref = match.get('reference_full_name', 'N/A')
                    score = match.get('similarity', 0.0)
                    explanation = match.get('explanation', '')
                    policy = match.get('policy_class', 'INFO')
                    
                    color = "#d32f2f" if policy == "BLOCKER" else "#f57c00" if policy == "WARN" else "#388e3c"
                    
                    body += f"""
                    <li style="margin-bottom: 10px;">
                        <strong><span style="color: {color};">[{policy}]</span> {target}</strong><br>
                        Matches: {ref} (Score: {score:.2f})<br>
                        <small>{explanation}</small>
                    </li>
                    """
                body += "</ul>"
            
            body += f"<p>Full results are available in the <code>{results_table}</code> table.</p>"

            from app.providers.notifications.client import NotificationProvider
            provider = NotificationProvider()
            
            logger.info(f"[{self.request.id}] Sending results email to {recipient}")
            await provider.send_email(
                to=recipient,
                subject=f"Deduplication Results: {target_catalog}",
                body=body,
                is_html=True,
                metadata={
                    "request_id": self.request.id,
                    "match_count": match_count
                }
            )
            
            add_fact(self.db, self.request.id, "notification_sent", {
                "recipient": recipient,
                "sent_at": datetime.utcnow().isoformat()
            }, actor="system")
            
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to send notification: {str(e)}")
            # We don't fail the request if notification fails, just log it.
            add_fact(self.db, self.request.id, "notification_failed", {"error": str(e)}, actor="system")

