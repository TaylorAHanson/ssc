"""
State machine transition tasks.
"""
from arq import cron
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.state_machines.lock import acquire_lock, release_lock
from app.db.session import get_lakebase_session
from app.db.request import RequestModel, FailureModel
from app.core.exceptions import RetryableError, PermanentError
import traceback
from datetime import datetime
from typing import Dict, Any


async def process_state_transition(ctx, request_id: str, transition_name: str) -> Dict[str, Any]:
    """
    Process state machine transition asynchronously with retry logic.
    
    Args:
        ctx: ARQ context
        request_id: Request ID
        transition_name: Name of transition to execute
        
    Returns:
        Dictionary with status and result
    """
    db = get_lakebase_session()
    worker_id = f"arq@{ctx.job_id}"
    
    try:
        # Load state from database
        request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
        if not request:
            raise PermanentError(f"Request {request_id} not found")
        
        # Check if max retries exceeded
        if request.retry_count >= request.max_retries:
            # Move to failed state
            request.status = 'failed'
            request.current_state = 'failed'
            save_state_machine(db, request, None)
            db.commit()
            
            # Notify user of permanent failure
            await notify_failure(ctx, request_id, "max_retries_exceeded")
            return {"status": "failed", "reason": "max_retries_exceeded"}
        
        # Acquire lock
        if not acquire_lock(db, request_id, worker_id=worker_id):
            # Another worker is processing, skip
            return {"status": "skipped", "reason": "locked"}
        
        try:
            # Load state machine from persisted state
            state_machine = load_state_machine(request)
            
            # Execute transition
            getattr(state_machine, transition_name)()
            
            # Save state back to database
            save_state_machine(db, request, state_machine)
            
            # Reset retry count on success
            request.retry_count = 0
            request.last_error = None
            db.commit()
            
            # If transition triggers next async operation, enqueue it
            if state_machine.current_state.id == "provisioning":
                await provision_workspace(ctx, request_id, request.state_context or {})
            
            return {"status": "completed", "state": state_machine.current_state.id}
            
        except RetryableError as e:
            # Retryable error - increment retry count and retry
            request.retry_count += 1
            request.last_failure = datetime.utcnow()
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "retry_count": request.retry_count
            }
            
            # Log failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx.job_id,
                failure_type="retryable_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow()
            )
            db.add(failure)
            db.commit()
            
            # Retry with exponential backoff
            raise  # ARQ will handle retry
            
        except PermanentError as e:
            # Permanent error - move to failed state
            request.status = 'failed'
            request.current_state = 'failed'
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "permanent": True
            }
            
            # Log permanent failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx.job_id,
                failure_type="permanent_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow(),
                resolved=False
            )
            db.add(failure)
            save_state_machine(db, request, None)
            db.commit()
            
            # Notify user of permanent failure
            await notify_failure(ctx, request_id, "permanent_error", str(e))
            
            raise  # Don't retry permanent errors
            
        except Exception as e:
            # Unexpected error - treat as retryable
            request.retry_count += 1
            request.last_failure = datetime.utcnow()
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "retry_count": request.retry_count,
                "unexpected": True
            }
            
            # Log failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx.job_id,
                failure_type="unexpected_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow()
            )
            db.add(failure)
            db.commit()
            
            # Retry with exponential backoff
            raise
            
        finally:
            # Release lock
            release_lock(db, request_id)
            
    except Exception as e:
        # Final catch-all - log and notify
        await notify_failure(ctx, request_id, "worker_error", str(e))
        raise
    finally:
        db.close()


async def provision_workspace(ctx, request_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Provision workspace with progress tracking and failure handling."""
    # TODO: Implement workspace provisioning
    return {"status": "not_implemented"}


async def notify_failure(ctx, request_id: str, failure_type: str, error_message: str = None) -> None:
    """Notify user and admins of failure."""
    # TODO: Implement failure notification
    pass


# Note: WorkerSettings is defined in arq_app.py to avoid circular imports

