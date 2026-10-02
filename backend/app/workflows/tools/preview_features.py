"""Preview-feature request workflow tools (Admin -> Preview Features).

Thin wrappers over ``app.services.preview_features.steps``. Each reads the
targets linked to the request (``_request_id``, injected by the ToolExecutor),
so a step needs no authored args.
"""
import logging
from typing import Any, Dict, List, Optional

from app.tools.mcp import tool

logger = logging.getLogger(__name__)

_STATUSES = ("requested", "approved", "rejected")


@tool(
    name="assess_preview_feature",
    side_effect_class="read",
    friendly_label="Summarizing the preview feature",
    description=(
        "Summarize a preview-feature request for approvers: the feature, its phase, scope, docs and "
        "announcement, and each selected target's current value. Returns report_markdown, shown on "
        "later approval cards. For a request the agent started, first links it to the feature "
        "(setting name) and workspaces given, with the same checks as the Preview Features page."
    ),
)
async def assess_preview_feature(
    feature: Optional[str] = None,
    action: Optional[str] = "enable",
    targets: Optional[List[str]] = None,
    justification: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    from app.services.preview_features import steps

    return steps.assess(kwargs.get("_request_id"), feature_ref=feature, action=action, targets=targets)


@tool(
    name="set_preview_target_status",
    side_effect_class="app_write",
    description=(
        "Record a preview-feature request's status (requested / approved / rejected) on every target "
        "in the request, so the Preview Features tab shows it. Approved also records who approved."
    ),
)
async def set_preview_target_status(status: str = "approved", **kwargs) -> Dict[str, Any]:
    from app.services.preview_features import steps

    if status not in _STATUSES:
        return {"ok": False, "error": f"status must be one of {', '.join(_STATUSES)}"}
    return steps.set_status(kwargs.get("_request_id"), status)


@tool(
    name="set_preview_setting",
    side_effect_class="infra",
    friendly_label="Changing the preview setting",
    description=(
        "Turn a workspace preview on (or off, for a disable request) on every approved workspace in "
        "the request, using each workspace's service principal (needs workspace admin). Skips "
        "workspaces already at the wanted value. A workspace where the change fails is listed in "
        "manual_targets and sets needs_manual, so a later manual task can cover it."
    ),
)
async def set_preview_setting(**kwargs) -> Dict[str, Any]:
    from app.services.preview_features import steps

    return await steps.apply(kwargs.get("_request_id"))


@tool(
    name="verify_preview_setting",
    # Reads settings, but records the outcome (implemented) in the app's tables.
    side_effect_class="app_write",
    friendly_label="Checking the preview is on",
    description=(
        "Check each approved target's effective setting value (or the feature's probe, or the "
        "implementer's attestation for account previews no workspace can read) and mark verified "
        "targets implemented. Unverified targets stay approved and the daily sync re-checks them."
    ),
)
async def verify_preview_setting(**kwargs) -> Dict[str, Any]:
    from app.services.preview_features import steps

    return await steps.verify(kwargs.get("_request_id"))
