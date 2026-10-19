"""Access Rules request workflow tools (the ``rbac_access_change`` graph, ADR 0005 D4, D8).

Thin wrappers over ``app.services.rbac.requests``. Each reads the request by
``_request_id`` (injected by the ToolExecutor); the record step works only from
the change prepared and stored on the request, never from its own arguments.
"""
from typing import Any, Dict, List, Optional

from app.tools.mcp import tool


@tool(
    name="rbac_prepare_access_change",
    # Stores the prepared change on the request row (so the grid marks the rows), but changes no rules.
    side_effect_class="read",
    friendly_label="Preparing the access change",
    friendly_completion_label="Prepared the access change",
    description=(
        "Prepare an Access Rules request against the current rules: validate the items, add prerequisites, "
        "check policy, work out the exact row changes and who approves, and store them on the request "
        "(state context 'prepared_change'). Refuses a request spanning more than one type of rule (split "
        "by type) and a blocked change, listing the deny findings. A direct edit filed under the two-person "
        "rule (changes + plane on the request) is re-checked the same way. Returns plane (the type of rule, "
        "which picks the approver group), approver_group (empty means Platform Admins approve) and "
        "report_markdown for the approval card."
    ),
)
def rbac_prepare_access_change(
    items: Optional[List[Dict[str, Any]]] = None,
    justification: Optional[str] = None,
    group_ref: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    from app.services.rbac import requests as rbac_requests

    return rbac_requests.prepare_step(kwargs.get("_request_id"), items=items, justification=justification,
                                      group_ref=group_ref)


@tool(
    name="record_access_change",
    side_effect_class="data_grant",
    friendly_label="Recording the access change",
    friendly_completion_label="Recorded the access change",
    description=(
        "Write the approved change prepared on the request into the access rules as one change set, never "
        "wider than what was approved; the next CSV export carries it to Terraform. Idempotent: a request "
        "already recorded returns its change set. The message says when the next export picks it up."
    ),
)
def record_access_change(**kwargs) -> Dict[str, Any]:
    from app.services.rbac import requests as rbac_requests

    return rbac_requests.record_step(kwargs.get("_request_id"))


# Former tool names -> current ones: specs saved under the old names still compile.
RENAMED_TOOLS: Dict[str, str] = {
    "rbac_plan_access_change": "rbac_prepare_access_change",
    "apply_access_change": "record_access_change",
}
