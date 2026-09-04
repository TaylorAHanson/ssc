import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
import urllib.request
import urllib.error

from app.tools.mcp import tool
from app.model_serving.agent_llm import AgentLLMClient

logger = logging.getLogger(__name__)

class DraftOdpsInput(BaseModel):
    odcs_yamls: List[str] = Field(..., description="List of ODCS YAML contents to be included as data products")
    openapi_urls: Optional[List[str]] = Field(None, description="Optional. List of publicly accessible HTTP(S) URLs to raw OpenAPI JSON/YAML specifications.")
    product_name: str = Field(..., description="The name of the Open Data Product")

@tool(
    name="draft_odps_document",
    description="Drafts an Open Data Product Specification (ODPS) v4.1 YAML document by combining ODCS data contracts and OpenAPI specs using the LLM.",
    args_schema=DraftOdpsInput
)
async def draft_odps_document(
    odcs_yamls: List[str], 
    product_name: str,
    openapi_urls: Optional[List[str]] = None
) -> str:
    """
    Combines existing ODCS YAMLs and optional OpenAPI specs into an ODPS document using AgentLLMClient.
    """
    openapi_content = ""
    if openapi_urls:
        for url in openapi_urls:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    # truncate if too large
                    if len(content) > 15000:
                        content = content[:15000] + "\n...[truncated]"
                    openapi_content += f"\n--- OpenAPI Spec: {url} ---\n{content}\n"
            except Exception as e:
                logger.warning(f"Failed to fetch OpenAPI spec from {url}: {e}")
                openapi_content += f"\n--- OpenAPI Spec: {url} ---\nFailed to fetch: {e}\n"
                
    if not openapi_content:
        openapi_content = "None provided."

    odcs_combined = ""
    for i, yaml_str in enumerate(odcs_yamls):
        odcs_combined += f"\n--- ODCS Document {i+1} ---\n{yaml_str}\n"

    try:
        client = AgentLLMClient()
        
        prompt = f"""
You are an expert Data Architect. Generate a valid Open Data Product Specification (ODPS) v4.1 YAML document.
This ODPS document should combine and reference the following ODCS data contracts and (if provided) the OpenAPI specification.
Extract relevant metadata, schemas, and descriptions from the provided ODCS documents to form the data product's unified specification.

Output ONLY the raw YAML, with no markdown formatting or conversational filler.

Product Name: {product_name}

OpenAPI Specification Content (or status):
{openapi_content}

ODCS Documents to include:
{odcs_combined}

Example ODPS v4.1 structure (combine datasets under data product elements, add API endpoint info if OpenAPI is present):
schema: "https://opendataproducts.org/v4.1/schema/odps.yaml"
version: "4.1"
product:
  details:
    en:
      name: "{product_name}"
      productID: "dp-{product_name.lower().replace(' ', '-')}"
      visibility: "organisation"
      status: "draft"
      type: "dataset"
      description: "A comprehensive data product containing multiple data assets and services."
      productVersion: "1.0.0"
      governanceProfile: "structured"
      portfolioPriority: "medium"
  contract:
    id: "contract-1"
    type: "ODCS"
    contractVersion: "3.1.0"
    contractURL: "https://example.com/contract.yaml"
  productStrategy:
    status: "Planned"
    startDate: "2026-01-01"
    endDate: "2026-12-31"
    objectives:
      - en: "Enable smarter analytics"
  support:
    phoneNumber: "+123456789"
    email: "support@example.com"
  license:
    en:
      scope: "internal"
      termination: "none"
      governance: "strict"
  dataHolder:
    en:
      legalName: "Your Company"
      email: "contact@example.com"
      URL: "https://example.com"
      streetAddress: "123 Main St"
      postalCode: "12345"
      addressLocality: "Anytown"
      addressCountry: "US"
"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await client.generate_response(messages=messages, temperature=0.2)
        content = response.get("content", "")
        
        content = content.replace("```yaml", "").replace("```yml", "").replace("```", "").strip()
        
        if "schema" in content and "version" in content and "product" in content:
            return content
            
        raise ValueError("LLM generated invalid ODPS format.")
            
    except Exception as e:
        logger.error(f"Failed to generate ODPS YAML in tool: {e}")
        # Fallback
        return f'schema: "https://opendataproducts.org/v4.1/schema/odps.yaml"\nversion: "4.1"\nproduct:\n  details:\n    en:\n      name: "{product_name}"\n'
