"""
Request business logic service.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.models.request import RequestCreate
from app.models.request import RequestStatus
from app.services.workflow_service import WorkflowService
from datetime import datetime, timezone
import uuid


class RequestService:
    """Service for request business logic."""
    
    @staticmethod
    def create_request(db: Session, request_data: RequestCreate) -> RequestModel:
        """
        Create a new request and initialize state machine.
        """
        # Request types are data-driven: validate against the published-workflow
        # registry + bundled catalog instead of a fixed enum.
        req_type = request_data.type
        if not WorkflowService.is_known_request_type(db, req_type):
            raise ValueError(
                f"Unknown request type '{req_type}'. Author and publish a workflow "
                f"with this type before submitting requests for it."
            )

        request_id = f"req-{uuid.uuid4()}"
        
        # Create minimal database model first (needed for SM init)
        request = RequestModel(
            id=request_id,
            type=req_type,
            title=request_data.title,
            status="pending",
            current_state="pending",
            state_context=request_data.metadata or {},
            requester_email=request_data.requester_email,
            # Derived from the workflow's own definition (has a training gate?),
            # not a hardcoded per-type check.
            requires_training=WorkflowService.spec_requires_training(db, req_type),
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
