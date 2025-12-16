"""
Request API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.request import Request, RequestCreate, RequestUpdate, StateMachineState
from app.services.request_service import RequestService
from app.db.session import get_db
from app.db.request import ApprovalModel
from datetime import datetime

router = APIRouter()


@router.get("/", response_model=List[Request])
async def get_requests(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get all requests."""
    requests = RequestService.get_requests(db, skip=skip, limit=limit)
    return [
        Request(
            id=req.id,
            type=req.type,
            title=req.title,
            status=req.status,
            createdAt=req.created_at,
            updatedAt=req.updated_at,
            stateMachine=StateMachineState(
                currentState=req.current_state,
                parallelPaths=req.parallel_paths or [],
                completedStates=req.completed_states or [],
                activeStates=req.active_states or []
            ),
            requiresTraining=req.requires_training,
            trainingCompleted=req.training_completed,
            environment=req.environment,
            metadata=req.state_context or {}
        ) for req in requests
    ]


@router.get("/{request_id}", response_model=Request)
async def get_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get a specific request by ID."""
    request_model = RequestService.get_request(db, request_id)
    if not request_model:
        raise HTTPException(status_code=404, detail="Request not found")
        
    # Manually construct the response to ensure field matching
    return Request(
        id=request_model.id,
        type=request_model.type,
        title=request_model.title,
        status=request_model.status,
        createdAt=request_model.created_at,
        updatedAt=request_model.updated_at,
        stateMachine=StateMachineState(
            currentState=request_model.current_state,
            parallelPaths=request_model.parallel_paths or [],
            completedStates=request_model.completed_states or [],
            activeStates=request_model.active_states or []
        ),
        requiresTraining=request_model.requires_training,
        trainingCompleted=request_model.training_completed,
        environment=request_model.environment,
        metadata=request_model.state_context or {}
    )


@router.get("/{request_id}/status")
async def get_request_status(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get request status for polling."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return {
        "id": request.id,
        "status": request.status,
        "current_state": request.current_state,
        "progress": request.state_context.get("progress") if request.state_context else None,
        "retry_count": request.retry_count,
        "last_error": request.last_error
    }


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def create_request(
    request_data: RequestCreate,
    db: Session = Depends(get_db)
):
    """Create a new request."""
    # Create request in database
    request = RequestService.create_request(db, request_data)
    
    # The background poller will pick up the 'pending' request automatically.
    
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "request_id": request.id,
            "status": "pending",
            "message": "Request created and queued for processing"
        }
    )


@router.patch("/{request_id}", response_model=Request)
async def update_request(
    request_id: str,
    request_update: RequestUpdate,
    db: Session = Depends(get_db)
):
    """Update a request."""
    updates = request_update.dict(exclude_unset=True)
    request = RequestService.update_request(db, request_id, updates)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


@router.post("/{request_id}/approve", status_code=status.HTTP_202_ACCEPTED)
async def approve_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Approve request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Find pending approval
    approval = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request_id,
        ApprovalModel.status == "pending"
    ).first()
    
    if approval:
        # Update existing approval
        approval.status = "approved"
        approval.approved_by = "admin" # placeholder
        approval.approved_at = datetime.utcnow()
        db.commit()
    else:
        # Create new approval record if none exists (fallback)
        approval = ApprovalModel(
            id=f"app-{datetime.utcnow().timestamp()}",
            request_id=request_id,
            approval_type="manager", # Default for now
            requested_by="system", # placeholder
            status="approved",
            approved_by="admin", # placeholder
            approved_at=datetime.utcnow()
        )
        db.add(approval)
        db.commit()

    return {"status": "accepted", "message": "Approval recorded"}


@router.post("/{request_id}/reject", status_code=status.HTTP_202_ACCEPTED)
async def reject_request(
    request_id: str,
    rejection_note: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Reject request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Find pending approval
    approval = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request_id,
        ApprovalModel.status == "pending"
    ).first()
    
    if approval:
        # Update existing approval
        approval.status = "rejected"
        approval.rejection_note = rejection_note
        approval.approved_at = datetime.utcnow() # using approved_at for decision time
        db.commit()
    else:
        # Create rejection record
        approval = ApprovalModel(
            id=f"app-{datetime.utcnow().timestamp()}",
            request_id=request_id,
            approval_type="manager",
            requested_by="system",
            status="rejected",
            rejection_note=rejection_note,
            approved_at=datetime.utcnow()
        )
        db.add(approval)
        db.commit()

    return {"status": "accepted", "message": "Rejection recorded"}


@router.get("/{request_id}/failures")
async def get_failures(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get failure history for a request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return request.failures


@router.post("/{request_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Manually retry a failed request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Reset to pending/retry state
    request.retry_count = 0
    request.status = "pending" # Reset to pending to restart flow or retry
    request.last_error = None
    db.commit()
    
    return {"status": "accepted", "message": "Retry queued"}
