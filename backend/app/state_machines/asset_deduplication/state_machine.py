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


class AssetDeduplicationStateMachine(BaseRequestStateMachine):
    """
    State machine for the Near-Duplicate Asset Detection governance pipeline.

    Follows the job topology defined in the agent instructions:
      pending → ingesting → computing → scoring → classifying → reporting → completed

    This pipeline runs without human approvals — it is an automated governance scan.
    """

    pending = State("pending", initial=True)
    ingesting = State("ingesting")       # ingest_metadata: collect columns, comments, tags, Delta history
    computing = State("computing")       # compute_features: normalize, MinHash/SimHash, lineage pull
    scoring = State("scoring")           # generate_candidates + score_pairs: block & compute similarity
    classifying = State("classifying")  # classify_and_persist: BLOCKER / WARN / INFO gate
    reporting = State("reporting")       # report_run: produce HTML/Markdown summary
    completed = State("completed", final=True)
    failed = State("failed", final=True)

    # --- Transitions ---
    submit = pending.to(ingesting, cond="has_request_submitted")

    finish_ingesting = ingesting.to(computing)
    finish_computing = computing.to(scoring)
    finish_scoring = scoring.to(classifying)
    finish_classifying = classifying.to(reporting)
    finish_reporting = reporting.to(completed)

    fail = (
        pending.to(failed, cond="has_request_rejected")
        | ingesting.to(failed)
        | computing.to(failed)
        | scoring.to(failed)
        | classifying.to(failed)
        | reporting.to(failed)
    )

    # --- Async hooks (implement pipeline logic here) ---

    async def on_enter_ingesting_async(self):
        """
        Enumerate tables/views in target_catalog and reference_catalog.
        Collect columns, comments, tags/properties, and Delta DESCRIBE DETAIL/HISTORY snapshots.
        """
        pass

    async def on_enter_computing_async(self):
        """
        Normalize schema tokens, build MinHash/SimHash signatures.
        Build description text blobs and pull lineage upstreams.
        """
        pass

    async def on_enter_scoring_async(self):
        """
        Block candidate pairs by shared prefixes, column overlap, or ANN matches.
        Compute per-signal scores (schema, description, lineage, volume, delta)
        and weighted composite similarity.
        """
        pass

    async def on_enter_classifying_async(self):
        """
        Apply policy thresholds to classify pairs as BLOCKER / WARN / INFO.
        Upsert results into governance.uc_similarity_matches.
        """
        pass

    async def on_enter_reporting_async(self):
        """
        Produce a Markdown/HTML summary of top BLOCKER and WARN matches.
        Dispatch the report via NotificationProvider to the notify recipients.
        """
        pass
