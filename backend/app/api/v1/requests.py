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
from app.db.request import ApprovalModel, RequestModel
from app.state_machines.persistence import load_state_machine
from app.state_machines.facts import add_fact
from datetime import datetime
import json

router = APIRouter()


@router.get("/", response_model=List[Request])
async def get_requests(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get all requests."""
    requests = RequestService.get_requests(db, skip=skip, limit=limit)
    response_list = []
    
    for req in requests:
        # Dynamically calculate state machine view
        try:
            sm = load_state_machine(req, db)
            sm_state = sm.to_state_machine_state()
        except Exception as e:
            # Fallback for corrupted/legacy data
            print(f"ERROR loading SM for {req.id}: {e}")
            sm_state = StateMachineState(
                currentState=req.current_state or "unknown",
                states=[]
            )

        response_list.append(Request(
            id=req.id,
            type=req.type,
            title=req.title,
            status=req.status,
            createdAt=req.created_at,
            updatedAt=req.updated_at,
            stateMachine=sm_state,
            requiresTraining=req.requires_training,
            trainingCompleted=req.training_completed,
            environment=req.environment,
            metadata=req.state_context or {}
        ))
    return response_list


@router.get("/{request_id}", response_model=Request)
async def get_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get a specific request by ID."""
    request_model = RequestService.get_request(db, request_id)
    if not request_model:
        raise HTTPException(status_code=404, detail="Request not found")
        
    # Dynamically calculate state machine view
    try:
        sm = load_state_machine(request_model, db)
        sm_state = sm.to_state_machine_state()
    except Exception as e:
        sm_state = StateMachineState(
            currentState=request_model.current_state or "unknown",
            states=[]
        )
        
    return Request(
        id=request_model.id,
        type=request_model.type,
        title=request_model.title,
        status=request_model.status,
        createdAt=request_model.created_at,
        updatedAt=request_model.updated_at,
        stateMachine=sm_state,
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
    """Get just the status of a request."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return {
        "status": request.status,
        "current_state": request.current_state,
        "updated_at": request.updated_at
    }


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_request(
    request_data: RequestCreate,
    db: Session = Depends(get_db)
):
    """Create a new request."""
    request = RequestService.create_request(db, request_data)
    return {
        "request_id": request.id,
        "status": request.status,
        "message": "Request created successfully"
    }


@router.post("/{request_id}/approve", status_code=status.HTTP_200_OK)
async def approve_request(
    request_id: str,
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
    
    approved_by = "api_user"  # TODO: Get from auth context
    
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
    
    rejected_by = "api_user"  # TODO: Get from auth context
    rejection_note = rejection_data.get("rejection_note")
    
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
