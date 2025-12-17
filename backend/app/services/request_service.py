"""
Request business logic service.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.models.request import RequestType, RequestCreate, Request
from app.state_machines.persistence import save_state_machine
from app.state_machines.request_state_machine import get_state_machine
from datetime import datetime
import uuid


class RequestService:
    """Service for request business logic."""
    
    @staticmethod
    def create_request(db: Session, request_data: RequestCreate) -> RequestModel:
        """
        Create a new request and initialize state machine.
        """
        request_id = f"req-{uuid.uuid4()}"
        
        # Create minimal database model first (needed for SM init)
        request = RequestModel(
            id=request_id,
            type=request_data.type.value,
            title=request_data.title,
            status="pending",
            current_state="pending",
            state_context=request_data.metadata or {},
            requires_training=request_data.type == RequestType.WORKSPACE_PROVISION,
            environment=request_data.environment.value if request_data.environment else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Initialize state machine (this will set up parallel paths)
        sm = get_state_machine(request, db)
        
        # Save initial state back to request
        sm.save()
        
        db.add(request)
        db.commit()
        db.refresh(request)
        
        return request

    @staticmethod
    def get_requests(db: Session, skip: int = 0, limit: int = 100):
        return db.query(RequestModel).offset(skip).limit(limit).all()

    @staticmethod
    def get_request(db: Session, request_id: str) -> Optional[RequestModel]:
        return db.query(RequestModel).filter(RequestModel.id == request_id).first()
