import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from app.core.config import settings
from app.core.exceptions import PermanentError
from app.db.allowlist import AllowlistModel
from app.db.session import get_db
from app.providers.opa.client import OpaProvider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)

class EvaluatePolicyInput(BaseModel):
    workspace: str = Field(..., description="The workspace ID or name where the resource lives (e.g. 'ws-enterprise-prod').")
    resource_type: str = Field(..., description="The type of resource (e.g. 'app', 'cluster', 'job', 'notebook').")
    resource_id: str = Field(..., description="The normalized ID or name of the resource.")
    
@tool(
    name="evaluate_policy",
    description="Do a dry-run evaluation of governance policies using OPA to see if an action or resource is allowed in a workspace.",
    args_schema=EvaluatePolicyInput
)
async def evaluate_policy(workspace: str, resource_type: str, resource_id: str) -> Dict[str, Any]:
    """Evaluate whether a resource is allowed in a given workspace using OPA and the Allowlist database."""
    try:
        # Determine workspace type based on name for OPA context
        workspace_type = "enterprise" if "enterprise" in workspace else "domain"
        
        db = next(get_db())
        try:
            # 1. Fetch Allowlist Context from DB
            allowlist_records = []
            db_entries = db.query(AllowlistModel).filter(AllowlistModel.workspace == workspace).all()
            for entry in db_entries:
                allowlist_records.append({
                    "resource_id": entry.resource_id,
                    "status": entry.status,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                    "justification": entry.justification
                })
        finally:
            db.close()

        # 2. Evaluate with OPA
        import glob
        import os
        opa_provider = OpaProvider(settings.opa_provider_config())
        input_data = {
            "workspace": {"name": workspace, "type": workspace_type},
            "resource": {"id": resource_id, "type": resource_type},
            "request_time": datetime.now(timezone.utc).isoformat(),
            "allowlist_records": allowlist_records
        }
        
        policy_files = glob.glob(os.path.join("policies", "*.rego"))
        violations = []
        
        for policy_path in policy_files:
            policy_name = os.path.basename(policy_path).replace(".rego", "")
            query = f"data.databricks.governance.{policy_name}"
            
            result = await opa_provider.evaluate(
                policy_path=policy_path,
                query=query,
                input_data=input_data
            )
            
            if result.get("is_violation"):
                violations.append({
                    "action": result.get("action", "KILL"),
                    "reason": result.get("reason", "Unknown violation"),
                    "severity": result.get("severity"),
                    "policy": policy_name
                })
        
        if not violations:
            return {
                "allowed": True,
                "action": "ALLOW",
                "reason": "Resource complied with all policies.",
                "is_violation": False,
                "severity": "NONE"
            }
            
        first = violations[0]
        return {
            "allowed": first["action"] not in ["KILL", "BLOCK", "DELETE"],
            "action": first["action"],
            "reason": first["reason"],
            "is_violation": True,
            "severity": first["severity"],
            "all_violations": violations
        }
    except PermanentError as e:
        logger.warning("Policy evaluation unavailable (configuration or OPA): %s", e)
        return {"error": str(e)}
    except Exception as e:
        logger.error("Error evaluating policy: %s", e)
        return {"error": str(e)}

class CheckAllowlistInput(BaseModel):
    resource_id: str = Field(..., description="The normalized ID or name of the resource.")
    workspace: Optional[str] = Field(None, description="The workspace ID or name where the resource lives.")

@tool(
    name="check_allowlist_status",
    description="Check the database to see if there is an active, pending, or expired governance exception for a specific resource.",
    args_schema=CheckAllowlistInput
)
async def check_allowlist_status(resource_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Query the allowlist database for a specific resource to check its exception status."""
    db = next(get_db())
    try:
        query = db.query(AllowlistModel).filter(AllowlistModel.resource_id == resource_id)
        if workspace:
            query = query.filter(AllowlistModel.workspace == workspace)
            
        entries = query.all()
        
        if not entries:
            return {"status": "not_found", "message": f"No allowlist exceptions found for resource '{resource_id}'"}
            
        results = []
        for entry in entries:
            results.append({
                "id": entry.id,
                "workspace": entry.workspace,
                "status": entry.status,
                "justification": entry.justification,
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                "request_id": entry.request_id
            })
            
        return {"status": "found", "entries": results}
    except Exception as e:
        logger.error(f"Error checking allowlist status: {e}")
        return {"error": str(e)}
    finally:
        db.close()
