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


class Environment(str, Enum):
    """Environment enumeration."""
    DEV = "dev"
    TEST = "test"
    STAGE = "stage"
    PROD = "prod"


class PathStateStatus(str, Enum):
    """Path state status enumeration."""
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"


class PathState(BaseModel):
    """Individual state within a parallel path."""
    id: str
    name: str
    status: PathStateStatus
    order: int


class ParallelPath(BaseModel):
    """Parallel execution path in state machine."""
    id: str
    name: str
    states: List[PathState]
    required: bool


class StateMachineState(BaseModel):
    """State machine state representation."""
    currentState: str
    parallelPaths: List[ParallelPath]
    completedStates: List[str]
    activeStates: List[str]


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

