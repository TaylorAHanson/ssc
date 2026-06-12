"""
Event tracking database models.
"""
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base

class EventModel(Base):
    """Event tracking database model."""
    __tablename__ = "events"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    event_type = Column(String)  # 'state_transition', 'approval', 'notification', etc.
    event_data = Column(JSON)  # Event-specific data
    created_at = Column(DateTime, server_default=func.now())
    
    request = relationship("RequestModel", back_populates="events")

    # The facts API (state_machines/facts.py) filters every render/idempotency
    # check on request_id (+ optionally event_type), so a composite index on the
    # hot path avoids a sequential scan of the whole events table at scale.
    __table_args__ = (
        Index("ix_events_request_id_type", "request_id", "event_type"),
    )
