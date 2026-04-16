"""
Request API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request as FastAPIRequest
import fastapi
from fastapi.responses import JSONResponse
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.request import Request, RequestCreate, RequestUpdate, StateMachineState, RequestStatus, RequestType
from app.services.request_service import RequestService
from app.db.session import get_db
from app.db import ApprovalModel, RequestModel
from app.state_machines.persistence import load_state_machine
from app.state_machines.facts import add_fact
from datetime import datetime
import json
import logging

from app.api.deps import get_current_user
from app.db.user import UserModel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=List[Request])
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
            # Handle invalid types by skipping - prevents whole page crash
            try:
                r_type = RequestType(req.type)
            except ValueError:
                logger.error(f"Skipping request {req.id} with invalid type: {req.type}")
                continue

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
            type=r_type,
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


from pydantic import BaseModel as _PydanticBase

class PaginatedRequestsResponse(_PydanticBase):
    items: List[Request]
    total: int

@router.get("/paginated", response_model=PaginatedRequestsResponse)
async def get_paginated_requests(
    skip: int = 0,
    limit: int = 10,
    type: Optional[str] = None,
    search: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get paginated requests with optional filtering and search."""
    from sqlalchemy import or_
    
    query = db.query(RequestModel)
    
    if not current_user.has_role("platform_admin"):
        query = query.filter(RequestModel.requester_email == current_user.email)
        
    if type:
        query = query.filter(RequestModel.type == type)
        
    if search:
        search_term = f"%{search}%"
        # Search in title, status, environment, ID, or workspace/environment in metadata
        from sqlalchemy import cast, String
        
        # Check if state_context is actually JSONB or just JSON
        query = query.filter(
            or_(
                RequestModel.title.ilike(search_term),
                RequestModel.status.ilike(search_term),
                RequestModel.environment.ilike(search_term),
                RequestModel.id.ilike(search_term),
                cast(RequestModel.state_context, String).ilike(search_term)
            )
        )
        
    # Get total count before pagination
    total = query.count()
    
    # Order by newest first
    requests = query.order_by(RequestModel.created_at.desc()).offset(skip).limit(limit).all()
    response_list = []
    
    for req in requests:
        try:
            try:
                r_type = RequestType(req.type)
            except ValueError:
                continue

            sm = load_state_machine(req, db)
            sm_state = sm.to_state_machine_state()
        except Exception as e:
            sm_state = StateMachineState(
                currentState=req.current_state or "unknown",
                states=[],
                currentProgress=None
            )

        try:
            request_status = RequestStatus(req.status)
        except ValueError:
            request_status = RequestStatus.PENDING
        
        response_list.append(Request(
            id=req.id,
            type=r_type,
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
        
    return PaginatedRequestsResponse(items=response_list, total=total)


@router.get("/{request_id}", response_model=Request)
async def get_request(
    request_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific request by ID."""
    from app.db.session import get_database_url
    db_url = get_database_url()
    logger.info(f"[GET /requests/{request_id}] Using database: {db_url[:50]}...")
    
    request_model = RequestService.get_request(db, request_id)
    if not request_model:
        # Debug: List all request IDs to see what's in the DB
        all_requests = db.query(RequestModel).limit(10).all()
        logger.error(f"[GET /requests/{request_id}] ❌ NOT FOUND. Existing requests: {[r.id for r in all_requests]}")
        raise HTTPException(status_code=404, detail="Request not found")
    
    logger.info(f"[GET /requests/{request_id}] ✅ Found request")
        
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


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
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
    current_user: UserModel = Depends(get_current_user),
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


from pydantic import BaseModel as _PydanticBase

class EditParametersRequest(_PydanticBase):
    parameters: dict
    note: Optional[str] = None


@router.post("/{request_id}/edit-parameters", status_code=status.HTTP_200_OK)
async def edit_parameters(
    request_id: str,
    body: EditParametersRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Edit the workflow input parameters for a request and restart execution.

    This is an ADDITIVE operation — no facts are deleted. Instead:
    1. A `parameters_edited` fact is recorded as an immutable temporal boundary.
    2. The pending approval is marked `superseded` (its history is preserved).
    3. The state_context is updated with the new parameters.
    4. The lock is cleared so the poller picks it up immediately.

    The poller calls tick() → detects `has_parameters_edited=True` → transitions
    through `parameters_updated` → back to `terraform_planning` → fresh plan runs.

    Requires: platform_admin role.
    Only valid when current_state is in the SM's get_editable_states() list.
    """
    from app.state_machines.persistence import load_state_machine

    if not current_user.has_role("platform_admin"):
        raise HTTPException(status_code=403, detail="Only platform admins can edit workflow parameters")

    request_model = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request_model:
        raise HTTPException(status_code=404, detail="Request not found")

    try:
        sm = load_state_machine(request_model, db)
    except Exception as e:
        logger.error(f"[edit-parameters] Failed to load state machine for {request_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load state machine")

    editable_states = sm.get_editable_states()
    if request_model.current_state not in editable_states:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot edit parameters in state '{request_model.current_state}'. "
                f"Edit is only allowed in: {editable_states}"
            )
        )

    old_params = (request_model.state_context or {}).copy()

    # Never allow editing internal identity fields via this endpoint
    _BLOCKED = {"requested_by", "requested_by_email"}
    sanitized = {k: v for k, v in body.parameters.items() if k not in _BLOCKED}

    # Merge (non-destructive — keys not in payload are preserved)
    updated_context = old_params.copy()
    updated_context.update(sanitized)
    request_model.state_context = updated_context

    # Record the immutable audit fact. This timestamp becomes the temporal boundary
    # that separates prior execution facts from the current run.
    add_fact(
        db, request_id, "parameters_edited",
        {
            "edited_by": current_user.email,
            "note": body.note or "",
            "old_params": {k: v for k, v in old_params.items() if k not in _BLOCKED},
            "new_params": sanitized,
        },
        actor=current_user.email
    )

    # Supersede any pending approvals — generated for old parameters.
    # NOT deleted: the history of what happened is preserved.
    pending_approvals = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request_id,
        ApprovalModel.status == "pending"
    ).all()

    for pending in pending_approvals:
        logger.info(f"[edit-parameters] Superseding approval {pending.id} for request {request_id}")
        pending.status = "superseded"
        pending.superseded_note = (
            f"Superseded by parameter edit from {current_user.email}: {body.note or '(no note)'}"
        )

    # Release the lock so the poller processes the state transition immediately
    request_model.locked_by = None
    request_model.locked_until = None
    request_model.updated_at = datetime.utcnow()

    db.commit()

    logger.info(
        f"[edit-parameters] Request {request_id} parameters updated "
        f"by {current_user.email}. Fields: {list(sanitized.keys())}"
    )

    return {
        "status": "parameters_updated",
        "message": (
            "Parameters saved. The pending approval has been superseded. "
            "A new plan will be generated and a fresh approval task created."
        ),
        "request_id": request_id,
        "updated_parameters": list(sanitized.keys()),
    }


