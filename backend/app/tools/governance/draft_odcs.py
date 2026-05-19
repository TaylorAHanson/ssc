from typing import Dict, Any, Optional, List
import json
import logging
import yaml
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.model_serving.agent_llm import AgentLLMClient
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

logger = logging.getLogger(__name__)

async def fetch_datasets_metadata(dataset_ids: List[str]) -> List[Dict[str, Any]]:
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
        
        try:
            catalog_info = provider.client.catalogs.get(name=catalog_name)
            catalog_desc = catalog_info.comment or "unknown"
        except Exception: pass
            
        try:
            schema_info = provider.client.schemas.get(full_name=f"{catalog_name}.{schema_name}")
            schema_desc = schema_info.comment or "unknown"
        except Exception: pass
        
        table_desc = "unknown"
        table_type = "TABLE"
        columns = []
        try:
            table_info = provider.client.tables.get(full_name=dataset_id)
            table_desc = table_info.comment or "unknown"
            if hasattr(table_info, 'table_type') and table_info.table_type:
                table_type = str(table_info.table_type.value) if hasattr(table_info.table_type, 'value') else str(table_info.table_type)
            if table_info.columns:
                for col in table_info.columns:
                    columns.append({
                        "name": col.name,
                        "type": col.type_text,
                        "description": col.comment or "No description"
                    })
        except Exception as e:
            logger.warning(f"Skipping table {dataset_id} during metadata fetch because it could not be accessed: {e}")
            continue
        
        try:
            uc_tags = provider.client.entity_tag_assignments.list(entity_type='table', entity_name=dataset_id)
            for tag_assign in uc_tags:
                if tag_assign.tag_key:
                    tags[tag_assign.tag_key] = tag_assign.tag_value
        except Exception: pass
        
        # 2. Fetch Lineage
        upstream_tables = set()
        downstream_tables = set()
        try:
            lineage_resp = provider.client.api_client.do(
                "GET", 
                f"/api/2.0/lineage-tracking/table-lineage?table_name={dataset_id}&include_entity_lineage=true"
            )
            if lineage_resp:
                for u in lineage_resp.get("upstreams", []):
                    table_info = u.get("tableInfo")
                    if table_info and "name" in table_info:
                        upstream_tables.add(table_info["name"])
                        
                for d in lineage_resp.get("downstreams", []):
                    table_info = d.get("tableInfo")
                    if table_info and "name" in table_info:
                        downstream_tables.add(table_info["name"])
        except Exception as e:
            logger.warning(f"Failed to fetch lineage for {dataset_id}: {e}")
        
        datasets_metadata.append({
            "dataset_id": dataset_id,
            "catalog": catalog_name,
            "schema": schema_name,
            "table": table_name,
            "catalog_desc": catalog_desc,
            "schema_desc": schema_desc,
            "table_desc": table_desc,
            "table_type": table_type,
            "tags": tags,
            "columns": columns,
            "upstream_tables": sorted(list(upstream_tables)),
            "downstream_tables": sorted(list(downstream_tables))
        })
        
    return datasets_metadata

class DraftOdcsInput(BaseModel):
    dataset_ids: List[str] = Field(..., description="The list of fully qualified dataset IDs (e.g., ['catalog.schema.table1', 'catalog.schema.table2'])")
    violations_context: Optional[Dict[str, Any]] = Field(None, description="Optional. The raw OPA violations context indicating what is currently failing certification.")
    existing_odcs_yaml: Optional[str] = Field(None, description="Optional. Existing ODCS YAML content to preserve manual edits.")
    pre_fetched_metadata: Optional[List[Dict[str, Any]]] = Field(None, description="Optional. Pre-fetched metadata to avoid duplicate API calls.")

