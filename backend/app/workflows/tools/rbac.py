"""Access Rules request workflow tools (the ``rbac_access_change`` graph, ADR 0005 D4, D8).

Thin wrappers over ``app.services.rbac.requests``. Each reads the request by
``_request_id`` (injected by the ToolExecutor); the apply step works only from
the plan stored on the request, never from its own arguments.
"""
from typing import Any, Dict, List, Optional

from app.tools.mcp import tool


@tool(
    name="rbac_plan_access_change",
    # Stores its plan on the request row (so the grid marks the rows), but changes no rules.
    side_effect_class="read",
    friendly_label="Planning the access change",
    description=(
        "Plan an Access Rules request against the current ledger: validate the items, add prerequisites, "
        "check policy, and store the plan on the request (state context 'rbac_plan'). Refuses a request "
        "spanning more than one type of rule (split by type) and a blocked plan, listing the deny "
        "findings. A direct edit filed under the two-person rule (changes + plane on the request) is "
        "re-checked the same way. Returns approvers (the type's approver group; empty means Platform "
        "Admins approve) and report_markdown for the approval card."
    ),
)
def rbac_plan_access_change(
    items: Optional[List[Dict[str, Any]]] = None,
    justification: Optional[str] = None,
    group_ref: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    from app.services.rbac import requests as rbac_requests

    return rbac_requests.plan_step(kwargs.get("_request_id"), items=items, justification=justification,
                                   group_ref=group_ref)


@tool(
    name="apply_access_change",
    side_effect_class="data_grant",
    friendly_label="Updating the access rules",
    friendly_completion_label="Updated the access rules",
    description=(
        "Write the approved plan stored on the request to the Access Rules ledger as one change set, "
        "never wider than what was approved. Idempotent: a request already applied returns its change "
        "set. The message says when the next CSV export picks the change up."
    ),
)
def apply_access_change(**kwargs) -> Dict[str, Any]:
    from app.services.rbac import requests as rbac_requests

    return rbac_requests.apply_step(kwargs.get("_request_id"))
