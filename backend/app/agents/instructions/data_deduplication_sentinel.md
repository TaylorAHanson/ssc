# Data Deduplication Sentinel Instructions

**Goal**: Act as an automated Governance Pipeline for Databricks Unity Catalog. Scan a **target catalog** against a **reference catalog** to detect near-duplicate assets (tables/views) that violate data consolidation policy. Use metadata-first signals to surface explainable, scored matches and record them for governance review.

The Sentinel is typically triggered manually by an agent or admin. All parameters are gathered upfront.

---

## Information to Gather

Before executing the workflow, ensure you have the following information from the user:

| # | Parameter | Required | Default | Notes |
|---|-----------|----------|---------|-------|
| 1 | **Target Workspace** | ✅ Yes | — | The workspace where the scan should run. (MUST use `get_target_workspaces` to find the exact `host` URL) |
| 2 | **Target Catalog** | ✅ Yes | — | The catalog to scan for potential duplicates. (MUST verify existence using `get_catalog_list` passing the `target_host`) |
| 3 | **Reference Catalog** | ✅ Yes | `enterprise_certified` | The "Golden" catalog to compare against. (MUST verify existence using `get_catalog_list` passing the `target_host`) |
| 3 | **Results Table** | ❌ Optional | `main.governance.uc_similarity_matches` | The fully qualified table (catalog.schema.table) to write results to. |
| 4 | **Email Notification** | ❌ Optional | — | Email address to send the final deduplication report to. |
| 5 | **Title** | ❌ Optional | "Deduplication Scan: {target}" | A descriptive title for the request. |

> **Agent Note**: If the user doesn't provide a reference catalog, suggest `enterprise_certified` or ask which catalog represents the certified "truth".

---

## Signals & Scoring

The Sentinel evaluates similarity across several dimensions:

1. **Schema (35%)**: Overlap in normalized column names and types.
2. **Lineage (25%)**: Shared upstream sources in Unity Catalog.
3. **Documentation (20%)**: Similarity in table/column comments and tags.
4. **Volume/Shape (10%)**: Similarity in data size (`sizeInBytes`) and file counts.
5. **Delta History (10%)**: Detection of `CLONE` operations or shared history.

Match Classification:
- **BLOCKER** (Similarity ≥ 0.90): Extremely high likelihood of being a duplicate. 
- **WARN** (Similarity ≥ 0.75): High similarity, requires manual review.
- **INFO**: Low similarity, informational only.

---

## Execution

Once you have gathered the required catalogs, call the `execute_workflow` tool with the following structure:

```json
{
  "workflow_type": "asset_deduplication",
  "parameters": {
    "target_host": "user_provided_target_host",
    "target_catalog": "user_provided_target",
    "reference_catalog": "user_provided_reference",
    "results_table": "user_provided_catalog.schema.table",
    "email": "user@example.com",
    "title": "Data Deduplication Sentinel Scan"
  }
}
```

---

## Required Providers and Methods

### DatabricksProvider (`app/providers/databricks/client.py`)

The workflow automatically uses the following methods:
- `import_notebook`: Uploads the deduplication logic to the workspace.
- `submit_notebook_job`: Triggers the PySpark execution on **Serverless Compute**.
- `get_run_status`: Polls for job completion.
- `execute_sql`: Used within the job to query `information_schema` and `system` tables.

---

## Architecture Note: Governance Pipeline

The Data Deduplication Sentinel is a **Governance Pipeline**. Unlike provisioning workflows, it does not require human-in-the-loop approvals before execution. Once `execute_workflow` is called, it moves directly to job submission and status polling.

Result data is persisted to:
- The fully qualified table specified in `results_table` (defaults to `main.governance.uc_similarity_matches`). This table contains the scored pairs with detailed explanations.
