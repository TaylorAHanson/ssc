"""
Approval database models.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base

class ApprovalModel(Base):
    """Approval database model."""
    __tablename__ = "approvals"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False, index=True)
    approval_type = Column(String)  # 'manager', 'data_owner', 'platform_admin', etc.
    requested_by = Column(String)
    requested_by_email = Column(String)
    assigned_to_email = Column(String, nullable=True, index=True)
    assigned_to_role = Column(String, nullable=True, index=True)
    status = Column(String, index=True)  # 'pending', 'approved', 'rejected', 'delegated', 'superseded'
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_by = Column(String, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_note = Column(String, nullable=True)
    delegated_to = Column(String, nullable=True)
    delegated_to_email = Column(String, nullable=True)
    superseded_note = Column(Text, nullable=True)  # Set when parameters are edited & approval is superseded
    # For 'manual_task' items: what the assignee must actually DO before marking it
    # done. Carried from the gate's interrupt payload because the whole point of a
    # manual task is work the platform has no tool for — without the text, the
    # inbox item is an unexplained "something is waiting on you".
    instructions = Column(Text, nullable=True)
    # Optional SLA for manual tasks. A manual task can park a request
    # indefinitely, so the inbox needs to be able to show what's overdue.
    due_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    request = relationship("RequestModel", back_populates="approvals")
