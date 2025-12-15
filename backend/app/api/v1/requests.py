"""
Request API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.request import Request, RequestCreate, RequestUpdate
from app.services.request_service import RequestService
from app.db.session import get_db

# ARQ/Redis imports - optional, only needed for async task processing
try:
    from app.workers.arq_app import get_redis_settings
    from arq import create_pool
    from arq.connections import ArqRedis
    ARQ_AVAILABLE = True
except ImportError:
    ARQ_AVAILABLE = False
    # Stub functions for when ARQ is not available
    def get_redis_settings():
        raise NotImplementedError("ARQ/Redis not installed. Install 'arq' and 'redis' packages to enable async task processing.")

router = APIRouter()


@router.get("/", response_model=List[Request])
async def get_requests(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get all requests."""
    requests = RequestService.get_requests(db, skip=skip, limit=limit)
    return requests


@router.get("/{request_id}", response_model=Request)
async def get_request(
    request_id: str,
    db: Session = Depends(get_db)
):
    """Get a specific request by ID."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


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
    """Create a new request (async)."""
    # Create request in database
    request = RequestService.create_request(db, request_data)
    
    # Enqueue state transition task (if ARQ is available)
    if ARQ_AVAILABLE:
        redis = await create_pool(get_redis_settings())
        await redis.enqueue_job(
            "process_state_transition",
            request.id,
            "submit_for_approval"
        )
        await redis.close()
    else:
        # Without ARQ, just return the request (state transitions would need to be handled differently)
        pass
    
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "request_id": request.id,
            "status": "pending",
            "message": "Request created and queued for processing" if ARQ_AVAILABLE else "Request created (async processing not available)"
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
    """Approve request (callback from admin dashboard)."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Enqueue approval transition (if ARQ is available)
    if ARQ_AVAILABLE:
        redis = await create_pool(get_redis_settings())
        await redis.enqueue_job("process_state_transition", request_id, "approve")
        await redis.close()
        return {"status": "accepted", "message": "Approval processed"}
    else:
        raise HTTPException(status_code=501, detail="Async processing not available. ARQ/Redis not installed.")


@router.post("/{request_id}/reject", status_code=status.HTTP_202_ACCEPTED)
async def reject_request(
    request_id: str,
    rejection_note: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Reject request (callback from admin dashboard)."""
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Enqueue rejection transition (if ARQ is available)
    if ARQ_AVAILABLE:
        redis = await create_pool(get_redis_settings())
        await redis.enqueue_job("process_state_transition", request_id, "reject")
        await redis.close()
        return {"status": "accepted", "message": "Rejection processed"}
    else:
        raise HTTPException(status_code=501, detail="Async processing not available. ARQ/Redis not installed.")


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
    
    # Reset retry count and enqueue (if ARQ is available)
    if ARQ_AVAILABLE:
        request.retry_count = 0
        request.status = "pending"
        db.commit()
        
        redis = await create_pool(get_redis_settings())
        await redis.enqueue_job("process_state_transition", request_id, "submit_for_approval")
        await redis.close()
        
        return {"status": "accepted", "message": "Retry queued"}
    else:
        raise HTTPException(status_code=501, detail="Async processing not available. ARQ/Redis not installed.")

