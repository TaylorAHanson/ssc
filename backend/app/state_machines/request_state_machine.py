"""
Request state machine base and implementations.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from statemachine import StateMachine, State
from app.models.request import RequestStatus, RequestType, StateMachineState, ParallelPath, PathState, PathStateStatus
from app.db.request import ApprovalModel, RequestModel
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


class BaseRequestStateMachine(StateMachine):
    """
    Base class for all request state machines.
    """
    
    def __init__(self, request: RequestModel, db: Session, **kwargs):
        self.request = request
        self.db = db
        # We no longer store visual state in the DB.
        # We calculate it on the fly.
        super().__init__(start_value=request.current_state, **kwargs)

    def to_state_machine_state(self) -> StateMachineState:
        """
        Calculate and return the visual state machine representation based on DB facts.
        """
        # 1. Define the ideal structure for this request type
        parallel_paths = self._get_path_definitions()
        
        # Add logging to debug
        logger.info(f"DEBUG: Calculated paths for {self.request.type}: {[p.id for p in parallel_paths]}")
        
        # 2. Determine status of each node based on facts
        completed_states = []
        active_states = []
        
        # We must re-map states to Pydantic models because _get_path_definitions returns Pydantic models
        # but we need to update their status fields which are immutable on Pydantic models by default
        # or just constructing new ones.
        
        # Let's reconstruct the parallel paths with updated statuses
        updated_parallel_paths = []
        
        for path in parallel_paths:
            updated_states = []
            for state in path.states:
                status = self._calculate_node_status(path.id, state.id)
                
                if status == PathStateStatus.COMPLETED:
                    completed_states.append(state.id)
                elif status == PathStateStatus.ACTIVE:
                    active_states.append(state.id)
                
                # Create new state with updated status
                updated_states.append(PathState(
                    id=state.id,
                    name=state.name,
                    status=status,
                    order=state.order
                ))
            
            updated_parallel_paths.append(ParallelPath(
                id=path.id,
                name=path.name,
                states=updated_states,
                required=path.required
            ))
        
        # 3. Return the dynamic state
        logger.info(f"DEBUG: Returning state with {len(updated_parallel_paths)} parallel paths")
        return StateMachineState(
            currentState=self.current_state.id,
            parallelPaths=updated_parallel_paths,
            completedStates=completed_states,
            activeStates=active_states,
        )

    def _get_path_definitions(self) -> List[ParallelPath]:
        """Override to define the visual structure (nodes)."""
        return []

    def _calculate_node_status(self, path_id: str, state_id: str) -> PathStateStatus:
        """Override to determine status of a specific node based on DB facts."""
        return PathStateStatus.PENDING

    def save(self):
        """Persist ONLY the core state machine status back to the request model."""
        self.request.current_state = self.current_state.id
        self.request.status = self.get_mapped_status().value
        # We do NOT save parallel_paths, completed_states, or active_states anymore.
        # They are derived.
        self.request.updated_at = datetime.utcnow()

    def get_mapped_status(self) -> RequestStatus:
        """Override to map internal states to RequestStatus enum."""
        return RequestStatus.PENDING

    def create_approval_task(self, approval_type: str):
        """Helper to create an approval record."""
        exists = self.db.query(ApprovalModel).filter(
            ApprovalModel.request_id == self.request.id,
            ApprovalModel.status == "pending",
            ApprovalModel.approval_type == approval_type
        ).first()
        
        if not exists:
            # Check if already approved/rejected recently? 
            # For now, just create if no pending one.
            approval_id = f"app-{datetime.utcnow().timestamp()}"
            new_approval = ApprovalModel(
                id=approval_id,
                request_id=self.request.id,
                approval_type=approval_type,
                requested_by="system",
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            self.db.add(new_approval)
            logger.info(f"Created pending approval {approval_id} ({approval_type}) for request {self.request.id}")


# ==========================================
# Concrete Implementations
# ==========================================

class WorkspaceProvisionStateMachine(BaseRequestStateMachine):
    
    # States
    pending = State("pending", initial=True)
    manager_approval = State("manager_approval")
    training_pending = State("training_pending")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")
    
    # Transitions
    submit = pending.to(manager_approval)
    approve_manager = manager_approval.to(training_pending)
    complete_training = training_pending.to(provisioning)
    finish_provisioning = provisioning.to(completed)
    reject = pending.to(rejected) | manager_approval.to(rejected)

    def get_mapped_status(self) -> RequestStatus:
        mapping = {
            "pending": RequestStatus.PENDING,
            "manager_approval": RequestStatus.MANAGER_APPROVAL,
            "training_pending": RequestStatus.TRAINING_PENDING,
            "provisioning": RequestStatus.PROVISIONING,
            "completed": RequestStatus.COMPLETED,
            "rejected": RequestStatus.REJECTED
        }
        return mapping.get(self.current_state.id, RequestStatus.PENDING)

    def _get_path_definitions(self) -> List[ParallelPath]:
        return [
            ParallelPath(
                id="approval", name="Approval Path", required=True,
                states=[
                    PathState(id="manager_approval", name="Manager Approval", status=PathStateStatus.PENDING, order=1),
                ]
            ),
            ParallelPath(
                id="training", name="Training Path", required=True,
                states=[
                    PathState(id="training_pending", name="Training Completion", status=PathStateStatus.PENDING, order=1),
                ]
            )
        ]

    def _calculate_node_status(self, path_id: str, state_id: str) -> PathStateStatus:
        # 1. Manager Approval Node
        if state_id == "manager_approval":
            # Fact: Do we have an approval?
            approval = self.db.query(ApprovalModel).filter(
                ApprovalModel.request_id == self.request.id,
                ApprovalModel.approval_type == "manager"
            ).order_by(ApprovalModel.updated_at.desc()).first()
            
            if approval and approval.status == "approved":
                return PathStateStatus.COMPLETED
            
            # If not approved, is it active?
            # It's active if we are in 'manager_approval' state or if it's the next logical step
            # Actually, simply: if we are PAST pending, it's at least active.
            if self.current_state.id != "pending":
                return PathStateStatus.ACTIVE
            
            return PathStateStatus.PENDING

        # 2. Training Node
        if state_id == "training_pending":
            # Fact: Is training marked complete?
            if self.request.training_completed:
                return PathStateStatus.COMPLETED
            
            # If not complete, is it active?
            # It should be active if Manager Approval is done OR if we are in training state
            # Simple rule: If we are not pending, show as active (waiting)
            # Or better: If Manager Approval is completed, then this is active.
            
            # Check Manager Approval status to determine dependency
            manager_status = self._calculate_node_status("approval", "manager_approval")
            if manager_status == PathStateStatus.COMPLETED or self.current_state.id == "training_pending":
                return PathStateStatus.ACTIVE
                
            # Special case: If we skipped straight to training (e.g. auto-approve), it's active
            if self.current_state.id in ["training_pending", "provisioning", "completed"]:
                 return PathStateStatus.ACTIVE

            return PathStateStatus.PENDING
            
        return PathStateStatus.PENDING

    # Hooks
    def on_enter_manager_approval(self):
        self.create_approval_task("manager")


class DataAccessStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    data_owner_approval = State("data_owner_approval")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")

    submit = pending.to(data_owner_approval)
    approve_owner = data_owner_approval.to(provisioning)
    finish_provisioning = provisioning.to(completed)
    reject = pending.to(rejected) | data_owner_approval.to(rejected)

    def get_mapped_status(self) -> RequestStatus:
        mapping = {
            "pending": RequestStatus.PENDING,
            "data_owner_approval": RequestStatus.MANAGER_APPROVAL,
            "provisioning": RequestStatus.PROVISIONING,
            "completed": RequestStatus.COMPLETED,
            "rejected": RequestStatus.REJECTED
        }
        return mapping.get(self.current_state.id, RequestStatus.PENDING)

    def _get_path_definitions(self) -> List[ParallelPath]:
        return [
            ParallelPath(
                id="approval", name="Approval Path", required=True,
                states=[
                    PathState(id="data_owner_approval", name="Data Owner Approval", status=PathStateStatus.PENDING, order=1),
                ]
            )
        ]

    def _calculate_node_status(self, path_id: str, state_id: str) -> PathStateStatus:
        if state_id == "data_owner_approval":
            approval = self.db.query(ApprovalModel).filter(
                ApprovalModel.request_id == self.request.id,
                ApprovalModel.approval_type == "data_owner"
            ).order_by(ApprovalModel.updated_at.desc()).first()
            
            if approval and approval.status == "approved":
                return PathStateStatus.COMPLETED
            
            if self.current_state.id != "pending":
                return PathStateStatus.ACTIVE
                
        return PathStateStatus.PENDING

    def on_enter_data_owner_approval(self):
        self.create_approval_task("data_owner")


class ServicePrincipalStateMachine(BaseRequestStateMachine):
    
    pending = State("pending", initial=True)
    platform_admin_approval = State("platform_admin_approval")
    provisioning = State("provisioning")
    completed = State("completed")
    rejected = State("rejected")

    submit = pending.to(platform_admin_approval)
    approve_admin = platform_admin_approval.to(provisioning)
    finish_provisioning = provisioning.to(completed)
    reject = pending.to(rejected) | platform_admin_approval.to(rejected)

    def get_mapped_status(self) -> RequestStatus:
        mapping = {
            "pending": RequestStatus.PENDING,
            "platform_admin_approval": RequestStatus.MANAGER_APPROVAL, 
            "provisioning": RequestStatus.PROVISIONING,
            "completed": RequestStatus.COMPLETED,
            "rejected": RequestStatus.REJECTED
        }
        return mapping.get(self.current_state.id, RequestStatus.PENDING)

    def _get_path_definitions(self) -> List[ParallelPath]:
        return [
            ParallelPath(
                id="approval", name="Approval Path", required=True,
                states=[
                    PathState(id="platform_admin_approval", name="Platform Admin Approval", status=PathStateStatus.PENDING, order=1),
                ]
            ),
            ParallelPath(
                id="provisioning", name="Provisioning Path", required=True,
                states=[
                    PathState(id="provisioning", name="Service Principal Creation", status=PathStateStatus.PENDING, order=1),
                    PathState(id="permissions_setup", name="Permissions Setup", status=PathStateStatus.PENDING, order=2),
                ]
            )
        ]

    def _calculate_node_status(self, path_id: str, state_id: str) -> PathStateStatus:
        # Approval Node
        if state_id == "platform_admin_approval":
            approval = self.db.query(ApprovalModel).filter(
                ApprovalModel.request_id == self.request.id,
                ApprovalModel.approval_type == "platform_admin"
            ).order_by(ApprovalModel.updated_at.desc()).first()
            
            if approval and approval.status == "approved":
                return PathStateStatus.COMPLETED
            if self.current_state.id != "pending":
                return PathStateStatus.ACTIVE
        
        # Provisioning Nodes
        # These are purely state-based for now as we don't have separate fact tables for them yet
        if state_id == "provisioning":
            if self.current_state.id in ["provisioning", "completed"]:
                # If we are in provisioning, assume active until completed?
                # Or if we have sub-states?
                # For now: if completed -> COMPLETED, else if in provisioning -> ACTIVE
                if self.current_state.id == "completed":
                    return PathStateStatus.COMPLETED
                return PathStateStatus.ACTIVE
            # If admin approval is done, this should probably be active
            if self._calculate_node_status("approval", "platform_admin_approval") == PathStateStatus.COMPLETED:
                return PathStateStatus.ACTIVE

        if state_id == "permissions_setup":
            # Dependent on previous step
            if self.current_state.id == "completed":
                return PathStateStatus.COMPLETED
            # If provisioning is done (mocked here by assuming if we are in provisioning state, first step is done?)
            # Simplified: just follow main state
            if self.current_state.id == "provisioning":
                return PathStateStatus.PENDING # Wait for step 1
                
        return PathStateStatus.PENDING

    def on_enter_platform_admin_approval(self):
        self.create_approval_task("platform_admin")


# Factory to get correct machine
def get_state_machine(request: RequestModel, db: Session) -> BaseRequestStateMachine:
    """Factory to return the appropriate state machine instance."""
    try:
        # Ensure we have a valid enum
        r_type = RequestType(request.type)
    except ValueError:
        logger.warning(f"Invalid request type '{request.type}' for request {request.id}. Defaulting to WORKSPACE_PROVISION.")
        r_type = RequestType.WORKSPACE_PROVISION
    
    if r_type == RequestType.WORKSPACE_PROVISION:
        return WorkspaceProvisionStateMachine(request, db)
    
    elif r_type in [RequestType.CATALOG_SCHEMA_TABLE_ACCESS, RequestType.BATCH_DATA_ACCESS]:
        return DataAccessStateMachine(request, db)
        
    elif r_type == RequestType.SERVICE_PRINCIPAL:
        return ServicePrincipalStateMachine(request, db)
    
    # Fallback / Default for others (implement specific ones as needed)
    return WorkspaceProvisionStateMachine(request, db)
