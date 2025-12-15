"""
Request business logic service.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.models.request import RequestType, RequestCreate, Request
from app.state_machines.request_state_machine import RequestStateMachine
from app.state_machines.persistence import save_state_machine
from datetime import datetime
import uuid


class RequestService:
    """Service for request business logic."""
    
    @staticmethod
    def create_request(db: Session, request_data: RequestCreate) -> RequestModel:
        """
        Create a new request and initialize state machine.
        
        Args:
            db: Database session
            request_data: Request creation data
            
        Returns:
            Created RequestModel
        """
        request_id = f"req-{uuid.uuid4()}"
        
        # Initialize state machine
        state_machine = RequestStateMachine(
            request_id=request_id,
            request_type=request_data.type
        )
        
        # Create database model
        request = RequestModel(
            id=request_id,
            type=request_data.type.value,
            title=request_data.title,
            status="pending",
            current_state=state_machine.current_state.id,
            state_context=request_data.metadata or {},
            parallel_paths=[{
                "id": path.id,
                "name": path.name,
                "states": [state.dict() for state in path.states],
                "required": path.required
            } for path in state_machine.parallel_paths.values()],
            completed_states=state_machine.completed_states,
            active_states=state_machine.active_states,
            requires_training=request_data.type == RequestType.WORKSPACE_PROVISION,
            environment=request_data.environment.value if request_data.environment else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(request)
        db.commit()
        db.refresh(request)
        
        return request
    
    @staticmethod
    def get_request(db: Session, request_id: str) -> Optional[RequestModel]:
        """Get request by ID."""
        return db.query(RequestModel).filter(RequestModel.id == request_id).first()
    
    @staticmethod
    def get_requests(db: Session, skip: int = 0, limit: int = 100) -> list[RequestModel]:
        """Get all requests with pagination."""
        return db.query(RequestModel).offset(skip).limit(limit).all()
    
    @staticmethod
    def update_request(db: Session, request_id: str, updates: dict) -> Optional[RequestModel]:
        """Update request."""
        request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
        if request:
            for key, value in updates.items():
                setattr(request, key, value)
            request.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(request)
        return request