@tool(
    name="draft_odcs_contract",
    description="Drafts an Open Data Contract Standard (ODCS) v3 YAML document for a given set of datasets by fetching their metadata and using AI to generate the combined structure. Important: Always use this tool to generate the 'odcs_yaml' parameter before calling execute_workflow for data_certification.",
    args_schema=DraftOdcsInput
)
async def draft_odcs_contract(
    dataset_ids: List[str], 
    violations_context: Optional[Any] = None, 
    existing_odcs_yaml: Optional[str] = None,
    pre_fetched_metadata: Optional[List[Dict[str, Any]]] = None
) -> str:
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
        datasets_metadata = pre_fetched_metadata
        if not datasets_metadata:
            datasets_metadata = await fetch_datasets_metadata(dataset_ids)
            
        # 2. Call LLM to draft ODCS
        client = AgentLLMClient()
        
        existing_contract_instructions = ""
        if existing_odcs_yaml:
            try:
                parsed_yaml = yaml.safe_load(existing_odcs_yaml)
                if "schema" in parsed_yaml and isinstance(parsed_yaml["schema"], list):
                    valid_names = {m["dataset_id"] for m in datasets_metadata}
                    valid_short_names = {m["table"] for m in datasets_metadata}
                    
                    filtered_schema = []
                    for table_def in parsed_yaml["schema"]:
                        phys_name = table_def.get("physicalName", "")
                        if phys_name in valid_names or phys_name in valid_short_names:
                            filtered_schema.append(table_def)
                            
                    parsed_yaml["schema"] = filtered_schema
                    existing_odcs_yaml = yaml.dump(parsed_yaml, sort_keys=False)
            except Exception as e:
                logger.warning(f"Failed to pre-prune existing YAML: {e}")

            existing_contract_instructions = f"""
CRITICAL INSTRUCTION: You are UPDATING an existing Open Data Contract Standard (ODCS) document.
You MUST preserve all existing manual customizations (descriptions, roles, thresholds, custom properties, slas) EXACTLY as they are in the existing YAML.
Only ADD new tables or columns found in the Metadata that are missing from the existing YAML. Do NOT overwrite existing human-written descriptions or rules.
CRITICAL: You MUST REMOVE any tables from the `schema` array that are NOT present in the provided Metadata. If a table is in the existing YAML but not in the Metadata, it has been deleted or untagged, so DELETE IT from the YAML.

EXISTING ODCS YAML TO UPDATE:
{existing_odcs_yaml}
"""

        prompt = f"""
You are an expert Data Architect. Generate a valid Open Data Contract Standard (ODCS) v3 YAML document based on the following metadata for one or more datasets.
Since an ODCS document can represent a Data Product containing multiple datasets, generate a single ODCS YAML where the 'schema' array contains one entry for each dataset.
Output ONLY the raw YAML, with no markdown formatting or conversational filler.

CRITICAL: ONLY generate `schema` entries for the specific datasets listed as top-level objects in the provided Metadata. DO NOT generate schema entries for any tables listed in the `upstream_tables` or `downstream_tables` arrays. Those are provided for lineage context only.

{existing_contract_instructions}

Metadata for included datasets (includes new or updated information):
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
    catalog: <catalog name>
    schema: <schema name>
    physicalType: table
    businessName: <generate friendly business name from dataset physical name>
    description: <table description or infer from name>
    tags: <include relevant tags from metadata as list>
    customProperties:
      - property: abac_required
        value: false
      - property: classification
        value: PII
      - property: upstream_tables
        value: <list of upstream tables from metadata, or []>
      - property: downstream_tables
        value: <list of downstream tables from metadata, or []>
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
        logger.info(f"Sending request to LLM for datasets: {dataset_ids}")
        logger.debug(f"LLM Prompt length: {len(prompt)} chars")
        
        response = await client.generate_response(
            messages=messages, 
            temperature=0.2,
            max_tokens=8000
        )
        content = response.get("content", "")
        
        logger.info(f"Received response from LLM for datasets: {dataset_ids}")
        logger.debug(f"LLM Response snippet: {content[:200]}...")
        
        content = content.replace("```yaml", "").replace("```yml", "").replace("```", "").strip()
        
        if "apiVersion:" in content and "kind: DataContract" in content:
            # Post-LLM Pruning: Ensure the LLM didn't hallucinate or re-add tables from upstream/downstream lists
            try:
                final_yaml = yaml.safe_load(content)
                if "schema" in final_yaml and isinstance(final_yaml["schema"], list):
                    valid_names = {m["dataset_id"] for m in datasets_metadata}
                    valid_short_names = {m["table"] for m in datasets_metadata}
                    
                    filtered_schema = []
                    for table_def in final_yaml["schema"]:
                        phys_name = table_def.get("physicalName", "")
                        if phys_name in valid_names or phys_name in valid_short_names:
                            filtered_schema.append(table_def)
                        else:
                            logger.info(f"Post-LLM Pruning: Removing invalid/hallucinated table {phys_name} from final YAML.")
                            
                    final_yaml["schema"] = filtered_schema
                    # Dump with sort_keys=False to preserve order, and default_flow_style=False for block format
                    content = yaml.dump(final_yaml, sort_keys=False, default_flow_style=False)
            except Exception as e:
                logger.warning(f"Failed to post-prune LLM YAML: {e}")
                
            return content
            
        raise ValueError("LLM generated invalid ODCS format.")
            
    except Exception as e:
        logger.error(f"Failed to generate ODCS YAML in tool: {e}")
        # Fallback
        first_dataset = dataset_ids[0] if dataset_ids else "unknown"
        return f"domain: unknown\ndataProduct: product_for_{first_dataset}\nversion: 1.0.0\n"
