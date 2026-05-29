"""
Asset Deduplication state machine.
Automated Governance Pipeline for detecting near-duplicate assets in Unity Catalog.

This pipeline compares a target catalog/schema against a certified reference catalog
and scores every asset pair using metadata-first signals (schema, descriptions,
lineage, volume, and Delta history).

### Architecture

Inherits the upload / submit / poll / notify lifecycle from
``BaseDatabricksJobStateMachine``. Only the dedup-specific bits live here:

* ``NOTEBOOK_PATH`` points at the local PySpark notebook (uploaded per run).
* ``build_job_parameters`` derives the widget values from ``state_context``.
* ``on_job_completed_async`` queries the results Delta table written by the
  notebook and persists a summary of the top matches as facts.
* ``send_notification_async`` renders the HTML report email.

### Providers used

* ``DatabricksProvider`` — notebook upload, job submit/poll (via the base
  class), and ``execute_sql`` for the post-run results query.
* ``NotificationProvider`` — HTML email with top matches.
"""

import logging
from datetime import datetime, timezone

from app.models.request import RequestType
from app.state_machines.databricks_job_base import BaseDatabricksJobStateMachine
from app.state_machines.decorators import workflow
from app.state_machines.facts import add_fact, get_latest_fact

logger = logging.getLogger(__name__)


@workflow(request_types=RequestType.ASSET_DEDUPLICATION, feature_flag="governance")
class AssetDeduplicationStateMachine(BaseDatabricksJobStateMachine):
    """Near-duplicate asset detection — runs as a Databricks notebook job."""

    # Local notebook (relative to this file). The base class uploads it to
    # /Shared/agents/<workflow_slug>_<request_id> on first visit to
    # ``job_submitted`` and submits a one-time run against it.
    NOTEBOOK_PATH = "asset_deduplication_job.py"

    # Serverless is fine — this is a long-running batch workload with no
    # control-plane network requirement. Override to True if/when a customer
    # needs to scan a workspace behind PrivateLink.
    USE_CLASSIC_COMPUTE = False

    # ------------------------------------------------------------------
    # Job spec
    # ------------------------------------------------------------------

    def build_job_parameters(self) -> dict:
        ctx = self.request.state_context or {}
        results_table = ctx.get("results_table", "main.governance.uc_similarity_matches")
        return {
            "target_catalog": ctx.get("target_catalog", ""),
            "reference_catalog": ctx.get("reference_catalog", ""),
            "run_id": self.request.id,
            "results_table": results_table,
        }

    def build_run_name(self) -> str:
        return f"Asset Deduplication: {self.request.id}"

    # ------------------------------------------------------------------
    # Results handling (custom: read from the Delta table the notebook wrote)
    # ------------------------------------------------------------------

    async def on_job_completed_async(self) -> None:
        provider = self.build_databricks_provider()
        ctx = self.request.state_context or {}
        target_catalog = ctx.get("target_catalog", "main")
        results_table = ctx.get("results_table", "main.governance.uc_similarity_matches")

        query = f"""
            SELECT target_full_name, reference_full_name, similarity, policy_class, explanation
            FROM {results_table}
            WHERE run_id = '{self.request.id}'
            ORDER BY similarity DESC
            LIMIT 50
        """

        logger.info(f"[{self.request.id}] Fetching deduplication results from {results_table}")
        results = await provider.execute_sql(query)
        matches = results.get("rows", [])

        add_fact(
            self.db, self.request.id, "results_fetched",
            {"match_count": len(matches), "top_matches": matches[:5]},
            actor="system",
        )
        add_fact(
            self.db, self.request.id, "results_summary",
            {
                "message": f"Deduplication job finished. Identified {len(matches)} potential duplicates.",
                "target_catalog": target_catalog,
            },
            actor="system",
        )

    # ------------------------------------------------------------------
    # Notification (custom: rich HTML report rather than the default stub)
    # ------------------------------------------------------------------

    async def send_notification_async(self) -> None:
        ctx = self.request.state_context or {}
        recipient = ctx.get("email")

        if not recipient:
            logger.info(f"[{self.request.id}] No 'email' in context; skipping notification.")
            add_fact(
                self.db, self.request.id, "notification_skipped",
                {"reason": "no_email_provided"}, actor="system",
            )
            return

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

        body = f"""
        <h2>Asset Deduplication Report</h2>
        <p><strong>Target Catalog:</strong> {target_catalog}</p>
        <p><strong>Reference Catalog:</strong> {reference_catalog}</p>
        <p><strong>Total Potential Duplicates:</strong> {match_count}</p>
        """

        if top_matches:
            body += "<h3>Top High-Similarity Matches</h3><ul>"
            for match in top_matches:
                target = match.get("target_full_name", "N/A")
                ref = match.get("reference_full_name", "N/A")
                score = match.get("similarity", 0.0)
                explanation = match.get("explanation", "")
                policy = match.get("policy_class", "INFO")
                color = (
                    "#d32f2f" if policy == "BLOCKER"
                    else "#f57c00" if policy == "WARN"
                    else "#388e3c"
                )
                body += f"""
                <li style="margin-bottom: 10px;">
                    <strong><span style="color: {color};">[{policy}]</span> {target}</strong><br>
                    Matches: {ref} (Score: {score:.2f})<br>
                    <small>{explanation}</small>
                </li>
                """
            body += "</ul>"

        body += f"<p>Full results are available in the <code>{results_table}</code> table.</p>"

        try:
            from app.providers.notifications.client import NotificationProvider
            await NotificationProvider().send_email(
                to=recipient,
                subject=f"Deduplication Results: {target_catalog}",
                body=body,
                is_html=True,
                metadata={"request_id": self.request.id, "match_count": match_count},
            )
            add_fact(
                self.db, self.request.id, "notification_sent",
                {"recipient": recipient, "sent_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to send notification: {e}")
            add_fact(
                self.db, self.request.id, "notification_failed",
                {"error": str(e)}, actor="system",
            )
