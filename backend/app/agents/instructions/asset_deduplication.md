# Near‑Duplicate Asset Detection for Unity Catalog

This document specifies a Databricks Job that scans a **target catalog/schema** against a **certified (reference) catalog** to detect copied or overly similar assets that violate consolidation policy. It uses metadata‑first signals (schema, docs, lineage, volume/shape, Delta history)to surface explainable, scored matches.

The goal is to reduce proliferation of redundant tables/views while preserving sanctioned patterns (e.g., curated marts with clear purpose/tags).

---

## Inputs and Parameters

- **target_catalog**: e.g., `sales_analytics`
- **reference_catalog**: e.g., `enterprise_certified`
- **weights**: per‑signal weights (schema, description, lineage, volume, delta)
- **thresholds**:
  - `blocker_threshold` (e.g., 0.90)
  - `warn_threshold` (e.g., 0.75)
- **include_views**: default `true`

---

## Signals (Features)

- **Schema/Columns**
  - Normalized column names (lowercase, alnum, underscores), types, nullability.
  - Jaccard similarity on column name sets; type agreement ratio.
- **Descriptions/Documentation**
  - Table and column comments; tags/properties (e.g., `purpose`, `domain`, `pii`).
  - Basic token overlap (Jaccard similarity) on normalized text.
- **Lineage (Unity Catalog)**
  - Upstream object overlap; proximity to reference objects.
  - CTAS from a certified table; views on certified sources with minimal transformations.
- **Volume/Shape/Freshness**
  - `sizeInBytes`, `numFiles`, modified time; optional approximate row count if available.
  - Ratio similarities using log‑transforms or bounded ratios.
- **Delta/History**
  - Presence of `CLONE` operations; history proximity to certified sources.

Example default weights (tunable per domain):
- `w_schema=0.35`, `w_desc=0.20`, `w_lineage=0.25`, `w_volume=0.10`, `w_delta=0.10`.  
If `fingerprint` is enabled and consistent, boost final similarity or “override”.

---

## Outputs (Delta Tables)

- `governance.uc_similarity_assets` (per‑asset features)
  - `full_name` STRING (catalog.schema.object)
  - `catalog`, `schema`, `object_name`, `object_type`
  - `owner` STRING
  - `comment` STRING
  - `tags` MAP<STRING, STRING>
  - `columns` ARRAY<STRUCT<name_norm:STRING, data_type:STRING, comment_norm:STRING>>
  - `schema_sig` STRING (e.g., MinHash/SimHash of tokens)
  - `desc_embedding` ARRAY<DOUBLE> (optional)
  - `size_in_bytes` BIGINT, `num_files` BIGINT, `last_modified` TIMESTAMP
  - `table_properties` MAP<STRING, STRING>
  - `lineage_upstreams` ARRAY<STRING>, `lineage_depth` INT
  - `delta_has_clone_history` BOOLEAN
  - `run_id` STRING, `ingested_at` TIMESTAMP

- `governance.uc_similarity_matches` (pairwise results)
  - `target_full_name` STRING, `reference_full_name` STRING
  - `s_schema`, `s_desc`, `s_lineage`, `s_volume`, `s_delta`, `s_fingerprint` DOUBLE
  - `similarity` DOUBLE
  - `policy_class` STRING (BLOCKER/WARN/INFO)
  - `explanation` STRING
  - `run_id` STRING, `scored_at` TIMESTAMP

---

## Job Topology (Tasks)

1) **ingest_metadata** (SQL + PySpark)
- Enumerate tables/views in `target_catalog.target_schema` and all tables/views in `reference_catalog`.
- Collect columns, comments, tags/properties, and Delta `DESCRIBE DETAIL`/`DESCRIBE HISTORY` snapshots.

2) **compute_features** (PySpark)
- Normalize schema tokens, build MinHash/SimHash signatures.
- Build description text blobs and (optionally) **embed** via Model Serving; fallback to TF‑IDF.
- Pull lineage upstreams for each object (metadata‑only).

3) **generate_candidates** (PySpark)
- Block by:
  - Shared domain/schema prefixes,
  - Overlap ≥ N normalized column tokens,
  - Top‑K description ANN matches (if embeddings enabled).
- Materialize a reduced candidate set for scoring.

4) **score_pairs** (PySpark/SQL UDFs)
- Compute per‑signal scores and composite similarity.
- Optional fingerprint check if sampling is permitted.

5) **classify_and_persist** (SQL)
- Policy gate into BLOCKER/WARN/INFO based on thresholds and allow‑lists/tags.
- Upsert rows into `governance.uc_similarity_matches` and update assets table.

6) **report_run** (SQL/Notebook)
- Produce a Markdown/HTML summary with top BLOCKER and WARN items.
- Optional: refresh a Databricks SQL dashboard.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "asset_deduplication",
  "parameters": {
    "target_catalog": "...",
    "reference_catalog": "..."
  }
}
```
