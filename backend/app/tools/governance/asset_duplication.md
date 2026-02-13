# Atomic Tool Design: `assess_table_duplication`

This tool provides a **single, atomic entry point** for detecting duplicate or near-duplicate assets across Unity Catalogs. By encapsulating the entire scanning and assessment workflow into one command, we ensure consistency and simplicity for the agent.

## Core Concept
The `assess_table_duplication` tool is an **asynchronous orchestrator**. It validates the request synchronously and then offloads the heavy computational work (metadata scanning, embedding generation, pairwise comparison) to a robust background process (e.g., a Databricks Job). This prevents webserver overloads and timeouts.

## Tool Interface

### `assess_table_duplication`
**Description**: Triggers a comprehensive assessment of the target catalog against the reference catalog to identify redundant assets.

**Inputs:**
- `target_catalog` (string, required): The name of the catalog to scan for duplicates (e.g., `sandbox_analytics`).
- `reference_catalog` (string, required): The "gold standard" catalog to compare against (e.g., `enterprise_certified`).
- `scope` (string, optional): A specific schema or table pattern to limit the scan (default: entire catalog).
- `mode` (string, optional): Analysis mode, either `standard` (metadata only) or `deep` (includes data sampling). Default: `standard`.

**Returns:**
- A JSON object containing:
  - `run_id`: The unique identifier for this assessment job.
  - `status`: "job_started".
  - `dashboard_url`: Link to the results dashboard.
  - `message`: "Assessment started. Results will be available in the governance dashboard shortly."

---

## Execution Constraints & Async Boundaries

To ensure system stability, strict boundaries define what happens **synchronously** (in the webserver/tool loop) versus **asynchronously** (in the background compute).

### 1. Synchronous Layer (The Tool)
*These operations happen immediately when the tool is called.*
- **Input Validation**: Verifies that `target_catalog` and `reference_catalog` exist and are accessible.
- **Configuration Generation**: Constructs the job configuration based on inputs (e.g., setting weights, defining scope).
- **Job Submission**: Calls the Databricks Jobs API (`runs.submit` or `jobs.run_now`) to kick off the background process.
- **Response**: Returns the `run_id` to the agent.

### 2. Asynchronous Layer (The Background Job)
*These operations are computationally intensive and **MUST** run in the background to avoid blocking the agent or overloading the webserver.*

#### A. Metadata Ingestion (I/O Bound)
- **Scanning**: Iterating through thousands of tables/views in `information_schema`.
- **Lineage Fetching**: Querying the system lineage graph for every asset.

#### B. Feature Engineering (Compute/GPU Bound)
- **Tokenization**: Normalizing column names and comments for Jaccard similarity.
- **Embedding Generation**: Sending descriptions to a Model Serving endpoint to generate vector embeddings. **(Critical Async Step)**

#### C. Pairwise Comparison (Memory/Shuffle Bound)
- **Candidate Generation**: Using LSH (Locality Sensitive Hashing) or Vector Search to find candidate pairs.
- **Scoring**: Computing weighted similarity scores for all candidate pairs.

#### D. Persistence (I/O Bound)
- **Result Writing**: Upserting findings into the `governance.duplication_assessments` Delta table.

---

## Data Output
The async job populates the following table:

**`governance.duplication_assessments`**
| Column | Type | Description |
| :--- | :--- | :--- |
| `run_id` | STRING | Unique run ID returned to the user. |
| `target_table_name` | STRING | The table being assessed. |
| `reference_table_name` | STRING | The potential certified match. |
| `similarity_score` | FLOAT | 0.0 - 1.0 composite score. |
| `match_type` | STRING | `EXACT`, `HIGH_CONFIDENCE`, `POSSIBLE`. |
| `evidence` | STRUCT | Breakdown of schema, lineage, and semantic scores. |
| `recommendation` | STRING | `DROP`, `ARCHIVE`, `FLAG_AS_DERIVED`. |

## Agent Usage Example

**User**: "Check if the `sandbox_sales` catalog has any duplicates of our `prod_finance` data."

**Agent**:
```json
{
  "name": "assess_table_duplication",
  "arguments": {
    "target_catalog": "sandbox_sales",
    "reference_catalog": "prod_finance"
  }
}
```

**Tool Output**:
```json
{
  "run_id": "job_12345_run_67890",
  "status": "job_started",
  "message": "Assessment job 67890 started. This usually takes 2-5 minutes. View progress at: https://<workspace>/#job/12345/run/67890"
}
```
