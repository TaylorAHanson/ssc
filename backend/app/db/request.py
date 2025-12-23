"""
Request database models (SQLAlchemy).
"""
from sqlalchemy import Column, String, DateTime, JSON, Integer, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class RequestModel(Base):
    """Request database model."""
    __tablename__ = "requests"
    
    id = Column(String, primary_key=True)
    type = Column(String)  # RequestType enum
    title = Column(String)
    status = Column(String)  # Current state (State Machine reads/writes this)
    state_context = Column(JSON)  # Stores variables (workspace_name, config, etc.)
    
    # State locking for idempotency
    locked_by = Column(String, nullable=True)  # Worker ID (e.g., 'poll-worker-hostname-12345')
    locked_until = Column(DateTime, nullable=True)  # Lock expiration timestamp
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # State machine state (serialized)
    current_state = Column(String)  # Current state ID
    parallel_paths = Column(JSON)  # Serialized ParallelPath objects
    completed_states = Column(JSON)  # List of completed state IDs
    active_states = Column(JSON)  # List of active state IDs
    
    # Failure tracking
    failure_count = Column(Integer, default=0)  # Number of failures
    last_failure = Column(DateTime, nullable=True)  # Last failure timestamp
    last_error = Column(JSON, nullable=True)  # Last error details
    retry_count = Column(Integer, default=0)  # Current retry attempt
    max_retries = Column(Integer, default=3)  # Maximum retries allowed
    
    # Training flags
    requires_training = Column(Boolean, default=False)
    training_completed = Column(Boolean, default=False)
    
    # Environment
    environment = Column(String, nullable=True)  # 'dev', 'test', 'stage', 'prod'
    
    # Relationships
    approvals = relationship("ApprovalModel", back_populates="request", cascade="all, delete-orphan")
    events = relationship("EventModel", back_populates="request", cascade="all, delete-orphan")
    failures = relationship("FailureModel", back_populates="request", cascade="all, delete-orphan")


class ApprovalModel(Base):
    """Approval database model."""
    __tablename__ = "approvals"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    approval_type = Column(String)  # 'manager', 'data_owner', 'platform_admin', etc.
    requested_by = Column(String)
    requested_by_email = Column(String)
    status = Column(String)  # 'pending', 'approved', 'rejected', 'delegated'
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_by = Column(String, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_note = Column(String, nullable=True)
    delegated_to = Column(String, nullable=True)
    delegated_to_email = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    request = relationship("RequestModel", back_populates="approvals")


class EventModel(Base):
    """Event tracking database model."""
    __tablename__ = "events"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    event_type = Column(String)  # 'state_transition', 'approval', 'notification', etc.
    event_data = Column(JSON)  # Event-specific data
    created_at = Column(DateTime, server_default=func.now())
    
    request = relationship("RequestModel", back_populates="events")


class FailureModel(Base):
    """Failure tracking database model."""
    __tablename__ = "failures"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    task_id = Column(String)  # Worker/task ID
    failure_type = Column(String)  # 'provider_error', 'tool_error', 'timeout', 'validation_error'
    error_message = Column(String)
    error_details = Column(JSON)  # Full error stack trace, context
    retry_count = Column(Integer)  # Retry attempt number
    occurred_at = Column(DateTime, server_default=func.now())
    resolved = Column(Boolean, default=False)  # Whether failure was resolved
    resolved_at = Column(DateTime, nullable=True)
    
    request = relationship("RequestModel", back_populates="failures")


class DelegationModel(Base):
    """Delegation database model for 'Delegate All' functionality."""
    __tablename__ = "delegations"
    
    id = Column(String, primary_key=True)
    delegator_email = Column(String, nullable=False)
    delegatee_email = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

