"""
Event tracking database models.
"""
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
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
