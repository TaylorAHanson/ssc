"""
Polling worker for processing request state machine transitions.

This worker polls the database for pending requests and processes them
in parallel with proper locking, error handling, and retry logic.
"""
import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db.session import get_lakebase_session, get_engine, reset_database_connection
from app.db.base import Base
from app.db.request import RequestModel, FailureModel
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.state_machines.lock import acquire_lock, release_lock, heartbeat_lock
from app.models.request import RequestStatus, RequestType
from app.db.report_subscription import ReportSubscription
from croniter import croniter
import uuid
from app.core.config import settings
from app.core.exceptions import RetryableError, PermanentError
import traceback

logger = logging.getLogger(__name__)

# Generate unique worker ID
_worker_id = f"poll-worker-{socket.gethostname()}-{os.getpid()}"


async def start_poller():
    """Start the background poller."""
    logger.info(f"Starting background poller (worker_id: {_worker_id})...")
    
    # Ensure tables exist (for SQLite dev)
    try:
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified.")
    except Exception as e:
        logger.error(f"Failed to verify database tables: {e}")
    
    # Get polling interval from config (default 5 seconds)
    poll_interval = getattr(settings, 'POLLER_INTERVAL_SECONDS', 5)
    logger.info(f"Poller configured with interval: {poll_interval} seconds")
    
    poll_count = 0
    consecutive_db_errors = 0
    while True:
        poll_count += 1
        try:
            logger.debug(f"Poller cycle #{poll_count} - checking for requests...")
            await process_open_requests()
            
            # Check for scheduled reports (could be throttled if needed, but checking DB is cheap)
            await process_scheduled_reports()

            consecutive_db_errors = 0  # Reset on success
        except Exception as e:
            error_msg = str(e).lower()
            is_auth_error = (
                "failed to decode token" in error_msg or
                "authentication failed" in error_msg or
                "password authentication failed" in error_msg or
                "operationalerror" in error_msg
            )
            
            if is_auth_error:
                consecutive_db_errors += 1
                logger.error(
                    f"Database auth error in poller (cycle #{poll_count}, consecutive: {consecutive_db_errors}): {e}"
                )
                
                # After 3 consecutive auth errors, reset the connection pool
                if consecutive_db_errors >= 3:
                    logger.warning("🔄 Too many consecutive DB auth errors - resetting connection pool...")
                    reset_database_connection()
                    consecutive_db_errors = 0
            else:
                logger.error(f"Error in poller loop (cycle #{poll_count}): {e}", exc_info=True)
        
        await asyncio.sleep(poll_interval)


async def process_open_requests():
    """Find and process all open requests in parallel."""
    db = get_lakebase_session()
    try:
        # Find requests that need processing (not completed/rejected/failed)
        # and are not locked (or lock has expired)
        now = datetime.utcnow()
        requests = db.query(RequestModel).filter(
            RequestModel.status.notin_([
                RequestStatus.COMPLETED.value, 
                RequestStatus.REJECTED.value,
                "failed"
            ]),
            # Only process requests that are not locked or have expired locks
            or_(
                RequestModel.locked_by.is_(None),
                RequestModel.locked_until < now
            )
        ).limit(settings.POLLER_BATCH_SIZE).all()
        
        if not requests:
            logger.debug("No requests found to process")
            return  # No work to do
        
        logger.debug(f"Found {len(requests)} request(s) to process: {[f'{r.id} ({r.current_state})' for r in requests]}")
        
        # Process in parallel with concurrency limit
        semaphore = asyncio.Semaphore(settings.POLLER_MAX_CONCURRENT)
        
        tasks = [
            process_single_request(semaphore, request.id)
            for request in requests
        ]
        
        # Wait for all tasks to complete, allowing exceptions
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log any exceptions that occurred
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error processing request {requests[i].id}: {result}",
                    exc_info=result
                )
                
    finally:
        db.close()



