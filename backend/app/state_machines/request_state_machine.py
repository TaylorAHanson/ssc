"""
Request state machine using python-statemachine.
"""
from statemachine import StateMachine, State
from typing import Optional, Dict, Any
from datetime import datetime
from app.models.request import RequestStatus, RequestType, StateMachineState, ParallelPath, PathState, PathStateStatus
from pydantic import BaseModel


class RequestStateMachine(StateMachine):
    """
    State machine for managing request lifecycle.
    
    States:
    - pending: Initial state when request is created
    - manager_approval: Waiting for manager approval
    - training_pending: Waiting for training completion
    - provisioning: Request is being provisioned
    - completed: Request has been completed
    - rejected: Request was rejected
    """
    
    # Define states
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    training_pending = State("training_pending")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")
    
    # Define transitions
    submit_for_approval = pending.to(manager_approval)
    require_training = pending.to(training_pending) | manager_approval.to(training_pending)
    approve = manager_approval.to(provisioning) | training_pending.to(provisioning)
    start_provisioning = manager_approval.to(provisioning) | training_pending.to(provisioning)
    complete = provisioning.to(completed)
    reject = manager_approval.to(rejected) | pending.to(rejected)
    
    def __init__(self, request_id: str, request_type: RequestType, **kwargs):
        super().__init__(**kwargs)
        self.request_id = request_id
        self.request_type = request_type
        self.parallel_paths: Dict[str, ParallelPath] = {}
        self.completed_states: list[str] = []
        self.active_states: list[str] = [self.current_state.id]
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self._initialize_parallel_paths()
    
    def _initialize_parallel_paths(self):
        """Initialize parallel paths based on request type."""
        if self.request_type == RequestType.WORKSPACE_PROVISION:
            # Approval path
            approval_path = ParallelPath(
                id="approval",
                name="Approval Path",
                required=True,
                states=[
                    PathState(
                        id="manager_approval",
                        name="Manager Approval",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                    PathState(
                        id="budget_approval",
                        name="Budget Approval",
                        status=PathStateStatus.PENDING,
                        order=2
                    ),
                ]
            )
            
            # Training path
            training_path = ParallelPath(
                id="training",
                name="Training Path",
                required=True,
                states=[
                    PathState(
                        id="training_pending",
                        name="Training Completion",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                ]
            )
            
            self.parallel_paths = {
                "approval": approval_path,
                "training": training_path,
            }
        
        elif self.request_type in [
            RequestType.CATALOG_SCHEMA_TABLE_ACCESS,
            RequestType.BATCH_DATA_ACCESS,
        ]:
            # Data owner approval path
            approval_path = ParallelPath(
                id="approval",
                name="Approval Path",
                required=True,
                states=[
                    PathState(
                        id="data_owner_approval",
                        name="Data Owner Approval",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                ]
            )
            
            self.parallel_paths = {"approval": approval_path}
        
        elif self.request_type == RequestType.SERVICE_PRINCIPAL:
            # Platform admin approval path
            approval_path = ParallelPath(
                id="approval",
                name="Approval Path",
                required=True,
                states=[
                    PathState(
                        id="platform_admin_approval",
                        name="Platform Admin Approval",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                ]
            )
            
            # Provisioning path
            provisioning_path = ParallelPath(
                id="provisioning",
                name="Provisioning Path",
                required=True,
                states=[
                    PathState(
                        id="provisioning",
                        name="Service Principal Creation",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                    PathState(
                        id="permissions_setup",
                        name="Permissions Setup",
                        status=PathStateStatus.PENDING,
                        order=2
                    ),
                ]
            )
            
            self.parallel_paths = {
                "approval": approval_path,
                "provisioning": provisioning_path,
            }
        
        elif self.request_type == RequestType.REST_API_ACCESS:
            # Security review path
            approval_path = ParallelPath(
                id="approval",
                name="Approval Path",
                required=True,
                states=[
                    PathState(
                        id="security_review",
                        name="Security Review",
                        status=PathStateStatus.PENDING,
                        order=1
                    ),
                    PathState(
                        id="api_access_grant",
                        name="API Access Grant",
                        status=PathStateStatus.PENDING,
                        order=2
                    ),
                ]
            )
            
            self.parallel_paths = {"approval": approval_path}
    
    def update_state_in_path(self, path_id: str, state_id: str, status: PathStateStatus):
        """Update the status of a state within a parallel path."""
        if path_id in self.parallel_paths:
            path = self.parallel_paths[path_id]
            for state in path.states:
                if state.id == state_id:
                    state.status = status
                    if status == PathStateStatus.COMPLETED:
                        if state_id not in self.completed_states:
                            self.completed_states.append(state_id)
                        if state_id in self.active_states:
                            self.active_states.remove(state_id)
                    elif status == PathStateStatus.ACTIVE:
                        if state_id not in self.active_states:
                            self.active_states.append(state_id)
                    self.updated_at = datetime.utcnow()
                    return True
        return False
    
    def to_state_machine_state(self) -> StateMachineState:
        """Convert to StateMachineState model."""
        # Convert ParallelPath objects to dicts
        parallel_paths_dicts = []
        for path in self.parallel_paths.values():
            parallel_paths_dicts.append({
                "id": path.id,
                "name": path.name,
                "states": [state.dict() for state in path.states],
                "required": path.required
            })
        
        return StateMachineState(
            currentState=self.current_state.id,
            parallelPaths=parallel_paths_dicts,
            completedStates=self.completed_states.copy(),
            activeStates=self.active_states.copy(),
        )
    
    def get_status(self) -> RequestStatus:
        """Get current request status."""
        status_map = {
            "pending": RequestStatus.PENDING,
            "manager_approval": RequestStatus.MANAGER_APPROVAL,
            "training_pending": RequestStatus.TRAINING_PENDING,
            "provisioning": RequestStatus.PROVISIONING,
            "completed": RequestStatus.COMPLETED,
            "rejected": RequestStatus.REJECTED,
        }
        return status_map.get(self.current_state.id, RequestStatus.PENDING)

