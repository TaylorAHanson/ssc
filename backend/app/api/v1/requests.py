"""
Request API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request as FastAPIRequest
import fastapi
from fastapi.responses import JSONResponse
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.request import Request, RequestCreate, RequestUpdate, StateMachineState, RequestStatus
from app.services.request_service import RequestService
from app.db.session import get_db
from app.db.request import ApprovalModel, RequestModel
from app.state_machines.persistence import load_state_machine
from app.state_machines.facts import add_fact
from datetime import datetime
import json
import logging

from app.api.deps import get_current_user
from app.db.user import UserModel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
@router.get("/", response_model=List[Request])
async def get_requests(
    skip: int = 0,
    limit: int = 100,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all requests."""
    # Build query
    query = db.query(RequestModel)
    
    # Non-admins can only see their own requests
    if not current_user.has_role("platform_admin"):
        query = query.filter(RequestModel.requester_email == current_user.email)
    
    requests = query.offset(skip).limit(limit).all()
    response_list = []
    
    for req in requests:
        # Dynamically calculate state machine view
        try:
            sm = load_state_machine(req, db)
            sm_state = sm.to_state_machine_state()
        except Exception as e:
            # Fallback for corrupted/legacy data
            logger.error(f"ERROR loading SM for {req.id}: {e}", exc_info=True)
            sm_state = StateMachineState(
                currentState=req.current_state or "unknown",
                states=[],
                currentProgress=None
            )

        # Handle invalid status values gracefully
        try:
            request_status = RequestStatus(req.status)
        except ValueError:
            # If status is not in enum (e.g., "failed" from old code), default to pending
            # This handles legacy data or unexpected status values
            request_status = RequestStatus.PENDING
        
        response_list.append(Request(
            id=req.id,
            type=req.type,
            title=req.title,
            status=request_status,
            createdAt=req.created_at,
            updatedAt=req.updated_at,
            stateMachine=sm_state,
            requiresTraining=req.requires_training,
            trainingCompleted=req.training_completed,
            environment=req.environment,
            requester_email=req.requester_email,
            lastError=req.last_error,
            metadata=req.state_context or {},
            conversation=req.conversation
        ))
    return response_list


