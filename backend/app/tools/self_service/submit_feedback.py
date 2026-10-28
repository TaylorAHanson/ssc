"""
Tool: tell the team something, via chat: feedback, a feature request, a bug, a
problem with some data, or anything else.

Lets the agent capture it into the same store the avatar-menu form writes to, so
it shows up in the admin triage page. Deliberately forgiving: the user never has
to classify what they are reporting, and an unrecognised label is kept (in the
description) rather than rejected. Console/network diagnostics aren't available
from the server side, so chat-sourced bug reports won't carry those (the web form
does).
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.db.session import get_db
from app.db.feedback import FEEDBACK_TYPES, FeedbackModel
from app.services.feedback_service import FeedbackService

logger = logging.getLogger(__name__)

# The same words from the same person inside this window are one report, not two.
_DUPLICATE_WINDOW = timedelta(minutes=15)
_MAX_TEXT = 8000  # matches FeedbackService's stored-text cap, so a long report still compares equal

# What a report is filed under when the caller doesn't say, or says something we
# don't have a bucket for (e.g. "data issue"). Triage reads the description.
DEFAULT_FEEDBACK_TYPE = "feedback"


class SubmitFeedbackInput(BaseModel):
    feedback_type: Optional[str] = Field(
        default=DEFAULT_FEEDBACK_TYPE,
        description=(
            "Optional. 'bug' (the app is broken), 'feature' (a request or idea), or "
            "'feedback' for everything else, including problems with data. When "
            "unsure, use 'feedback'. Never ask the user to choose."
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        description="A short one-line summary of what the user wants the team to know.",
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Full details in the user's own words. For bugs, include what happened and "
            "steps to reproduce. For data problems, what they saw and what they expected."
        ),
    )
    about: Optional[str] = Field(
        default=None,
        description=(
            "Optional. What this is about, when it's a specific thing: a full table name "
            "(catalog.schema.table), a dashboard, a report, or a page."
        ),
    )
    severity: Optional[str] = Field(
        default=None,
        description="For bugs only: 'low', 'medium', 'high', or 'critical'.",
    )


def _normalize(feedback_type: Optional[str]) -> tuple[str, Optional[str]]:
    """The stored type, plus the caller's own label when we had no bucket for it."""
    label = (feedback_type or "").strip()
    if not label:
        return DEFAULT_FEEDBACK_TYPE, None
    if label.lower() in FEEDBACK_TYPES:
        return label.lower(), None
    return DEFAULT_FEEDBACK_TYPE, label


def _compose_description(description: Optional[str], about: Optional[str], label: Optional[str]) -> Optional[str]:
    """Put what the report concerns, and any unrecognised label, above the user's words."""
    head = []
    if (about or "").strip():
        head.append(f"About: {about.strip()}")
    if label:
        head.append(f"Reported as: {label}")
    body = (description or "").strip()
    if not head:
        return body or None
    return "\n".join(head) + (f"\n\n{body}" if body else "")


@tool(
    name="submit_feedback",
    side_effect_class="app_write",
    description=(
        "Pass something on to the team for triage: feedback about the app, a feature request, a bug, "
        "or a problem with some data (stale, wrong, missing, broken). Use it for any report the user "
        "wants someone to see, and only after they have asked you to pass it on or said yes to your "
        "offer; a user who merely describes a problem hasn't asked yet, and a question about how "
        "feedback works is not a report. It is not a provisioning request."
    ),
    args_schema=SubmitFeedbackInput,
    feature_flag="core",
    friendly_label="Submitting feedback...",
)
def submit_feedback(
    title: str,
    feedback_type: Optional[str] = DEFAULT_FEEDBACK_TYPE,
    description: Optional[str] = None,
    about: Optional[str] = None,
    severity: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    user_email = kwargs.get("_user_email")
    stored_type, label = _normalize(feedback_type)
    composed = _compose_description(description, about, label)
    db = next(get_db())
    try:
        # The same person sending the same words again a few minutes later is a repeat (the
        # assistant asked twice, or the user answered a recap with "yes, report it"), not a
        # new report. Record it once.
        if user_email and composed:
            recent = (
                db.query(FeedbackModel)
                .filter(
                    FeedbackModel.submitted_by == user_email,
                    FeedbackModel.description == composed.strip()[:_MAX_TEXT],
                    FeedbackModel.created_at >= datetime.utcnow() - _DUPLICATE_WINDOW,
                )
                .first()
            )
            if recent is not None:
                return {
                    "success": True,
                    "type": recent.type,
                    "already_recorded": True,
                    "note": (
                        "This was already passed on to the admins a moment ago, so nothing new was "
                        "recorded. Tell the user it is already with the team; don't say you submitted "
                        "it again."
                    ),
                }
        fb = FeedbackService.create_feedback(
            db,
            type=stored_type,
            title=title,
            description=composed,
            severity=severity,
            source="chat",
            submitted_by=user_email,
        )
        return {
            "success": True,
            "type": fb.type,
            "note": (
                "Recorded. Confirm to the user it was submitted and will be "
                "reviewed by the admins, with a one-line recap of what it was about. "
                "Do not call it the wrong kind of report, and do not promise a fix or a "
                "timeline. If it concerns a specific table or dashboard, offer to find its "
                "owner so they can also reach out directly. Do NOT show an ID, reference "
                "number, or any link — feedback is not a trackable Request, so presenting an "
                "ID would be misleading."
            ),
        }
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "note": "Tell the user what was missing or invalid and ask them to clarify.",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("submit_feedback failed: %s", e)
        return {"success": False, "error": f"Could not submit feedback: {e}"}
    finally:
        db.close()
