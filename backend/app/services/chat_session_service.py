"""
Persistence for server-side chat transcripts.

Two jobs:

* store and retrieve a user's ``DisplayMessage[]`` transcripts, always scoped to
  the owner — a session is never addressable by id alone, or one user could read
  another's conversation;
* translate a stored transcript into the chat-completion ``messages`` list when a
  turn arrives with only a ``session_id``.

That translation mirrors ``buildHistory`` in ``src/components/chat/ChatView.tsx``.
The client still sends ``conversation_history`` on every turn and that still wins,
so this path is the fallback until the frontend stops replaying history — moving
persistence and the model's history source in one release would make any
regression impossible to bisect.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.chat_session import CHAT_SURFACES, ChatSessionModel
from app.services.user_context import normalize_email

logger = logging.getLogger(__name__)

DEFAULT_SURFACE = "unified"
# Longest title we derive from a first message.
_TITLE_MAX = 120


def normalize_surface(surface: Optional[str]) -> str:
    """Constrain a client-supplied surface to the known set."""
    candidate = (surface or "").strip().lower()
    return candidate if candidate in CHAT_SURFACES else DEFAULT_SURFACE


def _owner(user_email: str) -> str:
    """Key transcripts the same way profiles are keyed.

    ``user_profiles`` lowercases the email; this table was storing whatever the
    identity provider handed us. For any IdP that returns mixed case the two
    disagreed, and the mismatch was silent — the activity section's "recently
    asked about" joins on the profile's lowercased key and would simply never
    find a transcript. Normalizing here, at the boundary that owns the column,
    keeps every query and write on one spelling.
    """
    return normalize_email(user_email)


def derive_title(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Use the first thing the user said as the session's title."""
    for message in messages or []:
        if isinstance(message, dict) and message.get("kind") == "user":
            text = str(message.get("content") or "").strip()
            if text:
                return text[:_TITLE_MAX]
    return None


def list_sessions(
    db: Session,
    user_email: str,
    *,
    surface: Optional[str] = None,
    limit: int = 20,
) -> List[ChatSessionModel]:
    """A user's sessions, newest first. Bodies are not needed by callers here."""
    query = db.query(ChatSessionModel).filter(ChatSessionModel.user_email == _owner(user_email))
    if surface:
        query = query.filter(ChatSessionModel.surface == normalize_surface(surface))
    return query.order_by(ChatSessionModel.updated_at.desc()).limit(max(1, limit)).all()


def get_session(db: Session, user_email: str, session_id: str) -> Optional[ChatSessionModel]:
    """Load one session **belonging to this user**, or nothing.

    The owner filter is part of the lookup rather than a check afterwards, so
    there is no code path that reads a session without it.
    """
    return (
        db.query(ChatSessionModel)
        .filter(ChatSessionModel.id == session_id)
        .filter(ChatSessionModel.user_email == _owner(user_email))
        .first()
    )


def upsert_session(
    db: Session,
    user_email: str,
    session_id: str,
    *,
    messages: List[Dict[str, Any]],
    surface: Optional[str] = None,
    title: Optional[str] = None,
) -> ChatSessionModel:
    """Create or replace a transcript.

    A transcript is only ever written whole (the client owns the array and sends
    the current state of it), so this replaces rather than appends.
    """
    messages = messages or []
    session = get_session(db, user_email, session_id)
    if session is None:
        session = ChatSessionModel(
            id=session_id,
            user_email=_owner(user_email),
            surface=normalize_surface(surface),
            created_at=datetime.utcnow(),
        )
        db.add(session)
    elif surface:
        session.surface = normalize_surface(surface)

    session.messages = messages
    session.message_count = len(messages)
    session.title = title or derive_title(messages) or session.title
    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, user_email: str, session_id: str) -> bool:
    session = get_session(db, user_email, session_id)
    if session is None:
        return False
    db.delete(session)
    db.commit()
    return True


def delete_sessions(db: Session, user_email: str, *, surface: Optional[str] = None) -> int:
    """Clear a user's transcripts. Backs the sidebar's "clear my data" action."""
    query = db.query(ChatSessionModel).filter(ChatSessionModel.user_email == _owner(user_email))
    if surface:
        query = query.filter(ChatSessionModel.surface == normalize_surface(surface))
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def prune_sessions(db: Session, *, retention_days: Optional[int] = None) -> int:
    """Delete transcripts past the retention window."""
    days = retention_days if retention_days is not None else settings.CHAT_SESSION_RETENTION_DAYS
    days = int(days or 0)
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = (
        db.query(ChatSessionModel)
        .filter(ChatSessionModel.updated_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)


def to_summary(session: ChatSessionModel) -> Dict[str, Any]:
    return {
        "id": session.id,
        "surface": session.surface,
        "title": session.title,
        "message_count": session.message_count or 0,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def to_detail(session: ChatSessionModel) -> Dict[str, Any]:
    return {**to_summary(session), "messages": list(session.messages or [])}


# ---------------------------------------------------------------------------
# Transcript -> chat-completion messages
# ---------------------------------------------------------------------------
def _tool_result_content(message: Dict[str, Any]) -> str:
    """Best textual rendering of a finished tool call, for replay to the model."""
    result = message.get("toolResult")
    if result is not None and not isinstance(result, str):
        try:
            text = json.dumps(result, default=str)
        except (TypeError, ValueError):
            text = str(result)
    elif isinstance(result, str) and result.strip():
        text = result
    else:
        text = str(message.get("detail") or message.get("label") or "(no result recorded)")

    cap = int(settings.AGENT_MAX_TOOL_OUTPUT_CHARS or 25000)
    if len(text) > cap:
        text = text[:cap] + " …[truncated; re-run the tool with tighter filters if you need the rest]"
    return text


def transcript_to_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Translate stored ``DisplayMessage[]`` into chat-completion messages.

    Completed tool calls are replayed as a synthetic ``assistant`` turn carrying
    the original ``tool_calls`` block followed by the ``tool`` result. That
    pairing is mandatory: model serving endpoints reject a ``role='tool'``
    message that isn't preceded by a matching ``tool_calls``, with
    ``HTTP 400 BAD_REQUEST``. In-flight tool entries are skipped — they have no
    result to replay.
    """
    out: List[Dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        kind = message.get("kind")

        if kind == "user":
            out.append({
                "role": "user",
                "content": message.get("content") or "",
                "timestamp": message.get("timestamp"),
                "type": "user",
            })
        elif kind == "agent":
            out.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "timestamp": message.get("timestamp"),
                "type": "agent",
            })
        elif kind == "tool" and message.get("status") in ("success", "error"):
            tool_call_id = message.get("toolCallId") or message.get("id")
            tool_name = message.get("toolName") or "tool"
            if not tool_call_id:
                continue
            try:
                arguments = json.dumps(message.get("toolArguments") or {}, default=str)
            except (TypeError, ValueError):
                arguments = "{}"
            out.append({
                "role": "assistant",
                "content": "",
                "type": "agent",
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": arguments},
                }],
            })
            out.append({
                "role": "tool",
                "content": _tool_result_content(message),
                "tool_call_id": tool_call_id,
                "name": tool_name,
            })
        # 'reasoning' entries are display-only and never replayed.
    return out
