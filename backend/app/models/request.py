"""
Request API models (Pydantic).

These models are used for API request/response validation and serialization.
They define the shape of data exposed to the frontend and external consumers.
For database persistence models (SQLAlchemy), see `app.db.request`.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    """Request status enumeration."""
    PENDING = "pending"
    MANAGER_APPROVAL = "manager_approval"
    DATA_OWNER_APPROVAL = "data_owner_approval"
    TRAINING_PENDING = "training_pending"
    # Held while a human completes work the platform has no tool for (a
    # ``manual_task`` gate). Distinct from the approval statuses because nobody is
    # deciding anything — the request is waiting on an off-platform action.
    MANUAL_TASK_PENDING = "manual_task_pending"
    PROVISIONING = "provisioning"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RequestType(str, Enum):
    """System-triggered request types referenced by name in code.

    Request types are **data-driven**: a request's ``type`` is a free string
    validated at creation against the published-workflow registry (DB) + the
    bundled JSON spec catalog — adding a workflow needs no entry here. This enum
    is intentionally slim, holding only the few workflows the platform itself
    triggers internally (cron/poller/system) so that code can reference them by
    a named constant instead of a magic string.
    """
    ENFORCEMENT_SENTINEL = "enforcement_sentinel"
    REPORT_EXECUTION = "report_execution"
    TAG_CHANGE = "tag_change"


class Environment(str, Enum):
    """Environment enumeration."""
    LOCAL = "local"
    DEV = "dev"
    TEST = "test"
    STAGE = "stage"
    PROD = "prod"
    PRODUCTION = "production"


class ApprovalType(str, Enum):
    """Approval type enumeration."""
    MANAGER = "manager"
    DATA_OWNER = "data_owner"
    PLATFORM_ADMIN = "platform_admin"
    SECURITY = "security"


class ProgressInfo(BaseModel):
    """Progress information for long-running operations."""
    message: str
    percent: int
    timestamp: datetime


class StateInfo(BaseModel):
    """Detailed information about a state in the state machine."""
    id: str
    name: str
    isActive: bool
    isCompleted: bool
    isInitial: bool
    isFinal: bool
    completedAt: Optional[datetime] = None
    startedAt: Optional[datetime] = None
    facts: Optional[List[Dict[str, Any]]] = None
    # Gate type for approval/gate states ("data_owner", "manager",
    # "platform_admin", ...); None for non-gate states. Lets the UI identify
    # the approval step and show its assigned approver(s).
    gateType: Optional[str] = None


class StateMachineState(BaseModel):
    """State machine state representation - linear flow from python-statemachine."""
    currentState: str
    states: List[StateInfo]
    currentProgress: Optional[ProgressInfo] = None


class Approval(BaseModel):
    """Approval model.
    
    status values: 'pending', 'approved', 'rejected', 'delegated', 'superseded'
    A 'superseded' approval means the platform admin chose to edit parameters instead
    of approving/rejecting. The new parameters will trigger a fresh plan run.
    """
    id: str
    requestId: str
    requestTitle: str
    requestType: str  # data-driven workflow type (validated against the registry)
    approvalType: str
    requestedBy: str
    requestedByEmail: str
    assignedToEmail: Optional[str] = None
    assignedToRole: Optional[str] = None
    approvedBy: Optional[str] = None
    approvedAt: Optional[datetime] = None
    rejectedBy: Optional[str] = None
    rejectedAt: Optional[datetime] = None
    status: str
    createdAt: datetime
    updatedAt: datetime
    rejectionNote: Optional[str] = None
    delegatedTo: Optional[str] = None
    delegatedToEmail: Optional[str] = None
    supersededNote: Optional[str] = None
    requestConversation: Optional[List[Dict[str, Any]]] = None
    # Workflow input parameters (filtered state_context — excludes internal keys).
    # Displayed to approvers so they can review what the workflow will execute with.
    workflowParameters: Optional[Dict[str, Any]] = None
    # For ``approvalType == "manual_task"``: the work the assignee must complete
    # before marking it done, and an optional due date so an ignored task can be
    # shown as overdue rather than silently parking the request forever.
    instructions: Optional[str] = None
    dueAt: Optional[datetime] = None


class Request(BaseModel):
    """Request model."""
    id: str
    type: str  # data-driven workflow type (validated against the registry)
    title: str
    status: RequestStatus
    createdAt: datetime
    updatedAt: datetime
    stateMachine: StateMachineState
    requiresTraining: Optional[bool] = False
    trainingCompleted: Optional[bool] = False
    environment: Optional[Environment] = None
    requester_email: Optional[str] = None
    lastError: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    conversation: Optional[List[Dict[str, Any]]] = None  # Chat history
    approvals: Optional[List[Approval]] = None


class RequestCreate(BaseModel):
    """Request creation model."""
    type: str  # data-driven workflow type (validated against the registry)
    title: str
    requester_email: Optional[str] = None
    environment: Optional[Environment] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    conversation: Optional[List[Dict[str, Any]]] = None  # Chat history


class RequestUpdate(BaseModel):
    """Request update model."""
    status: Optional[RequestStatus] = None
    stateMachine: Optional[StateMachineState] = None
    trainingCompleted: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class Delegation(BaseModel):
    """Delegation model."""
    id: str
    delegator_email: str
    delegatee_email: str
    start_date: datetime
    end_date: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DelegationCreate(BaseModel):
    """Delegation creation model."""
    delegatee_email: str
    start_date: datetime
    end_date: datetime