@router.get("/{request_id}", response_model=Request)
async def get_request(
    request_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific request by ID."""
    request_model = RequestService.get_request(db, request_id)
    if not request_model:
        raise HTTPException(status_code=404, detail="Request not found")
        
    # Check permission
    if not current_user.has_role("platform_admin") and request_model.requester_email != current_user.email:
        logger.warning(f"Unauthorized access attempt to {request_id} by {current_user.email}")
        raise HTTPException(status_code=403, detail="Not authorized to view this request")
        
    # Dynamically calculate state machine view
    try:
        sm = load_state_machine(request_model, db)
        sm_state = sm.to_state_machine_state()
    except Exception as e:
        logger.error(f"ERROR loading SM for {request_id}: {e}", exc_info=True)
        sm_state = StateMachineState(
            currentState=request_model.current_state or "unknown",
            states=[],
            currentProgress=None
        )
    
    # Handle invalid status values gracefully
    try:
        request_status = RequestStatus(request_model.status)
    except ValueError:
        # If status is not in enum (e.g., "failed" from old code), default to pending
        request_status = RequestStatus.PENDING
    
    return Request(
            id=request_model.id,
            type=request_model.type,
            title=request_model.title,
            status=request_status,
            createdAt=request_model.created_at,
            updatedAt=request_model.updated_at,
            stateMachine=sm_state,
            requiresTraining=request_model.requires_training,
            trainingCompleted=request_model.training_completed,
            environment=request_model.environment,
            lastError=request_model.last_error,
            metadata=request_model.state_context or {},
            conversation=request_model.conversation
        )


@router.get("/{request_id}/status")
async def get_request_status(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get just the status of a request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return {
        "status": request.status,
        "current_state": request.current_state,
        "updated_at": request.updated_at
    }


@router.post("")
@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_request(
    request_data: RequestCreate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new request."""
    # Set the requester email from authenticated user
    request_data.requester_email = current_user.email
    
    # Inject user context into metadata if missing
    if not request_data.metadata:
        request_data.metadata = {}
        
    if "requested_by" not in request_data.metadata:
        request_data.metadata["requested_by"] = current_user.full_name or current_user.email
        
    if "requested_by_email" not in request_data.metadata:
        request_data.metadata["requested_by_email"] = current_user.email
            
    request_obj = RequestService.create_request(db, request_data)
    return {
        "request_id": request_obj.id,
        "status": request_obj.status,
        "message": "Request created successfully"
    }


@router.post("/{request_id}/approve", status_code=status.HTTP_200_OK)
async def approve_request(
    request_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Approve a request using fact-based approach.
    
    Records a fact (approval_received) - this is the source of truth.
    The state machine will reconcile state based on facts.
    """
    # Find pending approval for this request
    approval = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request_id,
        ApprovalModel.status == "pending"
    ).first()
    
    if not approval:
        raise HTTPException(status_code=404, detail="No pending approval found for this request")
    
    approved_by = current_user.email
    
    # Update approval status immediately
    approval.status = "approved"
    approval.approved_by = approved_by
    approval.approved_at = datetime.utcnow()
    
    # Record fact (this is the source of truth)
    add_fact(
        db, request_id, "approval_received",
        {
            "approval_type": approval.approval_type,
            "approved_by": approved_by,
            "approved_at": datetime.utcnow().isoformat()
        },
        actor=approved_by
    )
    
    db.commit()
    
    return {"status": "approved", "message": "Approval recorded. State will be updated by poller."}


@router.post("/{request_id}/reject", status_code=status.HTTP_200_OK)
async def reject_request(
    request_id: str,
    rejection_data: dict,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reject a request using fact-based approach.
    
    Records a fact (request_rejected) - this is the source of truth.
    The state machine will reconcile state based on facts.
    """
    # Find pending approval for this request
    approval = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request_id,
        ApprovalModel.status == "pending"
    ).first()
    
    if not approval:
        raise HTTPException(status_code=404, detail="No pending approval found for this request")
    
    rejected_by = current_user.email
    rejection_note = rejection_data.get("rejection_note")
    
    # Update approval status immediately
    approval.status = "rejected"
    approval.rejected_by = rejected_by
    approval.rejection_note = rejection_note
    approval.rejected_at = datetime.utcnow()
    
    # Record fact (this is the source of truth)
    add_fact(
        db, request_id, "request_rejected",
        {
            "rejected_by": rejected_by,
            "rejection_note": rejection_note,
            "approval_type": approval.approval_type
        },
        actor=rejected_by
    )
    
    db.commit()
    
    return {"status": "rejected", "message": "Rejection recorded. State will be updated by poller."}


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Delete a request."""
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Only admins or owner can delete
    if not current_user.has_role("platform_admin") and request.requester_email != current_user.email:
        raise HTTPException(status_code=403, detail="Not authorized to delete this request")
    
    db.delete(request)
    db.commit()
    return None

@router.post("/{request_id}/complete-training", status_code=status.HTTP_200_OK)
async def complete_training(
    request_id: str,
    db: Session = Depends(get_db)
):
    """
    Mark training as complete using fact-based approach.
    
    Records a fact (training_completed) - this is the source of truth.
    The state machine will reconcile state based on facts.
    """
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
        
    if not request.requires_training:
        raise HTTPException(status_code=400, detail="Request does not require training")
    
    # Record fact (this is the source of truth)
    add_fact(
        db, request_id, "training_completed",
        {},
        actor="user"  # TODO: Get from auth context
    )
    
    db.commit()
    
    return {
        "status": "success",
        "message": "Training completion recorded. State will be updated by poller.",
        "request_id": request.id
    }
