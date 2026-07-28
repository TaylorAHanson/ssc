"""
Server-side chat transcripts.

Chat history used to live only in the browser's ``localStorage``, which meant it
was lost on a cache clear, invisible on a second device, and unavailable to the
backend — so "what has this user been working on" could not inform anything.

Messages are stored as the client's ``DisplayMessage[]`` array verbatim in a
single JSON column rather than a normalized message table. That is deliberate:
it mirrors exactly what the browser already persisted, so moving the source of
truth to the server is a storage-adapter swap on the frontend instead of a
rewrite, and a transcript is only ever read or written whole.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String

from app.db.base import Base

# Chat surfaces, matching the frontend's session keys. A user has separate
# transcripts per surface (the landing chat, the workflow-authoring assistant,
# the inline agent on Discover).
CHAT_SURFACES = ("unified", "authoring", "discover")


class ChatSessionModel(Base):
    """One conversation transcript belonging to one user."""

    __tablename__ = "chat_sessions"

    # Listing a user's sessions for a surface, newest first, is the hot query.
    # (user_email alone needs no index — it leads the primary key.)
    __table_args__ = (
        Index("ix_chat_sessions_user_surface_updated", "user_email", "surface", "updated_at"),
    )

    # Composite key: the owner comes FIRST, and the id alone is not unique.
    # Session ids are generated client-side, so a lone-id primary key would mean
    # one user's id could collide with another's — turning an honest save into a
    # 500 and letting a probe confirm that someone else's session id exists.
    user_email: str = Column(
        String, primary_key=True,
        comment="Owner's lowercased email. Every query filters on this.",
    )
    id: str = Column(String, primary_key=True, comment="Client-generated UUID for the session")
    surface: str = Column(
        String, nullable=False, default="unified", index=True,
        comment="Which chat surface: unified, authoring, discover",
    )
    title: Optional[str] = Column(
        String, nullable=True, comment="Derived from the first user message, for session lists"
    )

    # The full DisplayMessage[] array as the client renders it.
    messages: list = Column(JSON, nullable=False, default=list)
    # Denormalized so the session list doesn't have to fetch every transcript
    # body just to show a count.
    message_count: int = Column(Integer, nullable=False, default=0)

    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: datetime = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )
