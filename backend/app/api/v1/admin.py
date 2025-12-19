"""
Admin API endpoints for form management.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.agents.forms_registry import (
    get_form_schema,
    save_form_schema,
    list_forms,
    list_form_versions,
    get_form_version
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class FormInfo(BaseModel):
    """Form information model."""
    path: str
    title: str
    filename: str


class FormVersionInfo(BaseModel):
    """Form version information model."""
    filename: str
    date: str
    is_active: bool


class FormSchemaResponse(BaseModel):
    """Form schema response model."""
    path: str
    form_schema: Dict[str, Any]  # Renamed from 'schema' to avoid shadowing BaseModel.schema()


class SaveFormRequest(BaseModel):
    """Request model for saving a form."""
    form_schema: Dict[str, Any]  # Renamed from 'schema' to avoid shadowing BaseModel.schema()
    create_version: bool = True


class WorkspaceInfo(BaseModel):
    """Workspace information model."""
    id: str
    name: str
    url: Optional[str] = None


class FeatureInfo(BaseModel):
    """Feature information model."""
    id: str
    name: str
    description: str
    category: str  # 'beta' or 'public_preview'
    enabled: bool


class WorkspaceFeaturesResponse(BaseModel):
    """Workspace features response model."""
    workspace_id: str
    features: List[FeatureInfo]


class UpdateFeatureRequest(BaseModel):
    """Request model for updating a feature."""
    enabled: bool


@router.get("/forms", response_model=List[FormInfo])
async def list_all_forms():
    """
    List all available forms.
    """
    try:
        forms = list_forms()
        return forms
    except Exception as e:
        logger.error(f"Error listing forms: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list forms: {str(e)}")


@router.get("/forms/{form_path:path}/versions", response_model=List[FormVersionInfo])
async def get_form_versions(form_path: str):
    """
    Get all versions of a form.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        versions = list_form_versions(form_path)
        return versions
    except Exception as e:
        logger.error(f"Error getting form versions for {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get form versions: {str(e)}")


