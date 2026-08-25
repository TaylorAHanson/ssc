"""The notice a requester gets when their request is denied.

A workflow's rejection path is terminal by construction: every gate's failure edge
goes to one built-in node that records ``request_rejected`` and ends the graph
(``app/workflows/spec.py``). Notifications in this platform are ordinary
provisioning steps, and no stage can sit on that edge — so a denied requester was
never told, even though the approver's reason was captured on the approval row.

This module closes that loop for every workflow at once, without re-authoring any
of them. It is deliberately not a governed tool call: a rejection notice that OPA
could deny would mean "the platform can't tell you it said no". Same reasoning as
the poller's failure notification, which also goes straight to the provider — but
resolved through the workflow tools' provider getter, so hermetic eval runs stay
hermetic.

Copy is admin-editable (Admin -> Settings -> Notifications & Governance) using the
``{{token}}`` convention shared with workflow instructions:

``{{request_title}}``, ``{{request_id}}``, ``{{request_type}}``, ``{{reason}}``,
``{{rejected_by}}``, ``{{brand_name}}``, ``{{app_url}}``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# The poller's generic resume value when it sees a rejection fact. It's a marker,
# not something a human wrote, so it must never be shown as "the reason".
_PLACEHOLDER_REASONS = {"", "rejected", "none", "null"}

_FALLBACK_SUBJECT = "Your request was not approved"
_NO_REASON = "No reason was recorded."


def _is_placeholder(reason: Optional[str]) -> bool:
    return str(reason or "").strip().lower() in _PLACEHOLDER_REASONS


def resolve_rejection_details(db: Session, request_id: str) -> tuple[Optional[str], Optional[str]]:
    """The approver's note and identity, read off the ``request_rejected`` fact.

    The fact written by the reject endpoint carries the note the approver actually
    typed; the graph state only carries whatever the resume value held. Also used
    by the poller so the note — not a marker — becomes the gate's resume reason.
    """
    from app.state_machines.facts import get_facts

    note: Optional[str] = None
    reviewer: Optional[str] = None
    try:
        for fact in get_facts(db, request_id, "request_rejected"):
            data = fact.event_data or {}
            note = data.get("rejection_note") or data.get("reason") or note
            reviewer = data.get("rejected_by") or reviewer
    except Exception as e:  # noqa: BLE001 - the notice matters more than its detail
        logger.debug("rejection notice: could not read rejection facts for %s: %s", request_id, e)
    return note, reviewer


def _render(template: str, tokens: Dict[str, str]) -> str:
    """Substitute ``{{token}}`` placeholders. Plain replacement, not ``format``:
    the values are human-written text that may legitimately contain braces."""
    out = template or ""
    for key, value in tokens.items():
        out = out.replace("{{" + key + "}}", value)
    return out


async def notify_requester_of_rejection(
    db: Session,
    request_id: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Email the requester that their request was denied, and why.

    Never raises and never blocks the rejection: a request is denied whether or
    not the mail goes out. Returns a small result dict for logging and tests.
    """
    if not getattr(settings, "REJECTION_NOTIFY_REQUESTER", True):
        return {"sent": False, "reason": "disabled"}

    from app.db.request import RequestModel

    try:
        request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    except Exception as e:  # noqa: BLE001
        logger.error("rejection notice: could not load request %s: %s", request_id, e)
        return {"sent": False, "reason": "request_lookup_failed"}

    if request is None:
        logger.warning("rejection notice: request %s not found", request_id)
        return {"sent": False, "reason": "request_not_found"}

    recipient = (request.requester_email or "").strip()
    if not recipient:
        # Nothing to do, but say so loudly: a request with no requester email is
        # a request whose owner can never be told anything.
        logger.warning(
            "rejection notice: request %s has no requester_email; nobody was notified",
            request_id,
        )
        return {"sent": False, "reason": "no_recipient"}

    note, reviewer = resolve_rejection_details(db, request_id)
    # Prefer the approver's typed note; fall back to the graph's reason only when
    # it carries something a person would recognize.
    resolved = note if not _is_placeholder(note) else (None if _is_placeholder(reason) else reason)

    tokens = {
        "request_title": request.title or request.type or request_id,
        "request_id": request_id,
        "request_type": request.type or "",
        "reason": (resolved or _NO_REASON).strip(),
        "rejected_by": reviewer or "an approver",
        "brand_name": settings.BRAND_NAME,
        "app_url": (getattr(settings, "APP_BASE_URL", "") or "").strip(),
    }

    subject = _render(settings.REJECTION_NOTIFY_SUBJECT, tokens).strip() or _FALLBACK_SUBJECT
    body = _render(settings.REJECTION_NOTIFY_BODY, tokens).strip()
    if not body:
        body = f"<p>{tokens['reason']}</p>"

    try:
        # Resolve the provider through the workflow tools' getter instead of
        # constructing the client here: that getter is the seam the hermetic eval
        # harness fakes, so a rejection exercised in an eval run can't email a
        # real person.
        from app.workflows.tools import _get_notification_provider

        await _get_notification_provider().send_email(
            to=recipient, subject=subject, body=body, is_html=True
        )
    except Exception as e:  # noqa: BLE001 - a failed email must not fail the rejection
        logger.error(
            "rejection notice: email to %s failed for request %s: %s",
            recipient, request_id, e, exc_info=True,
        )
        return {"sent": False, "reason": "send_failed", "to": recipient}

    logger.info("rejection notice: notified %s that request %s was denied", recipient, request_id)
    return {"sent": True, "to": recipient, "had_reason": resolved is not None}
