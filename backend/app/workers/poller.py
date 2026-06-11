"""
Polling worker for processing request state machine transitions.

This worker polls the database for pending requests and processes them
in parallel with proper locking, error handling, and retry logic.

Also polls the GitOps volume for status updates on Terraform requests.
"""
import asyncio
import logging
import os
import socket
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db.session import get_db, get_engine, reset_database_connection
from app.db.base import Base
from app.db import RequestModel, FailureModel
from app.state_machines.lock import acquire_lock, release_lock, heartbeat_lock
from app.models.request import RequestStatus, RequestType
from app.db.report_subscription import ReportSubscription
from croniter import croniter, CroniterBadCronError
import uuid
from app.core.config import settings, _yaml_config
from app.core.exceptions import RetryableError, PermanentError
from app.workers.tasks.sync_calendar import sync_calendar_task
from app.workers.tasks.sync_data_assets import sync_data_assets_task
import traceback

logger = logging.getLogger(__name__)

# States that require GitOps volume polling
TERRAFORM_POLLING_STATES = {"terraform_planning", "awaiting_approval", "terraform_applying"}

# Generate unique worker ID
_worker_id = f"poll-worker-{socket.gethostname()}-{os.getpid()}"

_next_sentinel_time = None

async def process_enforcement_sentinel_cron():
    """Check if it's time to run the enforcement sentinel and spawn it."""
    global _next_sentinel_time
    now = datetime.now(timezone.utc)
    
    cron_expr = getattr(settings, 'ENFORCEMENT_SENTINEL_CRON', '*/30 * * * *')
    if not cron_expr:
        return
        
    if _next_sentinel_time is None:
        try:
            iter = croniter(cron_expr, now)
            _next_sentinel_time = iter.get_next(datetime)
        except CroniterBadCronError:
            logger.error(f"Invalid ENFORCEMENT_SENTINEL_CRON expression: {cron_expr}")
            return
            
    if now >= _next_sentinel_time:
        # Time to run
        db = next(get_db())
        try:
            # Check if there is an active (pending/processing) sentinel run to avoid duplicates
            from app.models.request import RequestModel, RequestType, RequestStatus
            active_run = db.query(RequestModel).filter(
                RequestModel.type == RequestType.ENFORCEMENT_SENTINEL.value,
                RequestModel.status.in_([RequestStatus.PENDING.value, RequestStatus.PROCESSING.value])
            ).first()
            
            if not active_run:
                req_id = f"req-{uuid.uuid4()}"
                new_request = RequestModel(
                    id=req_id,
                    type=RequestType.ENFORCEMENT_SENTINEL.value,
                    title=f"Scheduled Sentinel Run",
                    status=RequestStatus.PENDING.value,
                    current_state="pending",
                    state_context={"enforcement_mode": "audit_only"},
                    created_at=now,
                    updated_at=now
                )
                db.add(new_request)
                db.commit()
                logger.info(f"Spawned scheduled Enforcement Sentinel request {req_id}")
            else:
                logger.info("Skipping scheduled Enforcement Sentinel run as one is already active.")
                
        except Exception as e:
            logger.error(f"Failed to spawn scheduled Sentinel run: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
            
        # Calculate next time
        try:
            iter = croniter(cron_expr, now)
            _next_sentinel_time = iter.get_next(datetime)
        except CroniterBadCronError:
            pass



async def start_poller():
    """Start the background poller."""
    logger.info(f"Starting background poller (worker_id: {_worker_id})...")
    
    # Ensure tables exist (for SQLite dev)
    try:
        import app.db  # Ensure all models are registered
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
    
    # Use a semaphore to limit concurrent asset sync tasks if they run long
    sync_semaphore = asyncio.Semaphore(1)
    
    while True:
        poll_count += 1
        try:
            logger.debug(f"Poller cycle #{poll_count} - checking for requests...")
            
            # Start sync tasks in background so they don't block request processing
            if _yaml_config.get("features", {}).get("calendar", False):
                asyncio.create_task(sync_calendar_task())
                
            if _yaml_config.get("features", {}).get("data_discovery", False):
                async def safe_sync_data_assets():
                    if sync_semaphore.locked():
                        return # Already syncing
                    async with sync_semaphore:
                        try:
                            await sync_data_assets_task()
                        except Exception as e:
                            logger.error(f"Error in background data asset sync: {e}", exc_info=True)
                
                asyncio.create_task(safe_sync_data_assets())

            if _yaml_config.get("features", {}).get("sentinel", False):
                await process_enforcement_sentinel_cron()

            await process_open_requests()
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
    db = next(get_db())
    try:
        # Find requests that need processing (not completed/rejected/failed)
        # and are not locked (or lock has expired)
        now = datetime.now(timezone.utc)
        # Process pending requests AND failed requests that might be recoverable
        # Failed requests in non-terminal states (like terraform_applying) may have
        # success facts that can transition them to completed
        terminal_states = {"completed", "rejected", "failed"}
        
        requests = db.query(RequestModel).filter(
            or_(
                # Normal pending requests
                RequestModel.status.notin_([
                    RequestStatus.COMPLETED.value, 
                    RequestStatus.REJECTED.value,
                    "failed"
                ]),
                # Failed requests NOT in terminal states (may be recoverable)
                (RequestModel.status == "failed") & (RequestModel.current_state.notin_(terminal_states))
            ),
            # Only process requests that are not locked or have expired locks.
            # locked_until is a timezone-naive UTC column, so compare against
            # naive UTC to avoid offset-naive vs offset-aware comparison errors.
            or_(
                RequestModel.locked_by.is_(None),
                RequestModel.locked_until < datetime.utcnow()
            ),
            # Only if we haven't exhausted retry attempts
            RequestModel.retry_count < RequestModel.max_retries
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
    db = next(get_db())
    try:
        now = datetime.now(timezone.utc)
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


async def _send_failure_notification(request: RequestModel, error_message: str):
    """Send an email notification if an enforcement sentinel request fails."""
    if request.type != RequestType.ENFORCEMENT_SENTINEL.value:
        return
        
    try:
        from app.providers.notifications.client import NotificationProvider
        notifier = NotificationProvider()
        to_email = settings.GOVERNANCE_EMAIL_GROUP
        
        subject = f"[Action Required] Enforcement Sentinel Run Failed: {request.title}"
        body = (
            f"An Enforcement Sentinel run failed and requires your attention.<br><br>"
            f"<b>Request ID:</b> {request.id}<br>"
            f"<b>Title:</b> {request.title}<br>"
            f"<b>Error:</b> {error_message}<br><br>"
            f"Please check the self-service center UI for more details."
        )
        
        # Don't fail the poller if notification fails
        await notifier.send_email(to=to_email, subject=subject, body=body, is_html=True)
        logger.info(f"Sent failure notification for sentinel run {request.id} to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send failure notification for {request.id}: {e}", exc_info=True)

async def process_single_request(semaphore: asyncio.Semaphore, request_id: str):
    """Process a single request with locking and error handling."""
    from app.core.logging_formatter import current_request_id, current_endpoint
    
    # Set request_id context variable for logging all state machine execution steps
    req_id_token = current_request_id.set(request_id)
    endpoint_token = current_endpoint.set("PollerWorker")
    
    async with semaphore:  # Limit concurrent processing
        db = next(get_db())
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
                await _send_failure_notification(request, f"Exceeded max retries ({request.max_retries})")
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
            current_request_id.reset(req_id_token)
            current_endpoint.reset(endpoint_token)


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
            db = next(get_db())
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


async def _check_gitops_volume_status(db: Session, request: RequestModel):
    """
    Check the GitOps volume for status updates and add facts if needed.
    
    This bridges the volume-based GitOps flow with the app's fact-based state machine.
    When GitHub Actions updates the volume status, this function detects the changes
    and adds the appropriate facts to trigger state transitions.
    
    Only processes requests that came from this app (have status files in the volume).
    Manual PRs in the Terraform repo won't have status files, so they're ignored.
    """
    from app.state_machines.facts import has_fact, add_fact
    
    # Only poll for requests in terraform states
    if request.current_state not in TERRAFORM_POLLING_STATES:
        return
    
    # Only poll if GitOps mode is "volume"
    gitops_mode = getattr(settings, 'GITOPS_MODE', 'volume')
    if gitops_mode != "volume":
        return
    
    try:
        from app.providers.terraform.volume_provider import VolumeGitOpsProvider
        
        # Initialize the volume provider
        provider = VolumeGitOpsProvider(
            volume_path=getattr(settings, 'GITOPS_VOLUME_PATH', '/Volumes/atlas_dev_catalog/atlas/gitops_requests'),
            config={"environment": getattr(settings, 'DEFAULT_ENVIRONMENT', 'dev')}
        )
        
        # Get status from volume
        status = await provider.get_status(request.id)
        
        if not status:
            logger.debug(f"[{request.id}] No volume status found yet")
            return
        
        logger.debug(f"[{request.id}] Volume status: {status}")
        
        # Check for plan completion (PR created)
        if request.current_state == "terraform_planning":
            pr_state = status.get("pr_state")
            pr_url = status.get("pr_url")
            
            # If PR is created (open or merged), plan is complete
            if pr_state in ("open", "merged") and not has_fact(db, request.id, "terraform_plan_received"):
                logger.info(f"[{request.id}] Volume shows PR created (state={pr_state}), adding terraform_plan_received fact")
                add_fact(db, request.id, "terraform_plan_received", {
                    "status": "success",
                    "pr_url": pr_url,
                    "pr_state": pr_state,
                    "plan_output": status.get("plan_output", ""),
                    "source": "volume_poll"
                })
                
                # If PR is already merged, that's the platform admin approval
                # The merge action IS the approval - no separate approval step needed
                if pr_state == "merged" and not has_fact(db, request.id, "approval_received", approval_type="platform_admin"):
                    logger.info(f"[{request.id}] PR already merged - adding platform admin approval")
                    add_fact(db, request.id, "approval_received", {
                        "approval_type": "platform_admin",
                        "approved": True,
                        "pr_url": pr_url,
                        "source": "volume_poll"
                    })
                
                db.commit()
            
            # Edge case: apply already failed but we missed the plan_received transition
            # This can happen if the app wasn't polling when the PR was created/merged
            elif pr_state == "apply_failed" or status.get("apply_success") is False:
                logger.warning(f"[{request.id}] Volume shows apply_failed but request still in planning - fast-forwarding to failed")
                error_msg = status.get("error", "Terraform apply failed before app could track it")
                
                # Add both facts to transition: planning -> awaiting_approval -> applying -> failed
                if not has_fact(db, request.id, "terraform_plan_received"):
                    add_fact(db, request.id, "terraform_plan_received", {
                        "status": "success",
                        "pr_url": pr_url,
                        "pr_state": "merged",  # It was merged since apply ran
                        "source": "volume_poll_recovery"
                    })
                if not has_fact(db, request.id, "approval_received", approval_type="platform_admin"):
                    add_fact(db, request.id, "approval_received", {
                        "approval_type": "platform_admin",
                        "approved": True,
                        "source": "volume_poll_recovery"
                    })
                if not has_fact(db, request.id, "terraform_apply_received"):
                    add_fact(db, request.id, "terraform_apply_received", {
                        "status": "failure",
                        "error": error_msg,
                        "source": "volume_poll_recovery"
                    })
                db.commit()
        
        # Check for PR merge while awaiting approval
        elif request.current_state == "awaiting_approval":
            pr_state = status.get("pr_state")
            pr_url = status.get("pr_url")
            
            # If PR was merged, that's the platform admin approval
            if pr_state == "merged" and not has_fact(db, request.id, "approval_received", approval_type="platform_admin"):
                logger.info(f"[{request.id}] PR merged while awaiting approval - adding platform admin approval")
                add_fact(db, request.id, "approval_received", {
                    "approval_type": "platform_admin",
                    "approved": True,
                    "pr_url": pr_url,
                    "source": "volume_poll"
                })
                db.commit()
            
            # Edge case: apply already completed or failed while waiting for approval
            elif pr_state in ("applied", "apply_failed") or status.get("apply_success") is not None:
                logger.warning(f"[{request.id}] Apply already ran while awaiting approval - fast-forwarding")
                
                # Add approval fact if missing
                if not has_fact(db, request.id, "approval_received", approval_type="platform_admin"):
                    add_fact(db, request.id, "approval_received", {
                        "approval_type": "platform_admin",
                        "approved": True,
                        "source": "volume_poll_recovery"
                    })
                
                # Add apply result fact
                if not has_fact(db, request.id, "terraform_apply_received"):
                    if status.get("apply_success") is True or pr_state == "applied":
                        add_fact(db, request.id, "terraform_apply_received", {
                            "status": "success",
                            "source": "volume_poll_recovery"
                        })
                    else:
                        add_fact(db, request.id, "terraform_apply_received", {
                            "status": "failure",
                            "error": status.get("error", "Apply failed"),
                            "source": "volume_poll_recovery"
                        })
                db.commit()
        
        # Check for apply completion
        elif request.current_state == "terraform_applying":
            apply_success = status.get("apply_success")
            pr_state = status.get("pr_state")
            
            # If apply succeeded (marked as applied)
            if (apply_success is True or pr_state == "applied") and not has_fact(db, request.id, "terraform_apply_received"):
                logger.info(f"[{request.id}] Volume shows apply complete, adding terraform_apply_received fact")
                add_fact(db, request.id, "terraform_apply_received", {
                    "status": "success",
                    "apply_output": status.get("apply_output", ""),
                    "source": "volume_poll"
                })
                db.commit()
            
            # Check for apply failure (apply_success: false OR pr_state: apply_failed OR error present)
            elif (apply_success is False or pr_state == "apply_failed" or status.get("error")) and not has_fact(db, request.id, "terraform_apply_received"):
                error_msg = status.get("error", "Terraform apply failed. Check the GitHub Actions logs for details.")
                logger.warning(f"[{request.id}] Volume shows apply failed: {error_msg}")
                add_fact(db, request.id, "terraform_apply_received", {
                    "status": "failure",
                    "error": error_msg,
                    "source": "volume_poll"
                })
                db.commit()
                
    except Exception as e:
        # Don't fail the whole processing if volume polling fails
        logger.warning(f"[{request.id}] Failed to check volume status: {e}")


def _v2_resume_value(db, request, result):
    """Map approval/event facts to a gate resume value, or None if still waiting.

    Bridges the existing fact/approval API (``approval_received``,
    ``training_completed``, ``pr_merged``, ``request_rejected``) to the V2
    graph's uniform gate-resume contract ``{"approved": bool}``.
    """
    from app.state_machines.facts import get_facts, has_fact

    if not result.interrupted:
        return None

    # Rejection short-circuits any gate.
    if has_fact(db, request.id, "request_rejected"):
        return {"approved": False, "reason": "rejected"}

    gtype = (result.interrupt_payload or {}).get("type")
    if gtype in ("manager", "platform_admin", "data_owner"):
        approved = any(
            f.event_type == "approval_received"
            and (f.event_data or {}).get("approval_type") == gtype
            for f in get_facts(db, request.id)
        )
        return {"approved": True} if approved else None
    if gtype == "training":
        return {"approved": True} if has_fact(db, request.id, "training_completed") else None
    if gtype == "pr_merge":
        return {"approved": True} if has_fact(db, request.id, "pr_merged") else None
    if gtype == "children":
        return {"approved": True} if has_fact(db, request.id, "all_children_completed") else None
    return None


async def _process_request_state_machine(db, request: RequestModel):
    """Advance a request through its V2 durable LangGraph workflow.

    The poller stays ignorant of business logic: it advances the graph, and when
    the graph is paused on a HITL gate, resumes it iff the corresponding
    approval/event fact is present. State is owned by the durable checkpointer;
    we only sync ``request.status`` for the UI.
    """
    from app.models.request import RequestStatus
    from app.v2.executor import executor as v2_executor, to_request_status

    logger.debug(f"[{request.id}] V2 advance - status: {request.status}")

    # Run from the last checkpoint (first pass runs from START to the first gate).
    result = await v2_executor.advance(request)

    # Resume as many satisfied gates as we can this tick (a gate may unblock the
    # next one, e.g. manager -> platform_admin in the same poll cycle).
    for _ in range(12):
        resume_value = _v2_resume_value(db, request, result)
        if resume_value is None:
            break
        result = await v2_executor.resume(request, resume_value)

    # Sync request.status for the UI/poller selection.
    new_status = to_request_status(result.status)
    if new_status is not None and request.status != new_status.value:
        logger.info(f"[{request.id}] V2 status: {request.status} -> {new_status.value}")
        request.status = new_status.value
        request.current_state = result.current_node or result.status
        db.commit()


async def _handle_retryable_error(
    db: Session, 
    request: RequestModel, 
    error: RetryableError, 
    worker_id: str
):
    """Handle a retryable error by incrementing retry count and logging."""
    request.retry_count += 1
    request.last_failure = datetime.now(timezone.utc)
    request.last_error = {
        "error": str(error),
        "traceback": traceback.format_exc(),
        "retry_count": request.retry_count,
        "worker_id": worker_id
    }
    
    # Check if max retries exceeded
    if request.retry_count >= request.max_retries:
        logger.warning(
            f"Request {request.id} exceeded max retries ({request.max_retries}), "
            "marking as failed"
        )
        request.status = RequestStatus.FAILED.value
        # Don't change current_state - keep the last valid state so state machine can still be loaded
    
    # Log failure to failures table
    failure = FailureModel(
        id=f"fail-{datetime.now(timezone.utc).timestamp()}",
        request_id=request.id,
        task_id=worker_id,
        failure_type="retryable_error",
        error_message=str(error),
        error_details=request.last_error,
        retry_count=request.retry_count,
        occurred_at=datetime.now(timezone.utc)
    )
    db.add(failure)
    db.commit()
    
    if request.status == RequestStatus.FAILED.value:
        await _send_failure_notification(request, f"Exceeded max retries ({request.max_retries}) after error: {error}")
    else:
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
    request.retry_count = request.max_retries  # Exhaust retries so poller stops picking it up
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
        id=f"fail-{datetime.now(timezone.utc).timestamp()}",
        request_id=request.id,
        task_id=worker_id,
        failure_type="permanent_error",
        error_message=str(error),
        error_details=request.last_error,
        retry_count=request.retry_count,
        occurred_at=datetime.now(timezone.utc),
        resolved=False
    )
    db.add(failure)

    # V2: mark the request failed directly (no state machine to persist).
    try:
        from app.models.request import RequestStatus
        request.status = RequestStatus.FAILED.value
    except Exception as e:
        logger.error(f"Failed to mark request {request.id} failed: {e}")

    db.commit()
    
    logger.error(
        f"Permanent error for request {request.id}, marked as failed: {error}"
    )
    
    await _send_failure_notification(request, str(error))