@router.get("/forms/{form_path:path}", response_model=FormSchemaResponse)
async def get_form(form_path: str, version: Optional[str] = None):
    """
    Get a specific form schema.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        version: Optional version filename to get a specific version
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        if version:
            schema = get_form_version(form_path, version)
        else:
            schema = get_form_schema(form_path)
        
        if not schema:
            raise HTTPException(status_code=404, detail=f"Form not found: {form_path}")
        
        return FormSchemaResponse(path=form_path, form_schema=schema)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting form {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get form: {str(e)}")


@router.put("/forms/{form_path:path}", response_model=FormSchemaResponse)
async def save_form(form_path: str, request: SaveFormRequest):
    """
    Save or update a form schema.
    
    Args:
        form_path: Route path (e.g., '/paas/workspace-access')
        request: Form schema and options
    """
    try:
        # Ensure path starts with /
        if not form_path.startswith("/"):
            form_path = f"/{form_path}"
        
        # Validate schema has required structure
        if not isinstance(request.form_schema, dict):
            raise HTTPException(status_code=400, detail="Schema must be a JSON object")
        
        # Save the form
        success = save_form_schema(
            form_path,
            request.form_schema,
            create_version=request.create_version
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save form")
        
        # Return the saved form
        return FormSchemaResponse(path=form_path, form_schema=request.form_schema)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving form {form_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save form: {str(e)}")


# Mock data for workspaces and features
# Format: ws_{team}_{environment}
# Teams: enterprise, finance, sales, supplychain
# Environments: dev, test, stage, prod
MOCK_WORKSPACES = [
    {"id": "ws_enterprise_dev", "name": "ws_enterprise_dev", "url": "https://enterprise-dev.cloud.databricks.com"},
    {"id": "ws_enterprise_test", "name": "ws_enterprise_test", "url": "https://enterprise-test.cloud.databricks.com"},
    {"id": "ws_enterprise_stage", "name": "ws_enterprise_stage", "url": "https://enterprise-stage.cloud.databricks.com"},
    {"id": "ws_enterprise_prod", "name": "ws_enterprise_prod", "url": "https://enterprise-prod.cloud.databricks.com"},
    {"id": "ws_finance_dev", "name": "ws_finance_dev", "url": "https://finance-dev.cloud.databricks.com"},
    {"id": "ws_finance_test", "name": "ws_finance_test", "url": "https://finance-test.cloud.databricks.com"},
    {"id": "ws_finance_stage", "name": "ws_finance_stage", "url": "https://finance-stage.cloud.databricks.com"},
    {"id": "ws_finance_prod", "name": "ws_finance_prod", "url": "https://finance-prod.cloud.databricks.com"},
    {"id": "ws_sales_dev", "name": "ws_sales_dev", "url": "https://sales-dev.cloud.databricks.com"},
    {"id": "ws_sales_test", "name": "ws_sales_test", "url": "https://sales-test.cloud.databricks.com"},
    {"id": "ws_sales_stage", "name": "ws_sales_stage", "url": "https://sales-stage.cloud.databricks.com"},
    {"id": "ws_sales_prod", "name": "ws_sales_prod", "url": "https://sales-prod.cloud.databricks.com"},
    {"id": "ws_supplychain_dev", "name": "ws_supplychain_dev", "url": "https://supplychain-dev.cloud.databricks.com"},
    {"id": "ws_supplychain_test", "name": "ws_supplychain_test", "url": "https://supplychain-test.cloud.databricks.com"},
    {"id": "ws_supplychain_stage", "name": "ws_supplychain_stage", "url": "https://supplychain-stage.cloud.databricks.com"},
    {"id": "ws_supplychain_prod", "name": "ws_supplychain_prod", "url": "https://supplychain-prod.cloud.databricks.com"},
]

MOCK_FEATURES = [
    # Data Engineering & Ingestion - Public Preview
    {"id": "lakebase-autoscaling", "name": "Lakebase (Autoscaling)", "description": "New version of Lakebase with autoscaling compute, scale-to-zero, and instant restore (AWS)", "category": "public_preview"},
    {"id": "netsuite-connector", "name": "NetSuite Connector", "description": "Native ingestion from NetSuite2.com via API, CLI, or notebooks", "category": "public_preview"},
    {"id": "sftp-connector", "name": "SFTP Connector", "description": "Native connector to ingest data from SFTP servers", "category": "public_preview"},
    {"id": "foreachbatch-lakeflow", "name": "ForEachBatch for Lakeflow Pipelines", "description": "Support for micro-batch processing in Lakeflow Spark Declarative Pipelines using a ForEachBatch sink", "category": "public_preview"},
    {"id": "lakeflow-system-tables", "name": "Lakeflow System Tables", "description": "New columns added to Lakeflow system tables for better pipeline observability", "category": "public_preview"},
    {"id": "convert-foreign-tables", "name": "Convert Foreign Tables", "description": "Ability to convert foreign tables to Unity Catalog managed or external tables", "category": "public_preview"},
    {"id": "google-sheets-connector", "name": "Databricks Connector for Google Sheets", "description": "Enhanced features for reading/writing to Google Sheets", "category": "public_preview"},
    {"id": "native-xml-support", "name": "Native XML Support", "description": "Built-in XML file support without requiring external libraries", "category": "public_preview"},
    
    # Data Engineering & Ingestion - Beta
    {"id": "builtin-excel-support", "name": "Built-in Excel Support", "description": "Native support for reading Excel files directly into Spark DataFrames", "category": "beta"},
    {"id": "customizable-sharepoint-connector", "name": "Customizable SharePoint Connector", "description": "Offers granular control over schema inference and transformations compared to the managed connector", "category": "beta"},
    {"id": "runtime-18", "name": "Databricks Runtime 18.0 & 18.0 ML", "description": "The next major runtime version is currently in Beta", "category": "beta"},
    {"id": "git-cli-git-folders", "name": "Use Git CLI in Git Folders", "description": "Run standard Git commands directly from the Databricks web terminal", "category": "beta"},
    
    # AI, Machine Learning & Genie - Public Preview
    {"id": "assistant-agent-mode", "name": "Databricks Assistant Agent Mode", "description": "An autonomous agent that can break down complex tasks, run code, and fix errors automatically", "category": "public_preview"},
    {"id": "embedding-dashboards-external", "name": "Embedding Dashboards for External Users", "description": "Securely embed AI/BI dashboards into external applications for users without Databricks accounts", "category": "public_preview"},
    {"id": "genie-copilot-studio", "name": "Connect Genie Spaces to Copilot Studio", "description": "Integration allowing Microsoft Copilot Studio to query Databricks Genie spaces", "category": "public_preview"},
    {"id": "azure-genie-mcp-server", "name": "Azure Databricks Genie MCP Server", "description": "Connect Genie to Azure AI Foundry", "category": "public_preview"},
    {"id": "mcp-servers-marketplace", "name": "List MCP Servers in Marketplace", "description": "Model Context Protocol (MCP) servers are now listed in the Marketplace", "category": "public_preview"},
    {"id": "ai-parse-document", "name": "ai_parse_document", "description": "A new AI function to extract structured data from documents", "category": "public_preview"},
    
    # AI, Machine Learning & Genie - Beta
    {"id": "sql-mcp-server", "name": "SQL MCP Server", "description": "Early access to SQL-based Model Context Protocol servers", "category": "beta"},
    {"id": "qwen3-next-instruct", "name": "Alibaba Cloud Qwen3-Next Instruct", "description": "Availability of this model as a Databricks-hosted model", "category": "beta"},
    
    # Governance, Security & Unity Catalog - Public Preview
    {"id": "delta-sharing-iceberg", "name": "Delta Sharing to Iceberg Clients", "description": "Share tables/views directly to external Iceberg clients (Snowflake, Trino, etc.) with zero-copy access", "category": "public_preview"},
    {"id": "abac-delta-sharing", "name": "ABAC for Delta Sharing", "description": "Attribute-Based Access Control (ABAC) policies can now be applied to shared assets", "category": "public_preview"},
    {"id": "automatic-token-expiration-emails", "name": "Automatic Token Expiration Emails", "description": "System sends emails to users 7 days before their Personal Access Tokens (PATs) expire", "category": "public_preview"},
    {"id": "data-classification", "name": "Data Classification", "description": "Automated detection and tagging of sensitive data (PII) across Unity Catalog", "category": "public_preview"},
    {"id": "compatibility-mode", "name": "Compatibility Mode", "description": "Generates a read-only version of UC managed tables that syncs with the original for broader tool compatibility", "category": "public_preview"},
    {"id": "tisax-c5-compliance", "name": "TISAX & C5 Compliance Controls", "description": "Expanded compliance standards availability", "category": "public_preview"},
    
    # Governance, Security & Unity Catalog - Beta
    {"id": "jdbc-uc-connection", "name": "JDBC Unity Catalog Connection", "description": "A new JDBC driver specifically optimized for Unity Catalog connectivity", "category": "beta"},
    {"id": "context-based-ingress-control", "name": "Context-Based Ingress Control", "description": "Enhanced network security rules based on connection context", "category": "beta"},
    
    # Platform & Compute - Public Preview
    {"id": "serverless-workspaces", "name": "Serverless Workspaces", "description": "Fully serverless workspace environment (no customer-managed VPC required)", "category": "public_preview"},
    {"id": "configure-compute-apps", "name": "Configure Compute for Databricks Apps", "description": "Ability to select specific compute instance sizes for Databricks Apps", "category": "public_preview"},
    {"id": "migrate-community-free", "name": "Migrate Community to Free Edition", "description": "Path to migrate from Community Edition to the new Free Edition tier", "category": "public_preview"},
    {"id": "dashboard-tags-certification", "name": "Dashboard Tags & Certification", "description": "Capability to tag dashboards and Genie spaces for better organization and governance", "category": "public_preview"},
    
    # Databricks SQL & BI - Public Preview
    {"id": "sql-alerts", "name": "Databricks SQL Alerts", "description": "A revamped alerting experience in the SQL editor", "category": "public_preview"},
    {"id": "sql-v2025-30", "name": "Databricks SQL v2025.30", "description": "The latest SQL warehouse version containing new functions and performance tweaks", "category": "public_preview"},
    {"id": "unified-runs-list", "name": "Unified Runs List", "description": "A single view to see all query and job runs across the workspace", "category": "public_preview"},
]

# In-memory storage for feature states (workspace_id -> feature_id -> enabled)
_feature_states: Dict[str, Dict[str, bool]] = {}


@router.get("/workspaces", response_model=List[WorkspaceInfo])
async def list_workspaces():
    """
    List all available workspaces.
    """
    try:
        return [WorkspaceInfo(**ws) for ws in MOCK_WORKSPACES]
    except Exception as e:
        logger.error(f"Error listing workspaces: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list workspaces: {str(e)}")


@router.get("/workspaces/{workspace_id}/features", response_model=WorkspaceFeaturesResponse)
async def get_workspace_features(workspace_id: str):
    """
    Get feature states for a specific workspace.
    
    Args:
        workspace_id: Workspace identifier
    """
    try:
        # Check if workspace exists
        workspace = next((ws for ws in MOCK_WORKSPACES if ws["id"] == workspace_id), None)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")
        
        # Get feature states for this workspace (default to False if not set)
        workspace_states = _feature_states.get(workspace_id, {})
        
        # Build feature list with current states
        features = []
        for feature in MOCK_FEATURES:
            enabled = workspace_states.get(feature["id"], False)
            features.append(FeatureInfo(
                id=feature["id"],
                name=feature["name"],
                description=feature["description"],
                category=feature["category"],
                enabled=enabled
            ))
        
        return WorkspaceFeaturesResponse(workspace_id=workspace_id, features=features)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workspace features for {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get workspace features: {str(e)}")


@router.put("/workspaces/{workspace_id}/features/{feature_id}", response_model=FeatureInfo)
async def update_workspace_feature(workspace_id: str, feature_id: str, request: UpdateFeatureRequest):
    """
    Update a feature state for a specific workspace.
    
    Args:
        workspace_id: Workspace identifier
        feature_id: Feature identifier
        request: Feature update request
    """
    try:
        # Check if workspace exists
        workspace = next((ws for ws in MOCK_WORKSPACES if ws["id"] == workspace_id), None)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")
        
        # Check if feature exists
        feature = next((f for f in MOCK_FEATURES if f["id"] == feature_id), None)
        if not feature:
            raise HTTPException(status_code=404, detail=f"Feature not found: {feature_id}")
        
        # Update feature state
        if workspace_id not in _feature_states:
            _feature_states[workspace_id] = {}
        _feature_states[workspace_id][feature_id] = request.enabled
        
        return FeatureInfo(
            id=feature["id"],
            name=feature["name"],
            description=feature["description"],
            category=feature["category"],
            enabled=request.enabled
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workspace feature {workspace_id}/{feature_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update workspace feature: {str(e)}")