async def process_scheduled_reports():
    """Check for and spawn scheduled reports."""
    db = get_lakebase_session()
    try:
        now = datetime.utcnow()
        due_subs = db.query(ReportSubscription).filter(
            ReportSubscription.is_active == True,
            ReportSubscription.next_run_at <= now
        ).all()
        
        if due_subs:
            logger.info(f"Found {len(due_subs)} due report subscription(s). Spawning requests...")
            
        for sub in due_subs:
            try:
                # 1. Spawn Request
                req_id = f"req-{uuid.uuid4()}"
                
                # Context with all needed info
                context = {
                    "subscription_id": sub.id,
                    "name": sub.name,
                    "subscribers": sub.subscribers,
                    "prompts": sub.prompts,
                    "schedule_cron": sub.schedule_cron
                }
                
                new_request = RequestModel(
                    id=req_id,
                    type=RequestType.REPORT_EXECUTION.value,
                    title=f"Report: {sub.name}",
                    status=RequestStatus.PENDING.value,
                    current_state="pending",
                    state_context=context,
                    created_at=now,
                    updated_at=now
                )
                db.add(new_request)
                
                # 2. Update Subscription
                sub.last_run_at = now
                
                # Calculate next run
                # Calculate next run in PST to respect user timezone
                pst_tz = ZoneInfo("America/Los_Angeles")
                
                # Convert current UTC time to PST
                now_utc = now.replace(tzinfo=ZoneInfo("UTC"))
                now_pst = now_utc.astimezone(pst_tz)
                
                # Get next scheduled time in PST
                iter = croniter(sub.schedule_cron, now_pst)
                next_pst = iter.get_next(datetime)
                
                # Convert back to UTC for storage (naive)
                sub.next_run_at = next_pst.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                
                db.commit()
                logger.info(f"Spawned report request {req_id} for subscription {sub.id}. Next run: {sub.next_run_at}")
                
            except Exception as e:
                logger.error(f"Failed to process subscription {sub.id}: {e}", exc_info=True)
                db.rollback() # Rollback validation of this single sub if failed
                
    except Exception as e:
        logger.error(f"Error in process_scheduled_reports: {e}", exc_info=True)
    finally:
        db.close()


async def process_single_request(semaphore: asyncio.Semaphore, request_id: str):
    """Process a single request with locking and error handling."""
    async with semaphore:  # Limit concurrent processing
        db = get_lakebase_session()
        heartbeat_task = None
        try:
            # Load request from database
            request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
            if not request:
                logger.warning(f"Request {request_id} not found")
                return
            
            # Check if max retries exceeded
            if request.retry_count >= request.max_retries:
                logger.warning(
                    f"Request {request_id} exceeded max retries ({request.max_retries}), "
                    "marking as failed"
                )
                request.status = RequestStatus.FAILED.value
                # Don't change current_state - keep the last valid state so state machine can still be loaded
                # The status="failed" indicates failure, not the state
                db.commit()
                return
            
            # Determine if this is a long-running operation that needs heartbeat
            is_long_running = request.status == RequestStatus.PROVISIONING.value
            lock_timeout = (
                settings.POLLER_LOCK_TIMEOUT_LONG_RUNNING_MINUTES
                if is_long_running
                else settings.POLLER_LOCK_TIMEOUT_MINUTES
            )
            
            if not acquire_lock(db, request_id, worker_id=_worker_id, timeout_minutes=lock_timeout):
                # Another worker is processing this request, skip it
                logger.debug(f"Request {request_id} is locked by another worker, skipping")
                return
            
            # Start heartbeat task for long-running operations
            if is_long_running:
                heartbeat_task = asyncio.create_task(
                    _heartbeat_lock_loop(request_id, lock_timeout)
                )
                logger.debug(f"Started heartbeat for long-running request {request_id}")
            
            try:
                # Process the request
                await _process_request_state_machine(db, request)
                
                # Reset retry count on success
                request.retry_count = 0
                request.last_error = None
                db.commit()
                
            except RetryableError as e:
                # Retryable error - increment retry count
                await _handle_retryable_error(db, request, e, _worker_id)
                # Don't raise - let it be retried on next poll cycle
                
            except PermanentError as e:
                # Permanent error - mark as failed
                await _handle_permanent_error(db, request, e, _worker_id)
                
            except Exception as e:
                # Unexpected error - treat as retryable
                logger.error(
                    f"Unexpected error processing request {request_id}: {e}",
                    exc_info=True
                )
                await _handle_retryable_error(
                    db, request, RetryableError(str(e)), _worker_id
                )
                
            finally:
                # Stop heartbeat if running
                if heartbeat_task:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    logger.debug(f"Stopped heartbeat for request {request_id}")
                
                # Always release lock
                release_lock(db, request_id)
                
        except Exception as e:
            logger.error(
                f"Critical error processing request {request_id}: {e}",
                exc_info=True
            )
        finally:
            db.close()


async def _heartbeat_lock_loop(request_id: str, timeout_minutes: int):
    """
    Background task to periodically extend lock expiration (heartbeat).
    Runs until cancelled.
    
    Args:
        request_id: Request ID to heartbeat
        timeout_minutes: Lock timeout to extend to
    """
    heartbeat_interval = getattr(settings, 'POLLER_HEARTBEAT_INTERVAL_SECONDS', 300)
    
    try:
        while True:
            await asyncio.sleep(heartbeat_interval)
            
            # Extend the lock
            db = get_lakebase_session()
            try:
                success = heartbeat_lock(
                    db, request_id, _worker_id, timeout_minutes
                )
                if success:
                    logger.debug(f"Heartbeat successful for request {request_id}")
                else:
                    logger.warning(
                        f"Heartbeat failed for request {request_id} - lock may have been released"
                    )
                    # If heartbeat fails, we've lost the lock - stop heartbeating
                    break
            except Exception as e:
                logger.error(
                    f"Error during heartbeat for request {request_id}: {e}",
                    exc_info=True
                )
            finally:
                db.close()
                
    except asyncio.CancelledError:
        logger.debug(f"Heartbeat cancelled for request {request_id}")
        raise


