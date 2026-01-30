[Skip to main content](https://docs.databricks.com/aws/en/admin/system-tables/billing#__docusaurus_skipToContent_fallback)

On this page

Last updated on **Jan 23, 2026**

This article provides an overview of the billable usage system table, including the schema and example queries. With system tables, your account's billable usage data is centralized and routed to all regions, so you can view your account's global usage from whichever region your workspace is in.

For information on using this table to monitor costs and sample queries, see [Monitor costs using system tables](https://docs.databricks.com/aws/en/admin/usage/system-tables).

**Table path**: This system table is located at `system.billing.usage`.

## Billable usage table schema [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#billable-usage-table-schema "Direct link to Billable usage table schema")

The billable usage system table uses the following schema:

| Column name | Data type | Description | Example |
| --- | --- | --- | --- |
| `record_id` | string | Unique ID for this usage record | `11e22ba4-87b9-4cc2-9770-d10b894b7118` |
| `account_id` | string | ID of the account this report was generated for | `23e22ba4-87b9-4cc2-9770-d10b894b7118` |
| `workspace_id` | string | ID of the workspace this usage was associated with | `1234567890123456` |
| `sku_name` | string | Name of the SKU | `STANDARD_ALL_PURPOSE_COMPUTE` |
| `cloud` | string | Cloud associated with this usage. Possible values are `AWS`, `AZURE`, and `GCP`. | `AWS`, `AZURE`, or `GCP` |
| `usage_start_time` | timestamp | The start time relevant to this usage record. Timezone information is recorded at the end of the value with `+00:00` representing UTC timezone. | `2023-01-09 10:00:00.000+00:00` |
| `usage_end_time` | timestamp | The end time relevant to this usage record. Timezone information is recorded at the end of the value with `+00:00` representing UTC timezone. | `2023-01-09 11:00:00.000+00:00` |
| `usage_date` | date | Date of the usage record, this field can be used for faster aggregation by date | `2023-01-01` |
| `custom_tags` | map | Custom tags associated with the usage record | `{ “env”: “production” }` |
| `usage_unit` | string | Unit this usage is measured in | `DBU` |
| `usage_quantity` | decimal | Number of units consumed for this record | `259.2958` |
| `usage_metadata` | struct | System-provided metadata about the usage, including IDs for compute resources and jobs (if applicable). See [Usage Metadata](https://docs.databricks.com/aws/en/admin/system-tables/billing#usage-metadata). | See [Usage metadata](https://docs.databricks.com/aws/en/admin/system-tables/billing#usage-metadata) |
| `identity_metadata` | struct | System-provided metadata about the identities involved in the usage. See [Identity Metadata](https://docs.databricks.com/aws/en/admin/system-tables/billing#identity-metadata). | See [Identity metadata](https://docs.databricks.com/aws/en/admin/system-tables/billing#identity-metadata) |
| `record_type` | string | Whether the record is original, a retraction, or a restatement. The value is `ORIGINAL` unless the record is related to a correction. See [Record Type](https://docs.databricks.com/aws/en/admin/system-tables/billing#record-type). | `ORIGINAL` |
| `ingestion_date` | date | Date the record was ingested into the `usage` table | `2024-01-01` |
| `billing_origin_product` | string | The product that originated the usage. Some products can be billed as different SKUs. For possible values, see [Product](https://docs.databricks.com/aws/en/admin/system-tables/billing#product). | `JOBS` |
| `product_features` | struct | Details about the specific product features used. See [Product features](https://docs.databricks.com/aws/en/admin/system-tables/billing#features). | See [Product features](https://docs.databricks.com/aws/en/admin/system-tables/billing#features) |
| `usage_type` | string | The type of usage attributed to the product or workload for billing purposes. Possible values are `COMPUTE_TIME`, `STORAGE_SPACE`, `NETWORK_BYTE`, `NETWORK_HOUR`, `API_OPERATION`, `TOKEN`, `GPU_TIME`, or `ANSWER`. | `STORAGE_SPACE` |

## Usage metadata reference [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#usage-metadata-reference "Direct link to usage-metadata-reference")

The values in `usage_metadata` are all strings that tell you about the workspace objects and resources involved in the usage record.

Only a subset of these values is populated in any given usage record, depending on the compute type and features used. The third column in the table shows which usage types cause each value to be populated.

| Value | Description | Populated for (otherwise `null`) |
| --- | --- | --- |
| `cluster_id` | ID of the cluster associated with the usage record | Non-serverless compute usage, including notebooks, jobs, Lakeflow Spark Declarative Pipelines, and legacy model serving |
| `job_id` | ID of the job associated with the usage record | Serverless jobs and jobs run on job compute (does not populate for jobs run on all-purpose compute) |
| `warehouse_id` | ID of the SQL warehouse associated with the usage record | Workloads run on a SQL warehouse |
| `instance_pool_id` | ID of the instance pool associated with the usage record | Non-serverless compute usage from pools, including notebooks, jobs, Lakeflow Spark Declarative Pipelines, and legacy model serving |
| `node_type` | The instance type of the compute resource | Non-serverless compute usage, including notebooks, jobs, Lakeflow Spark Declarative Pipelines, and all SQL warehouses |
| `job_run_id` | ID of the job run associated with the usage record | Serverless jobs and jobs run on job compute (does not populate for jobs run on all-purpose compute) |
| `notebook_id` | ID of the notebook associated with the usage | Serverless notebooks |
| `dlt_pipeline_id` | ID of the declarative pipeline associated with the usage record | Lakeflow Spark Declarative Pipelines and features that use Lakeflow Spark Declarative Pipelines, such as materialized views, online tables, vector search indexing, and Lakeflow Connect |
| `endpoint_name` | The name of the model serving endpoint or vector search endpoint associated with the usage record | Model serving and Vector Search |
| `endpoint_id` | ID of the model serving endpoint or vector search endpoint associated with the usage record | Model serving and Vector Search |
| `dlt_update_id` | ID of the pipeline update associated with the usage record | Lakeflow Spark Declarative Pipelines and features that use Lakeflow Spark Declarative Pipelines, such as materialized views, online tables, vector search indexing, and Lakeflow Connect |
| `dlt_maintenance_id` | ID of the pipeline maintenance tasks associated with the usage record | Lakeflow Spark Declarative Pipelines and features that use Lakeflow Spark Declarative Pipelines, such as materialized views, online tables, vector search indexing, and Lakeflow Connect |
| `metastore_id` | ID of the metastore associated with the default storage | [Default storage](https://docs.databricks.com/aws/en/storage/default-storage) |
| `run_name` | Unique user-facing name of the Foundation Model Fine-tuning run associated with the usage record | Foundation Model Fine-tuning |
| `job_name` | User-given name of the job associated with the usage record | Serverless jobs and jobs run on job compute (populated for job compute since September 2025). Not populated for all-purpose compute. |
| `notebook_path` | Workspace storage path of the notebook associated with the usage | Notebooks run on serverless compute |
| `central_clean_room_id` | ID of the central clean room associated with the usage record | Clean Rooms |
| `source_region` | Region where billed traffic originated. Only returns a value for serverless networking-related usage. | [Serverless networking](https://www.databricks.com/product/pricing/data-transfer-connectivity) |
| `destination_region` | Region where billed traffic was received. Only returns a value for serverless networking-related usage. | [Serverless networking](https://www.databricks.com/product/pricing/data-transfer-connectivity) |
| `app_id` | ID of the app associated with the usage record | Databricks Apps |
| `app_name` | User-given name of the app associated with the usage record | Databricks Apps |
| `private_endpoint_name` | This value is not populated in Databricks on AWS | Always `null` on Databricks on AWS |
| `budget_policy_id` | ID of the serverless budget policy attached to the workload | Serverless compute usage, including notebooks, jobs, Lakeflow Spark Declarative Pipelines, and model serving endpoints |
| `storage_api_type` | The type of operation performed on default storage. Possible values are `TIER_1` (PUT, COPY, POST, LIST) and `TIER_2` (other operations) | [Default storage](https://docs.databricks.com/aws/en/storage/default-storage) |
| `ai_runtime_workload_id` | ID of the serverless GPU workload associated with the usage record | [Serverless GPU](https://docs.databricks.com/aws/en/compute/serverless/gpu) workloads |
| `uc_table_catalog` | The Unity Catalog catalog name associated with the usage record | [Materialized views](https://docs.databricks.com/aws/en/ldp/dbsql/materialized) |
| `uc_table_schema` | The Unity Catalog schema name associated with the usage record | [Materialized views](https://docs.databricks.com/aws/en/ldp/dbsql/materialized) |
| `uc_table_name` | The Unity Catalog table name associated with the usage record | [Materialized views](https://docs.databricks.com/aws/en/ldp/dbsql/materialized) |
| `database_instance_id` | ID of the database instance associated with the usage record | Lakebase database instances |
| `sharing_materialization_id` | ID of the sharing materialization associated with the usage record | View sharing, materialized views, and streaming tables using Delta Sharing |
| `usage_policy_id` | ID of the usage policy associated with the usage record | Usage policies |
| `agent_bricks_id` | ID of the agent bricks workload associated with the usage record | Agent Bricks workloads |
| `base_environment_id` | ID of the [base environment](https://docs.databricks.com/aws/en/admin/workspace-settings/base-environment) associated with the usage | Usage from building or refreshing a workspace's serverless base environment. Populated when `billing_origin_product` is `BASE_ENVIRONMENTS`. |

## Identity metadata reference [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#identity-metadata-reference "Direct link to identity-metadata-reference")

The `identity_metadata` column provides more information about the identities involved in the usage.

- The `run_as` field logs who ran the workload. This values is only populated for certain workload types listed in the table below.
- The `owned_by` field only applies to SQL warehouse usage and logs the user or service principal who owns the SQL warehouse responsible for the usage.

- The `created_by` field applies to Databricks Apps and Agent Bricks, and logs the email of the user who created the app or agent.

### run\_as identities [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#run_as-identities "Direct link to run_as identities")

The identity recorded in `identity_metadata.run_as` depends on the product associated with the usage. Reference the following table for the `identity_metadata.run_as` behavior:

| Workload type | Identity of `run_as` |
| --- | --- |
| Jobs compute | The user or service principal defined in the `run_as` setting. By default, jobs run as the identity of the job owner, but admins can change this to be another user or service principal. |
| Serverless compute for jobs | The user or service principal defined in the `run_as` setting. By default, jobs run as the identity of the job owner, but admins can change this to be another user or service principal. |
| Serverless compute for notebooks | The user who ran the notebook commands (specifically, the user who created the notebook session). For shared notebooks, this includes usage by other users sharing the same notebook session. |
| Lakeflow Spark Declarative Pipelines | The user or service principal whose permissions are used to run the pipeline. This can be changed by transferring the pipeline's ownership. |
| Foundation Model Fine-tuning | The user or service principal that initiated the fine-tuning training run. |
| Predictive optimization | The Databricks-owned service principal that runs predictive optimization operations. |
| Data quality monitoring | The user who created the profile. |

note

In workspaces enabled for the FedRamp compliance standard, all non-null values in the `identity_metadata` column will be replaced with `__REDACTED__`.

## Record type reference [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#record-type-reference "Direct link to record-type-reference")

The `billing.usage` table supports corrections. Corrections occur when any field of the usage record is incorrect and must be fixed.

When a correction happens, Databricks adds two new records to the table. A retraction record negates the original incorrect record, then a restatement record includes the corrected information. Correction records are identified using the `record_type` field:

- `RETRACTION`: Used to negate the original incorrect usage. All fields are identical to the `ORIGINAL` record except `usage_quantity`, which is a negative value that cancels out the original usage quantity. For example, if the original record's usage quantity was `259.4356`, then the retraction record would have a usage quantity of `-259.4356`.
- `RESTATEMENT`: The record that includes the correct fields and usage quantity.

For example, the following query returns the correct hourly usage quantity related to a `job_id`, even if corrections have been made. By aggregating the usage quantity, the retraction record negates the original record and only the restatement's values are returned.

SQL

```sql
SELECT
  usage_metadata.job_id, usage_start_time, usage_end_time,
  SUM(usage_quantity) as usage_quantity
FROM system.billing.usage
GROUP BY ALL
HAVING usage_quantity != 0
```

note

For corrections where the original usage record should not have been written, a correction may only add a retraction record and no restatement record.

## Billing origin product reference [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#billing-origin-product-reference "Direct link to billing-origin-product-reference")

Some Databricks products are billed under the same shared SKU. For example, data quality monitoring, predictive optimization, and serverless workflows are all billed under the same serverless jobs SKU.

To help you differentiate usage, the `billing_origin_product` and `product_features` columns provide more insight into the specific product and features associated with the usage.

The `billing_origin_product` column shows the Databricks product associated with the usage record. The values include:

| Value | Description |
| --- | --- |
| `JOBS` | Costs associated with [Lakeflow Jobs](https://docs.databricks.com/aws/en/jobs/) workloads |
| `DLT` | Costs associated with [Lakeflow Spark Declarative Pipelines](https://docs.databricks.com/aws/en/ldp/) workloads |
| `SQL` | Costs associated with [Databricks SQL](https://docs.databricks.com/aws/en/sql/), including workloads run on SQL warehouses and materialized views |
| `ALL_PURPOSE` | Costs associated with [classic all-purpose compute](https://docs.databricks.com/aws/en/compute/use-compute) |
| `MODEL_SERVING` | Costs associated with [Mosaic AI Model Serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/) |
| `INTERACTIVE` | Costs associated with [serverless interactive workloads](https://docs.databricks.com/aws/en/compute/serverless/notebooks) |
| `DEFAULT_STORAGE` | Costs associated with [default storage](https://docs.databricks.com/aws/en/storage/default-storage) |
| `VECTOR_SEARCH` | Costs associated with [Vector Search](https://docs.databricks.com/aws/en/vector-search/vector-search) |
| `LAKEHOUSE_MONITORING` | Costs associated with [Data Quality Monitoring](https://docs.databricks.com/aws/en/data-quality-monitoring/) |
| `PREDICTIVE_OPTIMIZATION` | Costs associated with [predictive optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization) |
| `ONLINE_TABLES` | Costs associated with online tables (Legacy) |
| `FOUNDATION_MODEL_TRAINING` | Costs associated with [Foundation Model Fine-tuning](https://docs.databricks.com/aws/en/large-language-models/foundation-model-training/) |
| `AGENT_EVALUATION` | Costs associated with [agent evaluation](https://docs.databricks.com/aws/en/generative-ai/agent-evaluation/) |
| `FINE_GRAINED_ACCESS_CONTROL` | Serverless usage from [fine-grained access control on dedicated compute](https://docs.databricks.com/aws/en/compute/single-user-fgac) |
| `BASE_ENVIRONMENTS` | Usage associated with building or refreshing a workspace's [serverless base environment](https://docs.databricks.com/aws/en/admin/workspace-settings/base-environment) |
| `DATA_CLASSIFICATION` | Costs associated with [data classification](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-classification) operations |
| `DATA_QUALITY_MONITORING` | Costs associated with [data quality monitoring](https://docs.databricks.com/aws/en/data-quality-monitoring/), including anomaly detection and data profiling |
| `DATA_SHARING` | Costs associated with [Delta Sharing](https://docs.databricks.com/aws/en/delta-sharing/) |
| `AI_GATEWAY` | Costs associated with [AI Gateway](https://docs.databricks.com/aws/en/generative-ai/external-models/) usage |
| `AI_RUNTIME` | Costs associated with serverless GPU workloads |
| `NETWORKING` | Costs associated with connecting serverless compute to your resources |
| `APPS` | Costs associated with building and running [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) |
| `DATABASE` | Costs associated with [Lakebase database instances](https://docs.databricks.com/aws/en/oltp/instances/instance) |
| `AI_FUNCTIONS` | Costs associated with [AI Functions](https://docs.databricks.com/aws/en/large-language-models/ai-functions) usage. This product only records usage for the [AI\_PARSE\_DOCUMENT](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_parse_document) function. |
| `AGENT_BRICKS` | Costs associated with [Agent Bricks](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/) workloads |
| `CLEAN_ROOM` | Costs associated with [Clean Rooms](https://docs.databricks.com/aws/en/clean-rooms/) workloads |
| `LAKEFLOW_CONNECT` | Costs associated with [Lakeflow Connect](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/) managed connectors |

## Product features reference [​](https://docs.databricks.com/aws/en/admin/system-tables/billing\#product-features-reference "Direct link to product-features-reference")

The `product_features` column is an object containing information about the specific product features used and includes the following key/value pairs:

| Field | Description |
| --- | --- |
| `jobs_tier` | Values include `LIGHT`, `CLASSIC`, or `null` |
| `sql_tier` | Values include `CLASSIC`, `PRO`, or `null` |
| `dlt_tier` | Values include `CORE`, `PRO`, `ADVANCED`, or `null` |
| `is_serverless` | Values include `true` or `false`, or `null` (value is `true` or `false` when you can choose between serverless and classic compute, otherwise it's `null`) |
| `is_photon` | Values include `true` or `false`, or `null` |
| `serving_type` | Values include `MODEL`, `GPU_MODEL`, `FOUNDATION_MODEL`, `FEATURE`, or `null` |
| `offering_type` | Values include `BATCH_INFERENCE` or `null` |
| `performance_target` | Indicates the [performance mode](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs#performance) of the serverless job or pipeline. Values include `PERFORMANCE_OPTIMIZED`, `STANDARD`, or `null`. Non-serverless workloads have a `null` value. |
| `ai_runtime.compute_type` | Indicates the compute type for serverless GPU workloads or `null` |
| `model_serving.offering_type` | Indicates the offering type for model serving or `null` |
| `ai_gateway.feature_type` | Indicates the feature type for AI Gateway workloads or `null` |
| `serverless_gpu.workload_type` | Indicates the workload type for serverless GPU compute or `null` |
| `ai_functions.ai_function` | Indicates the AI function type or `null` |
| `networking.connectivity_type` | Values include `PUBLIC_IP` and `PRIVATE_IP` |
| `agent_bricks.problem_type` | Indicates the problem type for Agent Bricks workloads. Values include `AGENT_BRICKS_KNOWLEDGE_ASSISTANT` or `null` |
| `agent_bricks.workload_type` | Indicates the workload type for Agent Bricks. Values include `AGENT_BRICKS_REAL_TIME_INFERENCE` or `null` |

- [Billable usage table schema](https://docs.databricks.com/aws/en/admin/system-tables/billing#billable-usage-table-schema)
- [Usage metadata reference](https://docs.databricks.com/aws/en/admin/system-tables/billing#usage-metadata-reference)
- [Identity metadata reference](https://docs.databricks.com/aws/en/admin/system-tables/billing#identity-metadata-reference)
  - [run\_as identities](https://docs.databricks.com/aws/en/admin/system-tables/billing#run_as-identities)
- [Record type reference](https://docs.databricks.com/aws/en/admin/system-tables/billing#record-type-reference)
- [Billing origin product reference](https://docs.databricks.com/aws/en/admin/system-tables/billing#billing-origin-product-reference)
- [Product features reference](https://docs.databricks.com/aws/en/admin/system-tables/billing#product-features-reference)

Ask Assistant

Open Assistant

|     |     |
| --- | --- |
|  |  |