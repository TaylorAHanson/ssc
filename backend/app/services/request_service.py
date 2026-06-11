"""
Request business logic service.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.models.request import RequestType, RequestCreate, Request
from app.models.request import RequestStatus
from datetime import datetime, timezone
import uuid


class RequestService:
    """Service for request business logic."""
    
    @staticmethod
    def create_request(db: Session, request_data: RequestCreate) -> RequestModel:
        """
        Create a new request and initialize state machine.
        """
        # Double check enum validation (Pydantic does this, but being explicit)
        if not isinstance(request_data.type, RequestType):
            try:
                # Try to cast/validate if it's a string
                request_data.type = RequestType(request_data.type)
            except ValueError:
                raise ValueError(f"Invalid request type: {request_data.type}")

        request_id = f"req-{uuid.uuid4()}"
        
        # Create minimal database model first (needed for SM init)
        request = RequestModel(
            id=request_id,
            type=request_data.type.value,
            title=request_data.title,
            status="pending",
            current_state="pending",
            state_context=request_data.metadata or {},
            requester_email=request_data.requester_email,
            requires_training=request_data.type == RequestType.WORKSPACE_PROVISION,
            environment=request_data.environment.value if request_data.environment else None,
            conversation=request_data.conversation,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        # V2: no state-machine init. The durable graph starts on the first
        # poller pass; we just persist the request in its initial state.
        request.status = RequestStatus.PENDING.value
        request.current_state = "pending"

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
