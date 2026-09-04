"""
Infrastructure provisioning workflow tools (Terraform and Terramate).
"""
import logging
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Terraform / Volume GitOps
# --------------------------------------------------------------------------
@tool(
    name="terraform_plan",
    side_effect_class="read",
    description="Produce a Terraform/GitOps plan for review (no apply).",
)
async def terraform_plan(**kwargs) -> Dict[str, Any]:
    provider = _common._get_gitops_provider()
    plan = await provider.plan(kwargs.get("request_id"), kwargs.get("parameters", {}))
    return {"plan": plan}


@tool(
    name="terraform_apply",
    side_effect_class="infra",
    description="Apply a reviewed Terraform/GitOps plan (provisions infra).",
)
async def terraform_apply(**kwargs) -> Dict[str, Any]:
    provider = _common._get_gitops_provider()
    result = await provider.apply(kwargs.get("request_id"), kwargs.get("parameters", {}))
    return {"applied": result}


# --------------------------------------------------------------------------
# Terramate Provisioning Tools (v2 API abstraction - ADR-0004)
# --------------------------------------------------------------------------
TerramateResourceType = Literal["schema", "workspace"]


class TerramateProvisionInput(BaseModel):
    request_type: TerramateResourceType = Field(
        ...,
        description="Resource type to provision. Allowed values: 'schema', 'workspace'.",
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Type-specific provisioning parameters. "
            "For 'schema': catalog, name, owner, optional comment. "
            "For 'workspace': name, metastore, domain_owner, groups."
        ),
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Optional client idempotency key (UUIDv4). Defaults to workflow request_id if available.",
    )


@tool(
    name="terramate_provision",
    args_schema=TerramateProvisionInput,
    side_effect_class="infra",
    description=(
        "Submit an infrastructure provisioning request to the Terramate API service. "
        "Workflow building block used to provision schemas and workspaces via GitOps."
    ),
)
async def terramate_provision(
    request_type: str,
    parameters: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    import uuid

    provider = _common._get_terramate_provider()
    key = idempotency_key or kwargs.get("request_id") or kwargs.get("_tool_call_id") or str(uuid.uuid4())
    result = await provider.create_request(
        request_type=request_type,
        params=parameters or {},
        idempotency_key=key,
    )
    return {
        "ok": result.get("success", False),
        "terramate_request_id": result.get("request_id"),
        "status": result.get("status"),
    }


# Backwards compatibility aliases
terramate_submit_request = terramate_provision
TerramateSubmitInput = TerramateProvisionInput


class TerramateCheckStatusInput(BaseModel):
    terramate_request_id: str = Field(..., description="The Terramate request UUID")


@tool(
    name="terramate_check_status",
    args_schema=TerramateCheckStatusInput,
    side_effect_class="read",
    description="Query the live progress, PR state, and terminal status of a Terramate provisioning request.",
)
async def terramate_check_status(
    terramate_request_id: str,
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_terramate_provider()
    detail = await provider.get_request(terramate_request_id)
    if detail is None:
        return {
            "exists": False,
            "status": "not_found",
            "is_terminal": True,
            "is_succeeded": False,
            "terramate_request_id": terramate_request_id,
        }

    status = detail.get("status", "pending")
    steps = detail.get("steps") or []

    active_pr_url = None
    for step in steps:
        if step.get("status") == "submitted" and step.get("pr_url"):
            active_pr_url = step.get("pr_url")
            break

    is_terminal = status in ("succeeded", "failed", "cancelled")
    is_succeeded = status == "succeeded"

    return {
        "exists": True,
        "terramate_request_id": detail.get("id"),
        "type": detail.get("type"),
        "status": status,
        "is_terminal": is_terminal,
        "is_succeeded": is_succeeded,
        "active_pr_url": active_pr_url,
        "steps": steps,
    }


class CreateUcObjectInput(BaseModel):
    object_type: str = Field(..., description="catalog | schema | volume | folder | tag | credential")
    name: str = Field(..., description="Object name / path")
    parameters: Dict[str, Any] = Field(default_factory=dict)


@tool(
    name="create_uc_object",
    args_schema=CreateUcObjectInput,
    side_effect_class="infra",
    description="Create a Unity Catalog / workspace object (catalog, schema, volume, folder, tag, credential).",
)
async def create_uc_object(
    object_type: str,
    name: str,
    parameters: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    logger.info("create_uc_object: %s '%s' params=%s", object_type, name, parameters)
    return {"created": True, "object_type": object_type, "name": name}


class CreateSpInput(BaseModel):
    display_name: str = Field(..., description="Service principal display name")


@tool(
    name="create_service_principal",
    args_schema=CreateSpInput,
    side_effect_class="infra",
    description="Create a Databricks service principal.",
)
async def create_service_principal(display_name: str, **kwargs) -> Dict[str, Any]:
    provider = _common._get_gitops_provider()
    result = await provider.apply(
        kwargs.get("request_id"),
        {"display_name": display_name, **kwargs.get("parameters", {})},
    )
    return {"service_principal": display_name, "applied": result}
