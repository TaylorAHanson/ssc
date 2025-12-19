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


class StateMachineState(BaseModel):
    """State machine state representation - linear flow from python-statemachine."""
    currentState: str
    states: List[Dict[str, Any]]  # List of {id, name, isActive, isCompleted, isInitial, isFinal}


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

