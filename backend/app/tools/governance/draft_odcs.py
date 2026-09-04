from typing import Dict, Any, Optional, List
import json
import logging
import yaml
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.model_serving.agent_llm import AgentLLMClient
from app.core.config import settings
from app.core.exceptions import RetryableError

logger = logging.getLogger(__name__)


def _extract_odcs_yaml(content: str) -> str:
    """Strip code fences and any leading prose so we hand clean YAML to the parser.

    LLMs frequently wrap the contract in ```yaml fences or prepend a sentence of
    explanation; both break ``yaml.safe_load``. We drop the fences and trim
    anything before the document actually starts (``apiVersion:``).
    """
    content = content.replace("```yaml", "").replace("```yml", "").replace("```", "").strip()
    idx = content.find("apiVersion:")
    if idx > 0:
        content = content[idx:]
    return content


def _yaml_error_context(content: str, ye: Exception, radius: int = 3) -> str:
    """Render the lines around a YAML parse error (using its ``problem_mark``).

    This is what turns an opaque "could not find expected ':'" into an
    actionable log line showing exactly which generated line is malformed.
    """
    mark = getattr(ye, "problem_mark", None)
    lines = content.splitlines()
    if mark is None:
        return "\n".join(f"  {i + 1:4d}| {ln}" for i, ln in enumerate(lines[:12]))
    lo = max(0, mark.line - radius)
    hi = min(len(lines), mark.line + radius + 1)
    out = []
    for i in range(lo, hi):
        prefix = ">>" if i == mark.line else "  "
        out.append(f"{prefix} {i + 1:4d}| {lines[i]}")
    return "\n".join(out)

async def fetch_datasets_metadata(dataset_ids: List[str]) -> List[Dict[str, Any]]:
    # Runs as the governance SP, matching the discovery that produced these
    # dataset ids — otherwise drafting would read metadata under an identity
    # that may not even be able to see the tables discovery just found.
    #
    # Metadata comes from information_schema rather than the SDK's
    # catalogs.get / schemas.get / tables.get, which demand SELECT (or
    # ownership) on every table. BROWSE is enough for information_schema, and
    # BROWSE is the right grant for a scanner that only ever describes data.
    # See app.providers.databricks.uc_metadata.
    from app.core.workspaces import get_governance_uc_provider
    from app.providers.databricks.uc_metadata import fetch_uc_metadata

    provider = get_governance_uc_provider()

    metadata_batch = fetch_uc_metadata(provider.client, dataset_ids, settings.DATABRICKS_WAREHOUSE_ID)

    datasets_metadata = []
    inaccessible: List[str] = []
    for dataset_id in dataset_ids:
        parts = dataset_id.split(".")
        if len(parts) != 3:
            logger.warning(f"Invalid dataset_id format: {dataset_id}. Expected catalog.schema.table")
            continue
            
        catalog_name, schema_name, table_name = parts

        table_meta = metadata_batch.get(dataset_id)
        if table_meta is None:
            logger.warning(
                "Skipping table %s during metadata fetch: information_schema returned nothing "
                "for it, so it either does not exist or the governance service principal lacks "
                "BROWSE on %s.", dataset_id, catalog_name,
            )
            inaccessible.append(dataset_id)
            continue

        catalog_desc = table_meta.catalog_description or "unknown"
        schema_desc = table_meta.schema_description or "unknown"
        tags = dict(table_meta.tags)
        table_desc = table_meta.comment or "unknown"
        table_type = table_meta.table_type or "TABLE"
        columns = [
            {
                "name": col.name,
                "type": col.data_type,
                "description": col.comment or "No description",
            }
            for col in table_meta.columns
        ]

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

    # Skipped tables are omitted from the drafted contract, and a contract that
    # declares nothing is not a small problem — it's one that no certification
    # rule can evaluate. Report the gap once, as an error, instead of leaving it
    # as N per-table warnings that scan like routine noise.
    if inaccessible:
        shown = ", ".join(inaccessible[:10])
        more = f" (+{len(inaccessible) - 10} more)" if len(inaccessible) > 10 else ""
        logger.error(
            "Contract metadata fetch: no metadata available for %d of %d table(s); they will be "
            "OMITTED from the drafted contract. Grant the governance service principal BROWSE on "
            "the parent catalog (GRANT BROWSE ON CATALOG <catalog> TO <sp>) if these tables exist: "
            "%s%s", len(inaccessible), len(dataset_ids), shown, more,
        )
    if metadata_batch.failed_catalogs:
        logger.error(
            "Contract metadata fetch: information_schema query FAILED for catalog(s) %s. This is a "
            "hard error rather than a visibility gap — the drafted contracts for anything in those "
            "catalogs will be incomplete.", metadata_batch.failed_catalogs,
        )

    return datasets_metadata

