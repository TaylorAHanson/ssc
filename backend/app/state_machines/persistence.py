"""
State machine persistence to database.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.state_machines.request_state_machine import RequestStateMachine
from app.models.request import RequestType, StateMachineState
import json


def load_state_machine(request: RequestModel) -> RequestStateMachine:
    """
    Load state machine from database model.
    
    Args:
        request: RequestModel from database
        
    Returns:
        RequestStateMachine instance
    """
    request_type = RequestType(request.type)
    state_machine = RequestStateMachine(
        request_id=request.id,
        request_type=request_type
    )
    
    # Restore state
    if request.current_state:
        # Set current state
        state_machine.current_state = getattr(state_machine, request.current_state)
    
        # Restore parallel paths
        if request.parallel_paths:
            from app.models.request import ParallelPath, PathState, PathStateStatus
            for path_dict in request.parallel_paths:
                states = [
                    PathState(
                        id=s["id"],
                        name=s["name"],
                        status=PathStateStatus(s["status"]),
                        order=s["order"]
                    )
                    for s in path_dict.get("states", [])
                ]
                path = ParallelPath(
                    id=path_dict["id"],
                    name=path_dict["name"],
                    states=states,
                    required=path_dict.get("required", True)
                )
                state_machine.parallel_paths[path.id] = path
    
    # Restore completed/active states
    if request.completed_states:
        state_machine.completed_states = request.completed_states
    if request.active_states:
        state_machine.active_states = request.active_states
    
    return state_machine


def save_state_machine(db: Session, request: RequestModel, state_machine: Optional[RequestStateMachine]):
    """
    Save state machine to database model.
    
    Args:
        db: Database session
        request: RequestModel to update
        state_machine: RequestStateMachine instance (None if failed state)
    """
    if state_machine:
        request.current_state = state_machine.current_state.id
        request.status = state_machine.get_status().value
        
        # Serialize parallel paths
        request.parallel_paths = [{
            "id": path.id,
            "name": path.name,
            "states": [{
                "id": state.id,
                "name": state.name,
                "status": state.status.value,
                "order": state.order
            } for state in path.states],
            "required": path.required
        } for path in state_machine.parallel_paths.values()]
        
        # Save completed/active states
        request.completed_states = state_machine.completed_states
        request.active_states = state_machine.active_states
    else:
        # Failed state
        request.status = "failed"
    
    db.commit()

