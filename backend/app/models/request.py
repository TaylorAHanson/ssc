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
    PROVISIONING = "provisioning"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RequestType(str, Enum):
    """Request type enumeration."""
    WORKSPACE_ACCESS = "workspace_access"
    CATALOG_SCHEMA_TABLE = "catalog_schema_table"
    CATALOG_SCHEMA_TABLE_ACCESS = "catalog_schema_table_access"
    WORKSPACE_PROVISION = "workspace_provision"
    SERVICE_PRINCIPAL = "service_principal"
    DATA_CERTIFICATION = "data_certification" # Legacy, keep for enum compatibility
    REST_API_ACCESS = "rest_api_access"
    BATCH_DATA_ACCESS = "batch_data_access"
    GITHUB_REPO_CREATION = "github_repo_creation"
    PROJECT_ONBOARDING = "project_onboarding"
    DATA_ACCESS_REQUEST = "data_access_request"
    SIMPLE_EMAIL = "simple_email"
    CAMPAIGN = "campaign"
    REPORT_EXECUTION = "report_execution"
    ENFORCEMENT_SENTINEL = "enforcement_sentinel"
    ASSET_DEDUPLICATION = "asset_deduplication"
    ALLOWLIST_EXCEPTION = "allowlist_exception"


class Environment(str, Enum):
    """Environment enumeration."""
    DEV = "dev"
    TEST = "test"
    STAGE = "stage"
    PROD = "prod"


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
    requestType: RequestType
    approvalType: str
    requestedBy: str
    requestedByEmail: str
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


class Request(BaseModel):
    """Request model."""
    id: str
    type: RequestType
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


class RequestCreate(BaseModel):
    """Request creation model."""
    type: RequestType
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

