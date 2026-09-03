"""
Request API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request as FastAPIRequest
import fastapi
from fastapi.responses import JSONResponse, Response
import orjson
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.request import Request, RequestCreate, RequestUpdate, StateMachineState, RequestStatus, RequestType
from app.services.request_service import RequestService
from app.db.session import get_db
from app.db import ApprovalModel, RequestModel, EventModel
from app.state_machines.facts import add_fact
from app.workflows.render import render_state, _resolve_spec_dict
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
import json
import logging

from app.api.deps import get_current_user
from app.models.user import User
from app.services.state_summary import summarize_state_context

logger = logging.getLogger(__name__)

router = APIRouter()


def _orjson_response(data: Any) -> Response:
    """A JSON response encoded with orjson inside the (sync, threadpool) handler.

    Serializing a large payload (e.g. a Sentinel run's full findings) this way
    keeps the encode off the event loop and releases the GIL. Returning a bare
    Pydantic model instead would make FastAPI re-serialize on the loop — the very
    stall we're avoiding. This replaces the now-deprecated ``ORJSONResponse``
    class while preserving that off-loop behavior.
    """
    return Response(content=orjson.dumps(data), media_type="application/json")


def _summary_metadata(req: RequestModel) -> Dict[str, Any]:
    """The compact list-view metadata for a request.

    Prefers the persisted ``state_summary`` column (so the huge ``state_context``
    is never loaded for the list). Falls back to summarizing ``state_context`` for
    rows written before the column existed / were backfilled — rare, and the
    startup backfill covers existing Sentinel runs.
    """
    if req.state_summary is not None:
        return req.state_summary
    return summarize_state_context(req.state_context)


def _format_request(
    req: RequestModel, db: Session, facts=None, spec_dict=None, summary: bool = False
) -> Optional[Request]:
    """Format a RequestModel into a Request Pydantic model.

    The request ``type`` is a free, data-driven string (it names a workflow in
    the registry), so we never coerce it through an enum — unknown/custom types
    are rendered like any other instead of being silently dropped.

    ``facts``/``spec_dict`` are optional pre-fetched inputs supplied by the
    list endpoints (see :func:`_format_requests_bulk`) to avoid per-request
    fact + spec queries.

    ``summary`` trims the response for list views: ``metadata`` comes from the
    compact ``state_summary`` column (see :func:`_summary_metadata`) instead of
    the huge ``state_context``, and the ``conversation`` transcript is omitted.
    Row-level counts are preserved; the full record is available via
    ``GET /requests/{id}``.
    """
    r_type = req.type

    try:
        sm_state = render_state(req, db, facts=facts, spec_dict=spec_dict)
    except Exception as e:
        logger.error(f"ERROR rendering V2 state for {req.id}: {e}", exc_info=True)
        sm_state = StateMachineState(
            currentState=req.current_state or "unknown",
            states=[],
            currentProgress=None
        )

    try:
        request_status = RequestStatus(req.status)
    except ValueError:
        request_status = RequestStatus.PENDING
        
    approvals = []
    if hasattr(req, "approvals") and req.approvals:
        from app.models.request import Approval
        for app in req.approvals:
            approvals.append(Approval(
                id=app.id,
                requestId=app.request_id,
                requestTitle=req.title,
                requestType=r_type,
                approvalType=app.approval_type,
                requestedBy=app.requested_by or "",
                requestedByEmail=app.requested_by_email or "",
                assignedToEmail=app.assigned_to_email,
                assignedToRole=app.assigned_to_role,
                approvedBy=app.approved_by,
                approvedAt=app.approved_at,
                rejectedBy=app.rejected_by,
                rejectedAt=app.rejected_at,
                status=app.status,
                createdAt=app.created_at,
                updatedAt=app.updated_at,
                rejectionNote=app.rejection_note,
                delegatedTo=app.delegated_to,
                delegatedToEmail=app.delegated_to_email,
                supersededNote=app.superseded_note
            ))

    return Request(
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
        metadata=_summary_metadata(req) if summary else (req.state_context or {}),
        conversation=None if summary else req.conversation,
        approvals=approvals
    )

def _format_requests_bulk(
    requests: List[RequestModel], db: Session, summary: bool = False
) -> List[Request]:
    """Format a page of requests with O(1) fact + spec queries (no N+1).

    A naive loop over ``_format_request`` issues, per request, a fact query, a
    published-graph-spec query, and a lazy approvals load — i.e. ~3N+1 queries
    for a page of N. Here we instead:
      * batch every page request's facts in a single ``IN (...)`` query,
      * resolve the graph spec once per distinct request *type* (it's keyed only
        on type), and
      * rely on the caller eager-loading ``approvals`` via ``selectinload``.
    """
    if not requests:
        return []

    req_ids = [r.id for r in requests]
    facts_by_req: dict = {}
    all_facts = (
        db.query(EventModel)
        .filter(EventModel.request_id.in_(req_ids))
        .order_by(EventModel.created_at.asc())
        .all()
    )
    for f in all_facts:
        facts_by_req.setdefault(f.request_id, []).append(f)

    spec_cache: dict = {}
    out: List[Request] = []
    for req in requests:
        if req.type not in spec_cache:
            spec_cache[req.type] = _resolve_spec_dict(req, db)
        formatted = _format_request(
            req, db, facts=facts_by_req.get(req.id, []), spec_dict=spec_cache[req.type],
            summary=summary,
        )
        if formatted:
            out.append(formatted)
    return out


@router.get("", response_model=List[Request])
def get_requests(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all requests.

    Defined as a *sync* handler so Starlette runs it in the worker threadpool:
    formatting + JSON encoding a large page (e.g. a Sentinel run with tens of
    thousands of violations) is CPU-bound and would otherwise block the single
    event loop, starving every other in-flight request. We also serialize with
    orjson here (fast, releases the GIL) and return the ready Response so FastAPI
    doesn't re-encode it back on the loop.
    """
    # Build query; eager-load approvals to avoid a per-request lazy load.
    query = db.query(RequestModel).options(selectinload(RequestModel.approvals))
    
    # Non-admins can only see their own requests
    if not current_user.has_role("platform_admin"):
        query = query.filter(RequestModel.requester_email == current_user.email)
    
    requests = query.offset(skip).limit(limit).all()
    formatted = _format_requests_bulk(requests, db)
    return _orjson_response([r.model_dump(mode="json") for r in formatted])


from pydantic import BaseModel as _PydanticBase

class PaginatedRequestsResponse(_PydanticBase):
    items: List[Request]
    total: int

@router.get("/paginated", response_model=PaginatedRequestsResponse)
def get_paginated_requests(
    skip: int = 0,
    limit: int = 10,
    type: Optional[str] = None,
    search: Optional[str] = None,
    summary: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get paginated requests with optional filtering and search.

    ``summary=true`` returns list-optimized rows: heavy ``metadata`` arrays (e.g.
    a Sentinel run's full violation records) and the conversation transcript are
    omitted while aggregate counts are preserved — much smaller payloads for a
    growing table. Open a row to fetch its full record via ``GET /requests/{id}``.
    """
    from sqlalchemy import or_
    from sqlalchemy.orm import defer

    query = db.query(RequestModel).options(selectinload(RequestModel.approvals))
    # In summary mode never fetch the (potentially hundreds-of-MB) state_context
    # or conversation columns — the list reads the compact state_summary instead.
    # This is what makes the list fast regardless of run size.
    if summary:
        query = query.options(defer(RequestModel.state_context), defer(RequestModel.conversation))

    if not current_user.has_role("platform_admin"):
        query = query.filter(RequestModel.requester_email == current_user.email)
        
    if type:
        query = query.filter(RequestModel.type == type)
        
    if search:
        search_term = f"%{search}%"
        # Search title, status, environment, ID, and the metadata JSON. In summary
        # mode search the compact state_summary column, NOT state_context: casting
        # the big blob to text would detoast every row (defeating the whole point).
        from sqlalchemy import cast, String

        meta_col = RequestModel.state_summary if summary else RequestModel.state_context
        query = query.filter(
            or_(
                RequestModel.title.ilike(search_term),
                RequestModel.status.ilike(search_term),
                RequestModel.environment.ilike(search_term),
                RequestModel.id.ilike(search_term),
                cast(meta_col, String).ilike(search_term)
            )
        )
        
    # Get total count before pagination
    total = query.count()
    
    # Order by newest first
    requests = query.order_by(RequestModel.created_at.desc()).offset(skip).limit(limit).all()
    response_list = _format_requests_bulk(requests, db, summary=summary)
    payload = PaginatedRequestsResponse(items=response_list, total=total)
    # Sync handler + orjson => formatting/encoding happen in the threadpool, off
    # the event loop (see get_requests). Return the ready Response so FastAPI
    # doesn't re-serialize on the loop.
    return _orjson_response(payload.model_dump(mode="json"))


@router.get("/{request_id}", response_model=Request)
def get_request(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific request by ID.

    Sync handler on purpose: a single request can carry a very large
    ``state_context`` (e.g. a Sentinel run with tens of thousands of violation
    records). Formatting + JSON-encoding that on the event loop would freeze the
    whole app (other tabs, the context catalog, etc.) until it finished. Running
    in the threadpool and encoding with orjson (which releases the GIL) keeps the
    app responsive while the big payload is built.
    """
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
        
    formatted = _format_request(request_model, db)
    if not formatted:
        raise HTTPException(status_code=500, detail="Failed to format request")

    # Sentinel runs keep only counts/summary in state_context; the full per-record
    # detail (all violations + checks) lives in the joined ``sentinel_findings``
    # table. Rehydrate it here so the detail view has the complete set to search /
    # scroll. This is the on-demand detail path (already off the event loop +
    # orjson), so building the big payload here doesn't stall the app.
    if request_model.type == "enforcement_sentinel":
        from app.services.sentinel_findings import (
            has_findings, load_run_checks, load_run_violations,
        )

        if has_findings(db, request_id):
            meta = dict(formatted.metadata or {})
            meta["violations"] = load_run_violations(db, request_model)
            meta["checks"] = load_run_checks(db, request_model)
            formatted.metadata = meta

    return _orjson_response(formatted.model_dump(mode="json"))


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


@router.get("/{request_id}/graph")
async def get_request_graph(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The authored workflow graph for this request plus live per-node status.

    Powers the request-detail visual runner: the same graph_spec the no-code
    editor draws, annotated with which nodes are done / current / pending /
    rejected (derived from the fact log + status).
    """
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    if not current_user.has_role("platform_admin") and request.requester_email != current_user.email:
        raise HTTPException(status_code=403, detail="Not authorized to view this request")

    from app.workflows.render import live_graph
    return live_graph(request, db)


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_request(
    request_data: RequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new request."""
    # Admin-only workflow types must be gated server-side, not just by hiding
    # the UI. The enforcement sentinel scans/remediates governed assets, so a
    # crafted API call shouldn't be able to start one without the right role.
    if request_data.type == RequestType.ENFORCEMENT_SENTINEL.value and not (
        current_user.has_role("Platform Admin") or current_user.has_role("Governance Admin")
    ):
        raise HTTPException(status_code=403, detail="Not authorized to start enforcement sentinel runs")

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


def _authorize_approval_actor(approval: ApprovalModel, current_user: User) -> None:
    """Enforce that the caller is the assigned approver (or a platform admin).

    Manager-approval tasks are addressed to the requester's manager (email
    captured by the agent and stored in approval.assigned_to_email). Data-owner
    approval tasks are addressed to the asset owner returned by Unity Catalog
    (stored in assigned_to_email or, when only a group/role is known,
    assigned_to_role). Anyone else attempting to act on the approval is
    rejected here so the state machine can't be advanced by an unrelated user.
    Platform admins retain an override for break-glass scenarios.
    """
    if current_user.has_role("platform_admin"):
        return

    actor_email = (current_user.email or "").lower()
    assignee_email = (approval.assigned_to_email or "").lower()
    assignee_role = approval.assigned_to_role

    if assignee_email and actor_email == assignee_email:
        return
    if assignee_role and current_user.has_role(assignee_role):
        return

    if not assignee_email and not assignee_role:
        # Defensive: an approval with no assignee shouldn't be actionable by
        # arbitrary authenticated users. Force a platform-admin override.
        raise HTTPException(
            status_code=403,
            detail=(
                f"This {approval.approval_type} approval has no assignee on file; "
                "only a platform admin can act on it."
            ),
        )

    raise HTTPException(
        status_code=403,
        detail=(
            f"Only the assigned {approval.approval_type} approver "
            f"({approval.assigned_to_email or approval.assigned_to_role}) "
            "can act on this request."
        ),
    )


@router.post("/{request_id}/approve", status_code=status.HTTP_200_OK)
async def approve_request(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Approve a request using fact-based approach.

    Records a fact (approval_received) - this is the source of truth.
    The state machine will reconcile state based on facts.

    Authorization: only the assignee of the current pending approval (e.g. the
    manager for manager_approval, the data owner for data_owner_approval) may
    approve. Platform admins may override.
    """
    # Find all pending approvals for this request
    pending_approvals = (
        db.query(ApprovalModel)
        .filter(
            ApprovalModel.request_id == request_id,
            ApprovalModel.status == "pending",
        )
        .order_by(ApprovalModel.created_at.asc())
        .all()
    )

    if not pending_approvals:
        raise HTTPException(status_code=404, detail="No pending approval found for this request")

    # Find the first pending approval the user is authorized to act on
    approval = None
    for pending in pending_approvals:
        try:
            _authorize_approval_actor(pending, current_user)
            approval = pending
            break
        except HTTPException:
            continue

    if not approval:
        raise HTTPException(status_code=403, detail="You are not authorized to approve this request")

    approved_by = current_user.email

    # Update approval status immediately
    approval.status = "approved"
    approval.approved_by = approved_by
    approval.approved_at = datetime.now(timezone.utc)

    # Record fact (this is the source of truth)
    add_fact(
        db, request_id, "approval_received",
        {
            "approval_type": approval.approval_type,
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc).isoformat()
        },
        actor=approved_by
    )

    db.commit()

    return {"status": "approved", "message": "Approval recorded. State will be updated by poller."}


@router.post("/{request_id}/reject", status_code=status.HTTP_200_OK)
async def reject_request(
    request_id: str,
    rejection_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reject a request using fact-based approach.
    
    Records a fact (request_rejected) - this is the source of truth.
    The state machine will reconcile state based on facts.
    """
    # Find all pending approvals for this request
    pending_approvals = (
        db.query(ApprovalModel)
        .filter(
            ApprovalModel.request_id == request_id,
            ApprovalModel.status == "pending",
        )
        .order_by(ApprovalModel.created_at.asc())
        .all()
    )

    if not pending_approvals:
        raise HTTPException(status_code=404, detail="No pending approval found for this request")

    # Find the first pending approval the user is authorized to act on
    approval = None
    for pending in pending_approvals:
        try:
            _authorize_approval_actor(pending, current_user)
            approval = pending
            break
        except HTTPException:
            continue

    if not approval:
        raise HTTPException(status_code=403, detail="You are not authorized to reject this request")

    rejected_by = current_user.email
    rejection_note = rejection_data.get("rejection_note")
    
    # Update approval status immediately
    approval.status = "rejected"
    approval.rejected_by = rejected_by
    approval.rejection_note = rejection_note
    approval.rejected_at = datetime.now(timezone.utc)
    
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a request."""
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Only admins or owner can delete
    if not current_user.has_role("platform_admin") and request.requester_email != current_user.email:
        raise HTTPException(status_code=403, detail="Not authorized to delete this request")

    # Explicitly clear the run's joined findings. The DB-level FK cascades on
    # Postgres, but SQLite (local dev) doesn't enforce it by default, so do it
    # here to avoid orphaned rows regardless of dialect.
    from app.db.sentinel_finding import SentinelFindingModel

    db.query(SentinelFindingModel).filter(
        SentinelFindingModel.request_id == request_id
    ).delete(synchronize_session=False)
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
    current_user: User = Depends(get_current_user),
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
    from app.workflows.graphs import editable_states as v2_editable_states
    from app.workflows.render import render_state

    if not current_user.has_role("platform_admin"):
        raise HTTPException(status_code=403, detail="Only platform admins can edit workflow parameters")

    request_model = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request_model:
        raise HTTPException(status_code=404, detail="Request not found")

    allowed = v2_editable_states(request_model.type)
    current_state = render_state(request_model, db).currentState
    if current_state not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot edit parameters in state '{current_state}'. "
                f"Edit is only allowed in: {allowed}"
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
    request_model.updated_at = datetime.now(timezone.utc)

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
    # Host of the workspace the resource lives in (multi-workspace runs). When
    # set, the action is executed against that workspace; otherwise the app's
    # home workspace is used.
    workspace_host: Optional[str] = None


@router.post("/{request_id}/enforcement-action", status_code=status.HTTP_200_OK)
async def execute_enforcement_action(
    request_id: str,
    body: EnforcementActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Execute a specific enforcement action manually (e.g. from audit mode)."""
    if not current_user.has_role("Platform Admin") and not current_user.has_role("Governance Admin"):
        raise HTTPException(status_code=403, detail="Not authorized to execute enforcement actions")

    from app.providers.databricks.handlers import (
        AppResourceHandler, ClusterResourceHandler, JobResourceHandler,
        SqlWarehouseResourceHandler, DashboardResourceHandler,
        GenieSpaceResourceHandler, ServicePrincipalResourceHandler,
        NotebookResourceHandler, VolumeResourceHandler
    )
    from app.providers.databricks.handlers.dataset_handler import DatasetResourceHandler
    from app.db.enforcement_audit import EnforcementAuditModel
    from app.workflows.sentinel import _new_workspace_client, revalidate_violation
    import uuid

    host = (body.workspace_host or "").strip() or None
    try:
        # Resolve the client for the resource's workspace (multi-workspace runs);
        # falls back to the app's home workspace when no host is provided.
        workspace_client = _new_workspace_client(host)
    except Exception as e:
        logger.error(f"Failed to init databricks client: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize Databricks client")

    # Resolve the workspace NAME (for the audit row's dedup key) from the host.
    workspace_name: Optional[str] = None
    if host:
        try:
            from app.core.workspaces import get_workspace_config

            cfg = get_workspace_config(host)
            workspace_name = cfg.name if cfg else None
        except Exception:  # noqa: BLE001 - audit tagging is best-effort
            workspace_name = None

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
        "data_product": DatasetResourceHandler(workspace_client),
    }

    handler = handlers.get(body.resource_type)
    if not handler:
        raise HTTPException(status_code=400, detail=f"No handler for resource type: {body.resource_type}")

    action_to_take = body.action.upper()
    executed_action = f"manual_{action_to_take.lower()}"

    if body.resource_type == "app":
        from app.providers.databricks.handlers.app_handler import is_protected_app

        if action_to_take in ("KILL", "STOP_AND_REVOKE") and is_protected_app(body.resource_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot execute destructive action on protected app '{body.resource_id}'. This app is protected by platform safety policy.",
            )

    # Before a DESTRUCTIVE action, re-validate that the violation still exists.
    # A user may have already remediated it since the scan; we must not punish
    # them for a stale finding. Safe/reversible actions (certify/uncertify/warn)
    # are not gated. We only proceed on a POSITIVE confirmation that it still
    # violates — "fixed", "gone", or "couldn't determine" all abort.
    if action_to_take in ("KILL", "STOP_AND_REVOKE"):
        recheck = await revalidate_violation(
            workspace_client=workspace_client,
            host=host,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            policy_name=body.policy_name,
        )
        if recheck.get("still_violates") is False:
            logger.info(
                "Aborting manual %s on %s (%s): re-validation shows the violation "
                "is no longer present (%s).",
                action_to_take, body.resource_id, body.policy_name, recheck.get("reason"),
            )
            # Record an audit row so the abort is visible in history.
            try:
                db.add(EnforcementAuditModel(
                    id=str(uuid.uuid4()),
                    request_id=request_id,
                    resource_id=body.resource_id,
                    resource_type=body.resource_type,
                    workspace=workspace_name,
                    policy_name=body.policy_name,
                    severity="MANUAL",
                    intended_action=action_to_take,
                    executed_action="aborted_revalidated",
                    reason=(
                        f"{action_to_take} aborted by {current_user.email}: violation no longer present "
                        f"on re-validation ({recheck.get('detail') or recheck.get('reason')})."
                    ),
                ))
                db.commit()
            except Exception as audit_err:  # noqa: BLE001
                logger.warning(f"Could not record re-validation abort audit: {audit_err}")
                db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This violation is no longer present — it appears to have already been "
                    "remediated, so the action was not executed. Re-run the scan to refresh."
                ),
            )
        if recheck.get("still_violates") is None:
            # Couldn't confirm (no handler / discovery or evaluation error). Do NOT
            # kill on uncertainty — surface the reason and let the admin retry.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Could not re-validate that this violation still exists, so the destructive "
                    f"action was aborted for safety. {recheck.get('detail') or ''} Please retry."
                ).strip(),
            )

    audit_reason = f"Manually executed by {current_user.email}. Original reason: {body.reason}"
    try:
        if action_to_take in ("KILL", "STOP_AND_REVOKE"):
            if body.resource_type == "app" and hasattr(handler, "stop_and_revoke"):
                stop_res = await handler.stop_and_revoke(body.resource_id)
                if stop_res.get("status") == "skipped_protected":
                    raise HTTPException(status_code=400, detail=stop_res.get("message", "App is protected."))
                import json

                prev_acl = stop_res.get("previous_acl") or []
                executed_action = "manual_stop_and_revoke"
                audit_reason = (
                    f"Manually executed stop_and_revoke by {current_user.email}. "
                    f"Previous ACL snapshot: {json.dumps(prev_acl)}. Original reason: {body.reason}"
                )
            else:
                await handler.kill(body.resource_id)
        elif action_to_take == "REINSTATE":
            if hasattr(handler, "reinstate_permissions"):
                import json

                prior_audit = (
                    db.query(EnforcementAuditModel)
                    .filter(
                        EnforcementAuditModel.resource_id == body.resource_id,
                        EnforcementAuditModel.resource_type == body.resource_type,
                        EnforcementAuditModel.executed_action.in_(("automated_stop_and_revoke", "manual_stop_and_revoke")),
                    )
                    .order_by(EnforcementAuditModel.created_at.desc())
                    .first()
                )
                prev_acl = None
                if prior_audit and "Previous ACL snapshot:" in (prior_audit.reason or ""):
                    try:
                        raw = prior_audit.reason.split("Previous ACL snapshot:", 1)[1].strip()
                        start_idx = raw.find("[")
                        end_idx = raw.rfind("]")
                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            prev_acl = json.loads(raw[start_idx : end_idx + 1])
                        else:
                            prev_acl = json.loads(raw)
                    except Exception as parse_err:
                        logger.warning("Could not parse previous ACL snapshot: %s", parse_err)
                success = await handler.reinstate_permissions(body.resource_id, original_acl=prev_acl)
                if not success:
                    raise HTTPException(status_code=500, detail=f"Failed to reinstate permissions on app {body.resource_id}")
                executed_action = "manual_reinstate"
                audit_reason = f"Manually reinstated permissions by {current_user.email}. Reinstated from prior snapshot: {bool(prev_acl)}."
            else:
                raise HTTPException(status_code=400, detail=f"Handler does not support reinstate for {body.resource_type}")
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
            workspace=workspace_name,
            policy_name=body.policy_name,
            severity="MANUAL",
            intended_action=action_to_take,
            executed_action=executed_action,
            reason=audit_reason,
        )
        db.add(audit_record)
        db.commit()
        
    except Exception as e:
        logger.error(f"Failed to execute manual action {action_to_take} on {body.resource_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to execute action: {str(e)}")

    # Reflect the new certification state in the local cache the certification
    # UI reads (DataAsset.certified), so the page updates immediately instead of
    # waiting for (or never getting) the next scheduled sync. We re-read the live
    # UC tag rather than assuming success, so the cache mirrors reality even on
    # partial failures. Best-effort: a refresh hiccup must not fail the action
    # the user already executed against Databricks.
    if (
        body.resource_type in ("data_product", "table")
        and action_to_take in ("CERTIFY", "UNCERTIFY", "KILL")
        and hasattr(handler, "get_certification_status")
    ):
        try:
            from app.db.data_asset import DataAssetModel

            asset = db.query(DataAssetModel).filter(
                DataAssetModel.id == body.resource_id
            ).first()
            if asset:
                asset.certified = await handler.get_certification_status(body.resource_id)
                # Intentionally do NOT touch last_synced_at here. That column
                # backs the certification UI's "Last Policy Run" column, which
                # must mean exactly that — the last Enforcement Sentinel policy
                # evaluation — not a manual certify/uncertify action.
                db.add(asset)
                db.commit()
            else:
                logger.info(
                    f"No cached DataAsset for {body.resource_id}; certification "
                    f"status will refresh on the next data asset sync."
                )
        except Exception as e:
            logger.warning(
                f"Executed {action_to_take} on {body.resource_id} but could not "
                f"refresh cached certification status: {e}"
            )

    return {"status": "success", "message": f"Successfully executed {action_to_take} on {body.resource_id}"}


@router.get("/{request_id}/enforcement-actions", status_code=status.HTTP_200_OK)
async def list_enforcement_actions(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return manual enforcement actions recorded for a run.

    Lets the Sentinel UI durably rehydrate the "Executed" state after a page
    refresh instead of relying on ephemeral client state.
    """
    if not current_user.has_role("Platform Admin") and not current_user.has_role("Governance Admin"):
        raise HTTPException(status_code=403, detail="Not authorized to view enforcement actions")

    from app.db.enforcement_audit import EnforcementAuditModel
    from sqlalchemy import or_

    records = (
        db.query(EnforcementAuditModel)
        .filter(
            EnforcementAuditModel.request_id == request_id,
            or_(
                EnforcementAuditModel.executed_action.like("manual_%"),
                EnforcementAuditModel.executed_action.in_(("automated_stop_and_revoke", "skipped_protected")),
            ),
        )
        .order_by(EnforcementAuditModel.created_at.asc())
        .all()
    )
    return [
        {
            "resource_id": r.resource_id,
            "resource_type": r.resource_type,
            "workspace": r.workspace,
            "policy_name": r.policy_name,
            "action": r.intended_action,
            "executed_action": r.executed_action,
            "reason": r.reason,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

