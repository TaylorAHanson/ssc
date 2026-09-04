"""
Conversational agent tool for checking Terramate infrastructure provisioning status (ADR-0004).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.providers.terramate.client import TerramateProvider
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class CheckProvisioningStatusInput(BaseModel):
    request_id: str = Field(
        ...,
        description="The Terramate provisioning request UUID to check.",
    )


@tool(
    name="check_provisioning_status",
    description="Check the live status, step execution progress, and open pull request URLs of a Terramate infrastructure provisioning request by its request UUID.",
    args_schema=CheckProvisioningStatusInput,
    side_effect_class="read",
)
async def check_provisioning_status(
    request_id: str,
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

        status = detail.get("status", "pending")
        steps = detail.get("steps") or []

        steps_summary: List[Dict[str, Any]] = []
        active_pr_urls: List[str] = []
        stuck_steps: List[str] = []

        for step in steps:
            step_status = step.get("status")
            pr_url = step.get("pr_url")
            stuck = step.get("stuck", False)
            key = step.get("key", f"step-{step.get('ordinal')}")

            if step_status == "submitted" and pr_url:
                active_pr_urls.append(pr_url)
            if stuck:
                stuck_steps.append(key)

            step_info = {
                "ordinal": step.get("ordinal"),
                "key": key,
                "status": step_status,
                "pr_number": step.get("pr_number"),
                "pr_url": pr_url,
                "depends_on": step.get("depends_on", []),
                "stuck": stuck,
                "status_changed_at": step.get("status_changed_at"),
            }
            steps_summary.append(step_info)

        is_terminal = status in ("succeeded", "failed", "cancelled")
        is_succeeded = status == "succeeded"

        # Build human-friendly explanation of the status & approval seam
        msg_parts = [f"Request '{request_id}' ({detail.get('type')}) is '{status}'."]

        if status == "succeeded":
            msg_parts.append("All steps have been applied successfully.")
        elif status == "failed":
            msg_parts.append("Provisioning failed or a pull request was rejected.")
        elif status == "cancelled":
            msg_parts.append("The request was cancelled.")
        elif active_pr_urls:
            msg_parts.append(
                f"Action required on GitHub: A reviewer must inspect and merge (or close) the open PR to approve (or reject) this step: {', '.join(active_pr_urls)}"
            )
        elif status == "pending":
            msg_parts.append("Request is accepted and waiting for initial step execution.")
        elif status == "in_progress":
            msg_parts.append("Request is in progress.")

        if stuck_steps:
            msg_parts.append(
                f"Note: Step(s) {', '.join(stuck_steps)} are waiting longer than expected and may need operator attention."
            )

        return {
            "success": True,
            "found": True,
            "request_id": detail.get("id"),
            "type": detail.get("type"),
            "status": status,
            "is_terminal": is_terminal,
            "is_succeeded": is_succeeded,
            "requester": detail.get("requester"),
            "created_at": detail.get("created_at"),
            "updated_at": detail.get("updated_at"),
            "active_pr_urls": active_pr_urls,
            "steps": steps_summary,
            "message": " ".join(msg_parts),
        }
    except Exception as e:
        logger.error(f"Failed to check provisioning status for '{request_id}': {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Error retrieving provisioning status: {e}",
        }
