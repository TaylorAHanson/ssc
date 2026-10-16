"""
Tool to preview an Access Rules change before it's filed (ADR 0005 D4, D9).
"""
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.models.rbac import AccessItem
from app.tools.mcp import tool


class PreviewAccessChangeInput(BaseModel):
    items: List[AccessItem] = Field(
        ...,
        min_length=1,
        description=(
            "The grants / revokes asked for. One item per principal and securable: action, environment, "
            "securable_type plus its name fields (catalog / schema_name / object_name, or workspace_name / "
            "object_name), principal_type ('group' or 'service principal') and principal_name, privileges."
        ),
    )
    justification: str = Field(
        default="",
        description="The user's own reason, in their words. Leave empty until they give one; never write it.",
    )


@tool(
    name="preview_access_change",
    description=(
        "Dry-run an Access Rules change against the ledger and policy without filing anything. Returns "
        "one proposal per type of rule (UC securables, UC metastore, workspace securables), each with "
        "its approver group (or the Platform Admin fallback), outcomes per item (change / already true "
        "/ blocked), added prerequisites, findings, a summary and report_markdown, plus group_ref when "
        "there is more than one proposal. Present the plan, get an explicit yes, then file one "
        "rbac_access_change request per proposal with that proposal's submit_items, the user's "
        "justification and the shared group_ref. next_step says exactly what to do."
    ),
    args_schema=PreviewAccessChangeInput,
    feature_flag="rbac",
    friendly_label="Checking the access change",
    friendly_completion_label="Checked the access change",
)
def preview_access_change(items: List[Dict[str, Any]], justification: str = "", **kwargs) -> Dict[str, Any]:
    from pydantic import ValidationError

    from app.db.session import get_lakebase_session
    from app.models.rbac import AccessChangeIn
    from app.services.rbac import ledger, requests as rbac_requests

    try:
        change = AccessChangeIn.model_validate({"items": items, "justification": justification or ""})
    except ValidationError as exc:
        return {"ok": False, "error": f"Invalid access items: {exc.errors(include_url=False)}"}
    db = get_lakebase_session()
    try:
        return rbac_requests.preview_access_change(db, change, requester=kwargs.get("_user_email"))
    except ledger.LedgerError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()
