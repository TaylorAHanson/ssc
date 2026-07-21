"""
Request database models (SQLAlchemy).
"""
from sqlalchemy import Column, String, DateTime, JSON, Integer, Boolean, ForeignKey, Text, Index
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql import func
from app.db.base import Base


class RequestModel(Base):
    """Request database model."""
    __tablename__ = "requests"

    # Composite index for the paginated list query (filter by type, sort by
    # created_at desc) so it doesn't seq-scan + sort the whole table.
    __table_args__ = (
        Index("ix_requests_type_created_at", "type", "created_at"),
    )

    id = Column(String, primary_key=True)
    type = Column(String, index=True)  # RequestType enum
    title = Column(String)
    status = Column(String, index=True)  # Current state (State Machine reads/writes this)
    requester_email = Column(String, nullable=True, index=True)  # Who created the request
    state_context = Column(JSON)  # Stores variables (workspace_name, config, etc.)
    # Compact, list-view projection of state_context (see app.services.state_summary).
    # The full state_context can be hundreds of MB (a Sentinel run's violations +
    # checks); the list reads THIS small column instead so the big blob is never
    # fetched. Written whenever state_context is persisted; backfilled on startup.
    state_summary = Column(JSON, nullable=True)
    
    # State locking for idempotency
    locked_by = Column(String, nullable=True)  # Worker ID (e.g., 'poll-worker-hostname-12345')
    locked_until = Column(DateTime, nullable=True, index=True)  # Lock expiration timestamp
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Enforcement Sentinel: set on the one scheduled run per local day that emitted
    # the governance digest email. Lets the sentinel run on any cadence (e.g. every
    # 30 min) while sending the digest at most once/day (anchored to a target hour).
    digest_emitted_at = Column(DateTime, nullable=True)
    
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
    
    # User-Agent Conversation
    conversation = Column(JSON, nullable=True)  # Full chat history
    
    # Training flags
    requires_training = Column(Boolean, default=False)
    training_completed = Column(Boolean, default=False)
    
    # Environment
    environment = Column(String, nullable=True)  # 'dev', 'test', 'stage', 'prod'
    
    # Hierarchy (Compound Workflows)
    parent_id = Column(String, ForeignKey("requests.id"), nullable=True)
    root_id = Column(String, ForeignKey("requests.id"), nullable=True)
    
    # Relationships
    children = relationship("RequestModel", 
                          backref=backref("parent", remote_side=[id]),
                          foreign_keys=[parent_id])
    
    approvals = relationship("ApprovalModel", back_populates="request", cascade="all, delete-orphan")
    events = relationship("EventModel", back_populates="request", cascade="all, delete-orphan")
    failures = relationship("FailureModel", back_populates="request", cascade="all, delete-orphan")





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

