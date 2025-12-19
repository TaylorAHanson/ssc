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
from typing import Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db.session import get_lakebase_session, get_engine
from app.db.base import Base
from app.db.request import RequestModel, FailureModel
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.state_machines.lock import acquire_lock, release_lock, heartbeat_lock
from app.models.request import RequestStatus
from app.core.config import settings
from app.core.exceptions import RetryableError, PermanentError, ValidationError
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
    while True:
        poll_count += 1
        try:
            logger.debug(f"Poller cycle #{poll_count} - checking for requests...")
            await process_open_requests()
        except Exception as e:
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
        
        logger.info(f"Found {len(requests)} request(s) to process: {[f'{r.id} ({r.current_state})' for r in requests]}")
        
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
    
    logger.info(f"[{request.id}] Processing request - Current state: {request.current_state}, Status: {request.status}")
    
    # Load the polymorphic state machine
    sm = load_state_machine(request, db)
    initial_state = sm.current_state.id
    logger.info(f"[{request.id}] Loaded state machine - Current state: {initial_state}")
    
    # Log relevant facts for debugging
    from app.db.request import EventModel
    facts = db.query(EventModel).filter(
        EventModel.request_id == request.id,
        EventModel.event_type.in_(["request_submitted", "approval_received", "training_completed", "workspace_created", "provisioning_started", "request_rejected"])
    ).all()
    if facts:
        fact_summary = {f.event_type: getattr(f, 'event_data', {}) for f in facts}
        logger.info(f"[{request.id}] Relevant facts: {fact_summary}")
    
    # Let the state machine handle all logic
    logger.info(f"[{request.id}] Calling state machine tick()...")
    changed = sm.tick()
    new_state = sm.current_state.id
    
    if changed:
        logger.info(f"[{request.id}] State changed: {initial_state} -> {new_state}")
    else:
        logger.info(f"[{request.id}] No state change (still in {initial_state})")
    
    # Handle async tool execution for provisioning
    # Check if we're in provisioning state and need to start provisioning
    if sm.current_state.id == "provisioning":
        if not has_fact(db, request.id, "provisioning_started") and not has_fact(db, request.id, "workspace_created"):
            # Start provisioning asynchronously
            logger.info(f"[{request.id}] Starting async workspace provisioning...")
            try:
                await _execute_provisioning_tool(db, request, sm)
                # Reload state machine after tool execution to pick up new facts
                sm = load_state_machine(request, db)
                logger.info(f"[{request.id}] Re-running tick() after provisioning tool execution...")
                changed = sm.tick()  # Process any new transitions based on workspace_created fact
                if changed:
                    logger.info(f"[{request.id}] State changed after provisioning: {sm.current_state.id}")
            except Exception as e:
                logger.error(f"[{request.id}] Error executing provisioning tool: {e}", exc_info=True)
                raise
    
    # Save if state changed
    if changed:
        save_state_machine(db, request, sm)
        db.commit()
        logger.info(f"[{request.id}] Saved state machine - New state: {sm.current_state.id}")
    else:
        logger.debug(f"[{request.id}] No state change, skipping save")


async def _execute_provisioning_tool(db, request: RequestModel, sm):
    """
    Execute the provisioning tool for workspace creation.
    
    This is called by the poller when a request enters provisioning state.
    """
    from app.tools.workspace import CreateWorkspaceTool
    
    # Extract configuration from request
    state_context = request.state_context or {}
    
    # Get workspace name and environment
    workspace_name = state_context.get("workspace_name")
    if not workspace_name:
        # Try to extract from title
        if ":" in request.title:
            workspace_name = request.title.split(":")[-1].strip()
        else:
            workspace_name = request.title
    
    environment = request.environment or "dev"
    
    # Build config for tool
    # Get Databricks credentials from state_context (form data) or fall back to settings
    from app.core.config import settings
    
    config = {
        "databricks_account_id": (
            state_context.get("databricks_account_id") or 
            settings.DATABRICKS_ACCOUNT_ID
        ),
        "client_id": (
            state_context.get("client_id") or 
            settings.DATABRICKS_CLIENT_ID
        ),
        "client_secret": (
            state_context.get("client_secret") or 
            settings.DATABRICKS_CLIENT_SECRET
        ),
        "region": state_context.get("region", "eu-west-1"),
        "cidr_block": state_context.get("cidr_block", "10.4.0.0/16"),
        "tags": state_context.get("tags", {}),
        **state_context  # Include any other config
    }
    
    # Validate required config
    if not config.get("databricks_account_id"):
        raise ValidationError(
            "databricks_account_id is required. "
            "Set in request metadata or DATABRICKS_ACCOUNT_ID environment variable."
        )
    if not config.get("client_id"):
        raise ValidationError(
            "client_id is required. "
            "Set in request metadata or DATABRICKS_CLIENT_ID environment variable."
        )
    if not config.get("client_secret"):
        raise ValidationError(
            "client_secret is required. "
            "Set in request metadata or DATABRICKS_CLIENT_SECRET environment variable."
        )
    
    # Get requested_by from request
    requested_by = state_context.get("requested_by") or "system"
    
    # Create and execute tool
    tool = CreateWorkspaceTool()
    
    # Allow patching tool providers for testing
    # In tests, providers can be mocked and injected here
    if hasattr(tool, '_test_providers'):
        for provider_name, provider_instance in tool._test_providers.items():
            setattr(tool, provider_name, provider_instance)
    
    result = await tool.execute(
        request_id=request.id,
        name=workspace_name,
        environment=environment,
        config=config,
        requested_by=requested_by,
        db=db
    )
    
    logger.info(f"Workspace provisioning completed for request {request.id}: {result}")


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
