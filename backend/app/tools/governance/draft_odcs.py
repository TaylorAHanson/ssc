from typing import Dict, Any, Optional, List
import json
import logging
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.model_serving.agent_llm import AgentLLMClient
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

logger = logging.getLogger(__name__)

class DraftOdcsInput(BaseModel):
    dataset_ids: List[str] = Field(..., description="The list of fully qualified dataset IDs (e.g., ['catalog.schema.table1', 'catalog.schema.table2'])")
    violations_context: Optional[Dict[str, Any]] = Field(None, description="Optional. The raw OPA violations context indicating what is currently failing certification.")

@tool(
    name="draft_odcs_contract",
    description="Drafts an Open Data Contract Standard (ODCS) v3 YAML document for a given set of datasets by fetching their metadata and using AI to generate the combined structure. Important: Always use this tool to generate the 'odcs_yaml' parameter before calling execute_workflow for data_certification.",
    args_schema=DraftOdcsInput
)
async def draft_odcs_contract(dataset_ids: List[str], violations_context: Optional[Any] = None) -> str:
    """
    Fetches table metadata for multiple datasets and generates a draft combined ODCS YAML using the AgentLLMClient.
    """
    import ast
    if isinstance(violations_context, str):
        try:
            # LLM might pass a stringified dict instead of an actual object
            violations_context = ast.literal_eval(violations_context)
        except Exception:
            try:
                violations_context = json.loads(violations_context)
            except Exception:
                violations_context = {}
                
    if not isinstance(violations_context, dict):
        violations_context = {}
        
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        datasets_metadata = []
        for dataset_id in dataset_ids:
            parts = dataset_id.split(".")
            if len(parts) != 3:
                logger.warning(f"Invalid dataset_id format: {dataset_id}. Expected catalog.schema.table")
                continue
                
            catalog_name, schema_name, table_name = parts
            
            # 1. Fetch Metadata
            catalog_desc = "unknown"
            schema_desc = "unknown"
            tags = {}
            tdq_score = "unknown"
            bdq_score = "unknown"
            
            try:
                catalog_info = provider.client.catalogs.get(name=catalog_name)
                catalog_desc = catalog_info.comment or "unknown"
            except Exception: pass
                
            try:
                schema_info = provider.client.schemas.get(full_name=f"{catalog_name}.{schema_name}")
                schema_desc = schema_info.comment or "unknown"
            except Exception: pass
            
            table_desc = "unknown"
            columns = []
            try:
                table_info = provider.client.tables.get(full_name=dataset_id)
                table_desc = table_info.comment or "unknown"
                if table_info.columns:
                    for col in table_info.columns:
                        columns.append({
                            "name": col.name,
                            "type": col.type_text,
                            "description": col.comment or "No description"
                        })
            except Exception: pass
            
            try:
                uc_tags = provider.client.entity_tag_assignments.list(entity_type='tables', entity_name=dataset_id)
                for tag_assign in uc_tags:
                    if tag_assign.tag_key:
                        tags[tag_assign.tag_key] = tag_assign.tag_value
            except Exception: pass
            
            try:
                if settings.DATABRICKS_WAREHOUSE_ID:
                    query = f"SELECT tdq_score, bdq_score FROM {settings.DATABRICKS_DATA_QUALITY_TABLE} WHERE dataset_id = '{dataset_id}' ORDER BY run_date DESC LIMIT 1"
                    response = provider.client.statement_execution.execute_statement(
                        statement=query,
                        warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                        wait_timeout="30s"
                    )
                    if response.result and response.result.data_array and len(response.result.data_array) > 0:
                        tdq_score = float(response.result.data_array[0][0])
                        bdq_score = float(response.result.data_array[0][1])
            except Exception: pass
            
            datasets_metadata.append({
                "dataset_id": dataset_id,
                "catalog": catalog_name,
                "schema": schema_name,
                "table": table_name,
                "catalog_desc": catalog_desc,
                "schema_desc": schema_desc,
                "table_desc": table_desc,
                "tags": tags,
                "tdq_score": tdq_score,
                "bdq_score": bdq_score,
                "columns": columns
            })
            
        # 2. Call LLM to draft ODCS
        client = AgentLLMClient()
        
        prompt = f"""
You are an expert Data Architect. Generate a valid Open Data Contract Standard (ODCS) v3 YAML document based on the following metadata for one or more datasets.
Since an ODCS document can represent a Data Product containing multiple datasets, generate a single ODCS YAML where the 'schema' array contains one entry for each dataset.
Output ONLY the raw YAML, with no markdown formatting or conversational filler.

Metadata for included datasets:
{json.dumps(datasets_metadata, indent=2)}

Known Policy Violations (Fix these in the generated YAML if possible!):
{json.dumps(violations_context.get('violation_reasons', []) if violations_context else [], indent=2)}

Example ODCS structure:
apiVersion: v3.1.0
kind: DataContract

domain: <extracted from tags or 'unknown'>
dataProduct: <infer a cohesive data product name covering these datasets>
version: 1.0.0
status: active
id: <generate a unique contract id>

authoritativeDefinitions:
- type: canonical
  url: https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/examples/all/full-example.odcs.yaml
  description: Canonical URL to the latest version of the contract.

description:
  purpose: <infer a combined purpose from the tables>
  limitations: <infer or 'None specified'>
  usage: <infer or 'Internal analytics'>

servers:
  - id: production
    type: databricks
    host: prod-workspace.cloud.databricks.com
    catalog: <catalog name - assuming same for all, if not, use the first or a representative one>
    schema: <schema name>

schema:
  # GENERATE ONE BLOCK LIKE THIS FOR EACH DATASET IN THE METADATA
  - id: <dataset physical name>_obj
    name: <dataset physical name>
    physicalName: <dataset physical name>
    physicalType: table
    businessName: <generate friendly business name from dataset physical name>
    description: <table description or infer from name>
    tags: <include relevant tags from metadata as list>
    quality:
      - id: technical_dq_threshold
        type: custom
        engine: acceldata
        description: "Technical data quality score threshold"
        mustBe: <infer reasonable threshold (e.g. 90, 95, 100) based on actual TDQ Score for THIS dataset if available, otherwise 100>
      - id: business_dq_threshold
        type: custom
        engine: acceldata
        description: "Business logic validation score threshold"
        mustBe: <infer reasonable threshold based on actual BDQ Score for THIS dataset if available, otherwise 100>
      - id: schema_drift
        type: custom
        engine: acceldata
        description: "Schema drift score must be 0%"
        mustBe: 0
    customProperties:
      - property: abac_required
        value: false
      - property: classification
        value: PII
    properties:
      # FOREACH column in the Columns metadata for this dataset, generate a property block like this:
      - id: <column name>
        name: <column name>
        physicalName: <column name>
        logicalType: <map from column type>
        description: <column description or infer from name>

price:
  priceAmount: 0.00
  priceCurrency: USD
  priceUnit: request

team:
  name: <infer from Owner group tag or 'unknown'>
  members:
    - username: <infer from Owner group tag or 'unknown'>
      role: Owner

roles:
  - role: data_engineer
    access: write
  - role: data_analyst
    access: read

slaProperties:
  - property: latency
    value: 1
    unit: d
"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await client.generate_response(messages=messages, temperature=0.2)
        content = response.get("content", "")
        
        content = content.replace("```yaml", "").replace("```yml", "").replace("```", "").strip()
        
        if "apiVersion:" in content and "kind: DataContract" in content:
            return content
            
        raise ValueError("LLM generated invalid ODCS format.")
            
    except Exception as e:
        logger.error(f"Failed to generate ODCS YAML in tool: {e}")
        # Fallback
        first_dataset = dataset_ids[0] if dataset_ids else "unknown"
        return f"domain: unknown\ndataProduct: product_for_{first_dataset}\nversion: 1.0.0\n"
