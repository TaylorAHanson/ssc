import asyncio
import logging
from datetime import datetime
from app.db.session import get_lakebase_session, get_engine
from app.db.base import Base
from app.db.request import RequestModel, ApprovalModel
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.models.request import RequestStatus

logger = logging.getLogger(__name__)

async def start_poller():
    """Start the background poller."""
    logger.info("Starting background poller...")
    
    # Ensure tables exist (for SQLite dev)
    # In prod we use migrations, but for this dev setup we'll create them if missing
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
        
        await asyncio.sleep(10)  # Run every 10 seconds

async def process_open_requests():
    """Find and process all open requests."""
    db = get_lakebase_session()
    try:
        # Find requests that are not completed or rejected
        # Using status string values as stored in DB
        requests = db.query(RequestModel).filter(
            RequestModel.status.notin_([
                RequestStatus.COMPLETED.value, 
                RequestStatus.REJECTED.value,
                "failed" # Custom status for failures
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
    """Process a single request based on its current state."""
    # Load state machine
    try:
        sm = load_state_machine(request)
    except Exception as e:
        logger.error(f"Failed to load state machine for request {request.id}: {e}")
        return

    current_state = sm.current_state.id
    # logger.debug(f"Processing request {request.id} in state {current_state}")
    
    # Track if we made any changes
    changed = False
    
    # State Machine Logic
    
    # 1. Pending -> Manager Approval
    if current_state == "pending":
        logger.info(f"Auto-submitting request {request.id} for approval")
        sm.submit_for_approval()
        changed = True
        
        # Create pending approval record if one doesn't exist
        existing_approval = db.query(ApprovalModel).filter(
            ApprovalModel.request_id == request.id,
            ApprovalModel.status == "pending"
        ).first()
        
        if not existing_approval:
            approval_id = f"app-{datetime.utcnow().timestamp()}"
            new_approval = ApprovalModel(
                id=approval_id,
                request_id=request.id,
                approval_type="manager", # Default, could be derived from request type
                requested_by="system",
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(new_approval)
            logger.info(f"Created pending approval {approval_id} for request {request.id}")
        
    # 2. Manager Approval -> Provisioning (or Rejected)
    elif current_state == "manager_approval":
        # Check if there is an approval record
        approval = db.query(ApprovalModel).filter(
            ApprovalModel.request_id == request.id,
            ApprovalModel.status != "pending"
        ).order_by(ApprovalModel.updated_at.desc()).first()
        
        if approval:
            if approval.status == "approved":
                if request.requires_training and not request.training_completed:
                     logger.info(f"Request {request.id} approved but requires training")
                     sm.require_training()
                else:
                     logger.info(f"Request {request.id} approved, moving to provisioning")
                     sm.approve()
                changed = True
            elif approval.status == "rejected":
                logger.info(f"Request {request.id} rejected by approver")
                sm.reject()
                changed = True
                
    # 3. Training Pending -> Provisioning
    elif current_state == "training_pending":
        if request.training_completed:
            logger.info(f"Request {request.id} training completed, moving to provisioning")
            sm.approve() # Transition name 'approve' goes from training_pending to provisioning too
            changed = True
            
    # 4. Provisioning -> Completed
    elif current_state == "provisioning":
        logger.info(f"Provisioning request {request.id}...")
        
        # Execute provisioning logic here
        # For now, simulate success with a delay (async sleep doesn't block other tasks if we were concurrent, 
        # but here we are sequential per loop pass. That's fine for now.)
        
        # TODO: Integrate real provisioners (Terraform/GitHub/Databricks) here
        
        sm.complete()
        changed = True
        logger.info(f"Request {request.id} provisioning completed")

    # Save changes if any
    if changed:
        save_state_machine(db, request, sm)
        # Update timestamp
        request.updated_at = datetime.utcnow()
        db.commit()
