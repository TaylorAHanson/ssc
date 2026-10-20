"""Metadata change workflow tools (the ``metadata_change`` graph, ADR-0006 D8).

Thin wrappers over ``app.services.metadata.requests``. Each reads the request by
``_request_id`` (injected by the ToolExecutor); the record step works only from
the change prepared and stored on the request, never from its own arguments.
"""
from typing import Any, Dict

from app.tools.mcp import tool


@tool(
    name="prepare_metadata_change",
    # Stores the prepared change on the request row, but changes no tags or descriptions.
    side_effect_class="read",
    friendly_label="Preparing the metadata change",
    friendly_completion_label="Prepared the metadata change",
    description=(
        "Prepare a metadata change request against the current desired state: resolve the filed tag and "
        "description edits, check key policies, classify sensitive changes, and store the exact items and "
        "the owner groups who approve them on the request. Refuses a change a policy blocks, listing why. "
        "Returns nothing_to_change, sensitive (whether the governance gate applies), approver_groups and "
        "report_markdown for the approval card."
    ),
)
def prepare_metadata_change(**kwargs) -> Dict[str, Any]:
    from app.services.metadata import requests as metadata_requests

    return metadata_requests.prepare_step(kwargs.get("_request_id"))


@tool(
    name="record_metadata_change",
    side_effect_class="app_write",
    friendly_label="Recording the metadata change",
    friendly_completion_label="Recorded the metadata change",
    description=(
        "Write the approved change prepared on the request into the metadata desired state as one change set, "
        "never wider than what was approved, and queue it for the reconciler. Refuses when a tag or "
        "description changed since it was approved. Idempotent: a request already recorded returns its change set."
    ),
)
def record_metadata_change(**kwargs) -> Dict[str, Any]:
    from app.services.metadata import requests as metadata_requests

    return metadata_requests.record_step(kwargs.get("_request_id"))
