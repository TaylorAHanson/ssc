"""
Tool to look for existing requests that a workflow submission would duplicate.
"""
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.db.session import get_lakebase_session
from app.services import duplicate_requests
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class CheckDuplicateRequestsInput(BaseModel):
    workflow_type: str = Field(
        ...,
        description="The workflow_type you are about to submit (the same value you will pass to execute_workflow).",
    )
    parameters: Dict[str, Any] = Field(
        ...,
        description="The parameters you are about to submit to execute_workflow.",
    )


@tool(
    name="check_duplicate_requests",
    description=(
        "Run before submitting any workflow: finds open or recently completed requests of the same "
        "workflow that target the same resource, yours or other people's. Pass the exact "
        "workflow_type and parameters you are about to send to execute_workflow, and mention any "
        "matches in your confirmation summary."
    ),
    args_schema=CheckDuplicateRequestsInput,
    friendly_label="Checking for duplicate requests...",
)
def check_duplicate_requests(
    workflow_type: str,
    parameters: Dict[str, Any],
    _user_email: Optional[str] = None,
    _user_roles: Optional[Any] = None,
) -> Dict[str, Any]:
    if not duplicate_requests.is_enabled():
        return {
            "enabled": False,
            "checked": False,
            "count": 0,
            "possible_duplicates": [],
            "message": "Duplicate checking is turned off in this environment.",
        }
    db = get_lakebase_session()
    try:
        result = duplicate_requests.find_duplicate_requests(
            db,
            workflow_type,
            parameters,
            requester_email=_user_email,
            roles=_user_roles,
        )
    except Exception as e:  # noqa: BLE001 - report the failure instead of claiming "no duplicates"
        logger.warning("check_duplicate_requests failed for %s: %s", workflow_type, e)
        return {
            "enabled": True,
            "checked": False,
            "error": f"Could not check for duplicate requests: {e}",
        }
    finally:
        db.close()
    return {"enabled": True, "workflow_type": workflow_type, **result}
