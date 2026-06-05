"""
Tool: submit feedback, a feature request, or a bug report via chat.

Lets the agent capture a user's feedback into the same store the avatar-menu
form writes to, so it shows up in the admin triage page. Console/network
diagnostics aren't available from the server side, so chat-sourced bug reports
won't carry those (the web form does).
"""
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.db.session import get_db
from app.services.feedback_service import FeedbackService

logger = logging.getLogger(__name__)


class SubmitFeedbackInput(BaseModel):
    feedback_type: str = Field(
        ...,
        description=(
            "The kind of submission: 'bug' (something is broken), 'feature' (a "
            "request/idea), or 'feedback' (general comment)."
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        description="A short one-line summary of the feedback.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Full details. For bugs, include what happened and steps to reproduce.",
    )
    severity: Optional[str] = Field(
        default=None,
        description="For bugs only: 'low', 'medium', 'high', or 'critical'.",
    )


@tool(
    name="submit_feedback",
    description=(
        "Submit the user's feedback, feature request, or bug report so it's "
        "recorded for the admins to triage. Use this when the user wants to "
        "report a bug, request a feature, or share feedback about the app. "
        "ALWAYS confirm the details (type, a clear title, and a description) "
        "with the user first — do NOT invent or embellish what they said. "
        "Capture their own words."
    ),
    args_schema=SubmitFeedbackInput,
    feature_flag="core",
    friendly_label="Submitting feedback...",
)
async def submit_feedback(
    feedback_type: str,
    title: str,
    description: Optional[str] = None,
    severity: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    user_email = kwargs.get("_user_email")
    db = next(get_db())
    try:
        fb = FeedbackService.create_feedback(
            db,
            type=feedback_type,
            title=title,
            description=description,
            severity=severity,
            source="chat",
            submitted_by=user_email,
        )
        return {
            "success": True,
            "type": fb.type,
            "note": (
                "Recorded. Confirm to the user it was submitted and will be "
                "reviewed by the admins, with a one-line recap of the type and "
                "title. Do NOT show an ID, reference number, or any link — "
                "feedback is not a trackable Request, so presenting an ID would "
                "be misleading."
            ),
        }
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "note": "Tell the user what was invalid (e.g. type must be bug/feature/feedback) and ask them to clarify.",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("submit_feedback failed: %s", e)
        return {"success": False, "error": f"Could not submit feedback: {e}"}
    finally:
        db.close()