class DraftOdcsInput(BaseModel):
    dataset_ids: List[str] = Field(..., description="The list of fully qualified dataset IDs (e.g., ['catalog.schema.table1', 'catalog.schema.table2'])")
    violations_context: Optional[Dict[str, Any]] = Field(None, description="Optional. The raw OPA violations context indicating what is currently failing certification.")
    existing_odcs_yaml: Optional[str] = Field(None, description="Optional. Existing ODCS YAML content to preserve manual edits.")
    pre_fetched_metadata: Optional[List[Dict[str, Any]]] = Field(None, description="Optional. Pre-fetched metadata to avoid duplicate API calls.")

@tool(
    name="draft_odcs_contract",
    description="Draft an Open Data Contract Standard (ODCS) v3 YAML document for Unity Catalog datasets by fetching table schemas and generating contract structure.",
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
            is_valid_yaml = False
            try:
                parsed_yaml = yaml.safe_load(existing_odcs_yaml)
                is_valid_yaml = True
                if "schema" in parsed_yaml and isinstance(parsed_yaml["schema"], list):
                    valid_names = {m["dataset_id"] for m in datasets_metadata}
                    
                    filtered_schema = []
                    for table_def in parsed_yaml["schema"]:
                        phys_name = table_def.get("physicalName", "")
                        table_catalog = table_def.get("catalog", "")
                        table_schema = table_def.get("schema", "")
                        
                        if table_catalog and table_schema and "." not in phys_name:
                            full_name = f"{table_catalog}.{table_schema}.{phys_name}"
                        else:
                            full_name = phys_name
                            
                        if full_name in valid_names or phys_name in valid_names:
                            filtered_schema.append(table_def)
                        else:
                            logger.info(f"Pre-LLM Pruning: Removing invalid table {full_name} from existing YAML.")
                            
                    # Programmatically add missing tables to ensure the LLM doesn't skip them
                    existing_table_names = {t.get("physicalName", "") for t in filtered_schema}
                    for m in datasets_metadata:
                        m_full = m["dataset_id"]
                        m_short = m["table"]
                        if m_full not in existing_table_names and m_short not in existing_table_names:
                            logger.info(f"Pre-LLM Injection: Adding missing table {m_full} to existing YAML stub.")
                            filtered_schema.append({
                                "id": f"{m_short}_obj",
                                "name": m_short,
                                "physicalName": m_full,
                                "catalog": m["catalog"],
                                "schema": m["schema"],
                                "physicalType": "table",
                                "description": m.get("table_desc", "No description"),
                                "properties": [] # LLM will fill this in
                            })
                            
                    parsed_yaml["schema"] = filtered_schema
                    existing_odcs_yaml = yaml.dump(parsed_yaml, sort_keys=False)
            except Exception as e:
                logger.warning(f"Failed to parse or pre-prune existing YAML. Discarding it to force a clean regeneration: {e}")
                existing_odcs_yaml = None

            if is_valid_yaml and existing_odcs_yaml:
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

CRITICAL INSTRUCTIONS:
1. ONLY generate `schema` entries for the specific datasets listed as top-level objects in the provided Metadata. 
2. DO NOT generate schema entries for any tables listed in the `upstream_tables` or `downstream_tables` arrays. Those are provided for lineage context only.
3. You MUST ensure that EVERY dataset listed in the Metadata has a corresponding entry in the `schema` array. Do not skip any datasets.
4. The following datasets MUST be present in the final YAML `schema` array: {', '.join([m["dataset_id"] for m in datasets_metadata])}

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
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await client.generate_response(
                    messages=messages, 
                    temperature=0.2,
                    max_tokens=8000
                )
                content = response.get("content", "")
                
                logger.info(f"Received response from LLM for datasets: {dataset_ids} (Attempt {attempt + 1})")
                
                content = _extract_odcs_yaml(content)

                if "apiVersion:" in content and "kind: DataContract" in content:
                    # Step 1: parse. On failure, log the EXACT offending line(s) so a
                    # bad generation can be diagnosed from logs alone (see screenshot
                    # bug reports), then re-prompt the model with that context.
                    try:
                        final_yaml = yaml.safe_load(content)
                    except yaml.YAMLError as ye:
                        logger.error(
                            "ODCS YAML parse failed on attempt %d for %s: %s\nOffending YAML context:\n%s",
                            attempt + 1, dataset_ids, ye, _yaml_error_context(content, ye),
                        )
                        logger.debug("ODCS raw LLM content (attempt %d):\n%s", attempt + 1, content)
                        if attempt == max_retries - 1:
                            raise ValueError(f"LLM generated invalid YAML after {max_retries} attempts: {ye}")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": (
                            f"The YAML you generated is invalid and failed to parse with this error:\n{ye}\n\n"
                            f"The problem is around here:\n{_yaml_error_context(content, ye)}\n\n"
                            "Return ONLY corrected, valid YAML (no prose, no code fences). "
                            "Quote any string value that contains a colon, '#', '@', or other special "
                            "characters, and use a block scalar ('|') for any multi-line description."
                        )})
                        continue

                    # Step 2: post-LLM pruning — drop any table the model hallucinated
                    # or re-added from upstream/downstream lists.
                    try:
                        if isinstance(final_yaml, dict) and "schema" in final_yaml and isinstance(final_yaml["schema"], list):
                            valid_names = {m["dataset_id"] for m in datasets_metadata}

                            filtered_schema = []
                            for table_def in final_yaml["schema"]:
                                phys_name = table_def.get("physicalName", "")
                                table_catalog = table_def.get("catalog", "")
                                table_schema = table_def.get("schema", "")

                                if table_catalog and table_schema and "." not in phys_name:
                                    full_name = f"{table_catalog}.{table_schema}.{phys_name}"
                                else:
                                    full_name = phys_name

                                if full_name in valid_names or phys_name in valid_names:
                                    filtered_schema.append(table_def)
                                else:
                                    logger.info(f"Post-LLM Pruning: Removing invalid/hallucinated table {full_name} from final YAML.")

                            final_yaml["schema"] = filtered_schema
                            # Dump with sort_keys=False to preserve order, and default_flow_style=False for block format
                            content = yaml.dump(final_yaml, sort_keys=False, default_flow_style=False)
                        return content
                    except Exception as e:
                        logger.warning(f"Failed to post-prune LLM YAML: {e}")
                        return content

                # Markers missing — the model returned prose or a wrong shape.
                # Re-prompt with explicit feedback instead of silently re-calling
                # with the same messages (which tended to reproduce the failure).
                logger.warning(
                    "LLM response missing ODCS markers (apiVersion/kind) on attempt %d for %s. Head: %r",
                    attempt + 1, dataset_ids, content[:200],
                )
                if attempt == max_retries - 1:
                    raise ValueError("LLM generated invalid ODCS format.")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": (
                    "Your response was not a valid ODCS contract. It MUST be pure YAML that begins with "
                    "'apiVersion:' and includes 'kind: DataContract'. Return ONLY the YAML — no prose, no code fences."
                )})
                continue
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                logger.warning(f"LLM call failed on attempt {attempt + 1}: {e}")
            
    except Exception as e:
        logger.error(f"Failed to generate ODCS YAML in tool: {e}")
        # Fallback
        first_dataset = dataset_ids[0] if dataset_ids else "unknown"
        return f"domain: unknown\ndataProduct: product_for_{first_dataset}\nversion: 1.0.0\n"
