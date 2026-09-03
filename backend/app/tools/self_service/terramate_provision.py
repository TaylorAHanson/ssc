"""
Conversational agent tool for initiating Terramate infrastructure provisioning requests.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.providers.terramate.client import TerramateProvider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class TerramateProvisionInput(BaseModel):
    request_type: str = Field(
        ...,
        description="The resource type to provision via Terramate (e.g. 'workspace', 'schema').",
    )
    parameters: Dict[str, Any] = Field(
        ...,
        description="Type-specific parameters (e.g. for workspace: {name, metastore, domain_owner, groups}; for schema: {catalog, name, owner, comment}).",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional business justification or reason for this request.",
    )


@tool(
    name="terramate_provision",
    description=(
        "Submit an infrastructure provisioning request to the Terramate API service. "
        "Use this when a user asks to provision a new workspace, schema, catalog, or other "
        "infrastructure resource managed via Terramate/Terraform GitOps."
    ),
    args_schema=TerramateProvisionInput,
    side_effect_class="infra",
)
async def terramate_provision(
    request_type: str,
    parameters: Dict[str, Any],
    reason: Optional[str] = None,
    _user_email: Optional[str] = None,
    _tool_call_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Submit provisioning request via the TerramateProvider.
    """
    provider = TerramateProvider()
    idempotency_key = _tool_call_id or str(uuid.uuid4())
    requester = _user_email or "unknown-user"

    try:
        result = await provider.create_request(
            request_type=request_type,
            params=parameters,
            idempotency_key=idempotency_key,
            requester=requester,
        )

        request_id = result.get("request_id")
        status = result.get("status")

        return {
            "success": True,
            "request_id": request_id,
            "type": request_type,
            "status": status,
            "message": (
                f"Successfully submitted Terramate provisioning request '{request_id}' "
                f"for resource type '{request_type}' with status '{status}'."
            ),
        }
    except Exception as e:
        logger.error(f"Terramate provisioning failed for type '{request_type}': {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to submit provisioning request: {e}",
        }
