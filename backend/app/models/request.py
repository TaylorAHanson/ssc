"""
Request data models.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    """Request status enumeration."""
    PENDING = "pending"
    MANAGER_APPROVAL = "manager_approval"
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
    MARKETPLACE_CERTIFICATION = "marketplace_certification"
    REST_API_ACCESS = "rest_api_access"
    BATCH_DATA_ACCESS = "batch_data_access"
    GITHUB_REPO_CREATION = "github_repo_creation"
    PROJECT_ONBOARDING = "project_onboarding"


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
    facts: Optional[List[Dict[str, Any]]] = None


class StateMachineState(BaseModel):
    """State machine state representation - linear flow from python-statemachine."""
    currentState: str
    states: List[StateInfo]
    currentProgress: Optional[ProgressInfo] = None


class Approval(BaseModel):
    """Approval model."""
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
    lastError: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class RequestCreate(BaseModel):
    """Request creation model."""
    type: RequestType
    title: str
    environment: Optional[Environment] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


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

