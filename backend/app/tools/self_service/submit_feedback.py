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
import re
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

# A report with less than this in the user's own words says too little for anyone to act on
# ("this app sucks"). The tool sends the assistant back to ask what happened before it drafts.
# Naming the thing it is about (`about`) counts as enough on its own.
MIN_DETAIL_WORDS = 5

# The user's latest message has to ask for the report to go to the team, or agree to an offer to send it,
# before it is filed. The model reports that it did ("confirmed"), and models do say so about a problem
# the user only described, so the conversation is checked as well. Wording that isn't recognised sends the
# assistant back to ask, which costs a question; filing something nobody asked for costs more.
_ASKS_TO_SEND = re.compile(
    r"\b(?:report|flag|log|file|submit|send|raise)\s+(?:it|this|that|these|them|a|an|the|my|our|some)\b"
    r"|\bpass\s+(?:it|this|that)\s+on\b"
    r"|\b(?:let|tell)\s+(?:the|your)?\s*(?:team|admins?)\b",
    re.I,
)
_AGREES = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|go for it|do it|do that|please do|sounds good|absolutely)\b",
    re.I,
)
_SHORT_REPLY_WORDS = 10  # an agreement is a short reply; "yes" inside a long description isn't one


def _text_of(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def user_asked_to_send(history: Optional[list]) -> bool:
    """Whether the user's latest message asks for this to be sent, or agrees to sending it.

    With no conversation to read (the MCP server, a direct call) there is nothing to check against, so the
    caller's own ``confirmed`` stands.
    """
    if history is None:
        return True
    latest = next(
        (_text_of(m) for m in reversed(history) if isinstance(m, dict) and m.get("role") == "user"),
        "",
    )
    if _ASKS_TO_SEND.search(latest):
        return True
    return len(latest.split()) <= _SHORT_REPLY_WORDS and bool(_AGREES.search(latest))


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
    confirmed: bool = Field(
        default=False,
        description=(
            "Set true ONLY when the user asked you to send it, or said yes after you showed them what it "
            "says. Left false, nothing is sent and you just get a draft back."
        ),
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
        "wants someone to see. Nothing is sent unless you pass confirmed=true, which you do only after the "
        "user has asked you to pass it on or said yes to your offer, with the details in their own words. A "
        "user who merely describes a problem, or says how they feel about the app, hasn't asked yet; ask "
        "what happened first and don't call this for the remark. A question about how feedback works is not "
        "a report. It is not a provisioning request."
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
    confirmed: bool = False,
    conversation_history: Optional[list] = None,
    **kwargs,
) -> Dict[str, Any]:
    user_email = kwargs.get("_user_email")
    stored_type, label = _normalize(feedback_type)
    composed = _compose_description(description, about, label)

    # Nothing is written until the report says something and the user has agreed to send it.
    # These come back as results, not errors, so the assistant carries on the conversation. They carry
    # `filed` and no `success` key: `success: false` is how a tool says it failed, which the executor
    # audits and logs as a failed call, and asking the user a question isn't one.
    if not (title or "").strip():
        return {
            "success": False,
            "error": "title is required",
            "note": "Tell the user what was missing or invalid and ask them to clarify.",
        }
    if not (about or "").strip() and len((description or "").split()) < MIN_DETAIL_WORDS:
        return {
            "filed": False,
            "needs_details": True,
            "note": (
                "Nothing was sent: there isn't enough here for the team to act on. Ask the user what "
                "happened, or what they would like to see changed, and wait for their answer. Don't "
                "guess, and don't fill the details in yourself. Once they have answered, put their own "
                "words in `description` and call this again."
            ),
        }
    if not confirmed or not user_asked_to_send(conversation_history):
        return {
            "filed": False,
            "needs_confirmation": True,
            "draft": {"type": stored_type, "title": title.strip(), "description": composed},
            "note": (
                "Nothing was sent yet: the user hasn't asked for this to go to the team, or said yes to "
                "your offer. Show them what it would say (the title and their own words) and ask "
                "whether to send it. Only after they say yes, call submit_feedback again with the same "
                "fields and confirmed=true."
            ),
        }
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