class EnforcementActionRequest(_PydanticBase):
    resource_id: str
    resource_type: str
    action: str
    policy_name: str
    reason: Optional[str] = None


@router.post("/{request_id}/enforcement-action", status_code=status.HTTP_200_OK)
async def execute_enforcement_action(
    request_id: str,
    body: EnforcementActionRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Execute a specific enforcement action manually (e.g. from audit mode)."""
    if not current_user.has_role("platform_admin") and not current_user.has_role("governance_admin"):
        raise HTTPException(status_code=403, detail="Not authorized to execute enforcement actions")

    from app.core.config import settings
    from app.providers.databricks.client import DatabricksProvider
    from app.providers.databricks.handlers import (
        AppResourceHandler, ClusterResourceHandler, JobResourceHandler,
        SqlWarehouseResourceHandler, DashboardResourceHandler,
        GenieSpaceResourceHandler, ServicePrincipalResourceHandler,
        NotebookResourceHandler, VolumeResourceHandler
    )
    from app.providers.databricks.handlers.dataset_handler import DatasetResourceHandler
    from app.db.enforcement_audit import EnforcementAuditModel
    import uuid

    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST, 
            client_id=settings.DATABRICKS_CLIENT_ID, 
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        workspace_client = provider.client
    except Exception as e:
        logger.error(f"Failed to init databricks client: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize Databricks client")

    handlers = {
        "app": AppResourceHandler(workspace_client),
        "cluster": ClusterResourceHandler(workspace_client),
        "job": JobResourceHandler(workspace_client),
        "sql_warehouse": SqlWarehouseResourceHandler(workspace_client),
        "dashboard": DashboardResourceHandler(workspace_client),
        "genie_space": GenieSpaceResourceHandler(workspace_client),
        "service_principal": ServicePrincipalResourceHandler(workspace_client),
        "notebook": NotebookResourceHandler(workspace_client),
        "storage": VolumeResourceHandler(workspace_client),
        "table": DatasetResourceHandler(workspace_client),
    }

    handler = handlers.get(body.resource_type)
    if not handler:
        raise HTTPException(status_code=400, detail=f"No handler for resource type: {body.resource_type}")

    action_to_take = body.action.upper()
    executed_action = f"manual_{action_to_take.lower()}"

    try:
        if action_to_take == "KILL":
            await handler.kill(body.resource_id)
        elif action_to_take == "CERTIFY":
            if hasattr(handler, "certify"):
                await handler.certify(body.resource_id)
            else:
                raise HTTPException(status_code=400, detail="Handler does not support certify")
        elif action_to_take == "UNCERTIFY":
            if hasattr(handler, "uncertify"):
                await handler.uncertify(body.resource_id)
            else:
                raise HTTPException(status_code=400, detail="Handler does not support uncertify")
        elif action_to_take == "WARN":
            await handler.warn(body.resource_id, body.reason or "Manual warning")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported manual action: {action_to_take}")
            
        # Log it to the audit table
        audit_record = EnforcementAuditModel(
            id=str(uuid.uuid4()),
            request_id=request_id,
            resource_id=body.resource_id,
            resource_type=body.resource_type,
            policy_name=body.policy_name,
            severity="MANUAL",
            intended_action=action_to_take,
            executed_action=executed_action,
            reason=f"Manually executed by {current_user.email}. Original reason: {body.reason}"
        )
        db.add(audit_record)
        db.commit()
        
    except Exception as e:
        logger.error(f"Failed to execute manual action {action_to_take} on {body.resource_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to execute action: {str(e)}")

    return {"status": "success", "message": f"Successfully executed {action_to_take} on {body.resource_id}"}

