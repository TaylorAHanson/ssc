import asyncio
import logging
from datetime import datetime
from app.db.session import get_lakebase_session, get_engine
from app.db.base import Base
from app.db.request import RequestModel, ApprovalModel
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.models.request import RequestStatus, PathStateStatus

logger = logging.getLogger(__name__)

async def start_poller():
    """Start the background poller."""
    logger.info("Starting background poller...")
    
    # Ensure tables exist (for SQLite dev)
    try:
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified.")
    except Exception as e:
        logger.error(f"Failed to verify database tables: {e}")
    
    while True:
        try:
            await process_open_requests()
        except Exception as e:
            logger.error(f"Error in poller loop: {e}", exc_info=True)
        
        await asyncio.sleep(2)  # Run every 2 seconds

async def process_open_requests():
    """Find and process all open requests."""
    db = get_lakebase_session()
    try:
        requests = db.query(RequestModel).filter(
            RequestModel.status.notin_([
                RequestStatus.COMPLETED.value, 
                RequestStatus.REJECTED.value,
                "failed"
            ])
        ).all()
        
        for request in requests:
            try:
                await process_single_request(db, request)
            except Exception as e:
                logger.error(f"Error processing request {request.id}: {e}", exc_info=True)
                
    finally:
        db.close()

async def process_single_request(db, request: RequestModel):
    """Process a single request using its specific state machine."""
    try:
        # Load the polymorphic state machine
        sm = load_state_machine(request, db)
        
        # Track changes
        changed = False
        
        # We don't need healing anymore as visual state is calculated on the fly
        # if sm.ensure_visual_consistency():
        #    changed = True
        
    except Exception as e:
        logger.error(f"Failed to load state machine for request {request.id}: {e}")
        return

    # 1. Check for Pending -> Start
    if sm.current_state.id == "pending":
        logger.info(f"Starting request {request.id}")
        if hasattr(sm, 'submit'):
            sm.submit()
            changed = True
            
    # 2. Check Triggers / Transitions
    # The Poller is now "dumb". It just checks for external facts (Approvals, Training)
    # and tells the State Machine to advance if allowed.
    
    # -- Check Approvals --
    latest_approval = db.query(ApprovalModel).filter(
        ApprovalModel.request_id == request.id
    ).order_by(ApprovalModel.updated_at.desc()).first()

    if latest_approval:
        if latest_approval.status == "approved":
            # Determine logic based on current state
            if sm.current_state.id == "manager_approval":
                if hasattr(sm, "approve_manager"):
                    logger.info(f"Manager approved request {request.id}, advancing...")
                    sm.approve_manager()
                    changed = True
            elif sm.current_state.id == "data_owner_approval":
                 if hasattr(sm, "approve_owner"):
                    logger.info(f"Owner approved request {request.id}, advancing...")
                    sm.approve_owner()
                    changed = True
            elif sm.current_state.id == "platform_admin_approval":
                 if hasattr(sm, "approve_admin"):
                    logger.info(f"Admin approved request {request.id}, advancing...")
                    sm.approve_admin()
                    changed = True
                    
        elif latest_approval.status == "rejected":
             if hasattr(sm, "reject"):
                 sm.reject()
                 changed = True

    # -- Check Training --
    if request.requires_training and request.training_completed:
        # If training is done, try to advance
        if sm.current_state.id == "training_pending":
            if hasattr(sm, "complete_training"):
                logger.info(f"Training completed for {request.id}, advancing...")
                sm.complete_training()
                changed = True

    # -- Check Provisioning --
    if sm.current_state.id == "provisioning":
        # Placeholder for actual provisioning logic
        pass

    # Save changes
    if changed:
        save_state_machine(db, request, sm)
        db.commit()
