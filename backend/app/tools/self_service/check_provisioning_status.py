"""
Conversational agent tool for checking Terramate infrastructure provisioning status.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.providers.terramate.client import TerramateProvider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class CheckProvisioningStatusInput(BaseModel):
    request_id: str = Field(
        ...,
        description="The Terramate provisioning request UUID to check.",
    )
    include_plan: bool = Field(
        default=False,
        description="Whether to fetch the Terraform plan text if available.",
    )


@tool(
    name="check_provisioning_status",
    description=(
        "Check the status, step progress, open PR links, outputs, and plan details "
        "of a Terramate infrastructure provisioning request."
    ),
    args_schema=CheckProvisioningStatusInput,
    side_effect_class="read",
)
async def check_provisioning_status(
    request_id: str,
    include_plan: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Retrieve live provisioning status via TerramateProvider.
    """
    provider = TerramateProvider()

    try:
        detail = await provider.get_request(request_id)
        if detail is None:
            return {
                "success": False,
                "found": False,
                "request_id": request_id,
                "message": f"Provisioning request '{request_id}' not found.",
            }

        steps_summary = []
        for step in detail.get("steps", []):
            step_info = {
                "ordinal": step.get("ordinal"),
                "key": step.get("key"),
                "status": step.get("status"),
                "pr_number": step.get("pr_number"),
                "pr_url": step.get("pr_url"),
                "stuck": step.get("stuck", False),
            }
            steps_summary.append(step_info)

        plan_info = None
        if include_plan:
            plan_res = await provider.get_step_plan(request_id, ordinal=0)
            if plan_res.get("available"):
                plan_info = plan_res.get("plan")
            else:
                plan_info = plan_res.get("message", "Plan not available yet.")

        return {
            "success": True,
            "found": True,
            "request_id": detail.get("id"),
            "type": detail.get("type"),
            "status": detail.get("status"),
            "requester": detail.get("requester"),
            "created_at": detail.get("created_at"),
            "updated_at": detail.get("updated_at"),
            "steps": steps_summary,
            "plan": plan_info,
            "message": (
                f"Request '{request_id}' ({detail.get('type')}) is currently in status '{detail.get('status')}' "
                f"with {len(steps_summary)} step(s)."
            ),
        }
    except Exception as e:
        logger.error(f"Failed to check provisioning status for '{request_id}': {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Error retrieving provisioning status: {e}",
        }