async def _process_request_state_machine(db, request: RequestModel):
    """
    Process state machine - delegate all logic to the state machine.
    
    The poller is completely ignorant of business logic.
    It just loads the state machine and calls tick().
    Also handles async tool execution for provisioning states.
    """
    from app.state_machines.facts import has_fact, get_latest_fact
    
    logger.debug(f"[{request.id}] Processing request - Current state: {request.current_state}, Status: {request.status}")
    
    # Load the polymorphic state machine
    sm = load_state_machine(request, db)
    initial_state = sm.current_state.id
    logger.debug(f"[{request.id}] Loaded state machine - Current state: {initial_state}")
    
    # Log relevant facts for debugging
    from app.db.request import EventModel
    facts = db.query(EventModel).filter(
        EventModel.request_id == request.id,
        EventModel.event_type.in_(["request_submitted", "approval_received", "training_completed", "workspace_created", "provisioning_started", "request_rejected"])
    ).all()
    if facts:
        fact_summary = {f.event_type: getattr(f, 'event_data', {}) for f in facts}
        logger.debug(f"[{request.id}] Relevant facts: {fact_summary}")
    
    # Let the state machine handle all logic
    logger.debug(f"[{request.id}] Calling state machine tick()...")
    changed = sm.tick()
    new_state = sm.current_state.id
    
    # Save state machine to sync status and state
    # We do this every time to ensure the database matches the state machine's internal view
    # even if no state transition occurred (e.g. manual status resets)
    if changed or request.status != sm.get_mapped_status().value:
        if request.status != "failed":
            logger.info(f"[{request.id}] Syncing state machine status: {request.status} -> {sm.get_mapped_status().value}")
            save_state_machine(db, request, sm)
            db.commit()
            logger.info(f"[{request.id}] Saved state machine - Current state: {sm.current_state.id}, Status: {request.status}")
    else:
        logger.debug(f"[{request.id}] No state change or status sync needed (still in {initial_state})")
    
    # Execute any tasks associated with the current state
    # These tasks (like provisioning) can be long-running
    await sm.execute_tasks()
    
    # If tasks added facts that might change state further, the next poll cycle will handle it
    # We don't save again here to avoid redundant commits, tick() is what matters


async def _handle_retryable_error(
    db: Session, 
    request: RequestModel, 
    error: RetryableError, 
    worker_id: str
):
    """Handle a retryable error by incrementing retry count and logging."""
    request.retry_count += 1
    request.last_failure = datetime.utcnow()
    request.last_error = {
        "error": str(error),
        "traceback": traceback.format_exc(),
        "retry_count": request.retry_count,
        "worker_id": worker_id
    }
    
    # Log failure to failures table
    failure = FailureModel(
        id=f"fail-{datetime.utcnow().timestamp()}",
        request_id=request.id,
        task_id=worker_id,
        failure_type="retryable_error",
        error_message=str(error),
        error_details=request.last_error,
        retry_count=request.retry_count,
        occurred_at=datetime.utcnow()
    )
    db.add(failure)
    db.commit()
    
    logger.warning(
        f"Retryable error for request {request.id} (attempt {request.retry_count}/{request.max_retries}): {error}"
    )


async def _handle_permanent_error(
    db: Session, 
    request: RequestModel, 
    error: PermanentError, 
    worker_id: str
):
    """Handle a permanent error by marking request as failed."""
    request.status = RequestStatus.FAILED.value
    # Don't change current_state - keep the last valid state so state machine can still be loaded
    # The status="failed" indicates failure, not the state
    request.last_error = {
        "error": str(error),
        "traceback": traceback.format_exc(),
        "permanent": True,
        "worker_id": worker_id
    }
    
    # Log permanent failure
    failure = FailureModel(
        id=f"fail-{datetime.utcnow().timestamp()}",
        request_id=request.id,
        task_id=worker_id,
        failure_type="permanent_error",
        error_message=str(error),
        error_details=request.last_error,
        retry_count=request.retry_count,
        occurred_at=datetime.utcnow(),
        resolved=False
    )
    db.add(failure)
    
    # Save state machine (mark as failed)
    try:
        sm = load_state_machine(request, db)
        save_state_machine(db, request, sm)
    except Exception as e:
        logger.error(f"Failed to save state machine for failed request {request.id}: {e}")
    
    db.commit()
    
    logger.error(
        f"Permanent error for request {request.id}, marked as failed: {error}"
    )
