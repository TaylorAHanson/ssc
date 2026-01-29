"""
Reports API endpoints.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from app.db.session import get_db
from app.db.report_subscription import ReportSubscription
from app.db.request import RequestModel
from app.models.request import RequestType
from croniter import croniter, CroniterBadCronError
import uuid

router = APIRouter()

# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------

class PromptDef(BaseModel):
    label: str
    prompt: str

class ReportSubscriptionCreate(BaseModel):
    name: str
    subscribers: str
    schedule_cron: str
    prompts: List[PromptDef]
    is_active: bool = True

class ReportSubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    subscribers: Optional[str] = None
    schedule_cron: Optional[str] = None
    prompts: Optional[List[PromptDef]] = None
    is_active: Optional[bool] = None

class ReportSubscriptionResponse(ReportSubscriptionCreate):
    id: str
    last_run_at: Optional[datetime] = None
    next_run_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True

class ExecutionSummary(BaseModel):
    id: str
    title: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None

# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.get("/subscriptions", response_model=List[ReportSubscriptionResponse])
def list_subscriptions(db: Session = Depends(get_db)):
    """List all scheduled reports."""
    return db.query(ReportSubscription).order_by(desc(ReportSubscription.created_at)).all()

@router.post("/subscriptions", response_model=ReportSubscriptionResponse)
def create_subscription(sub: ReportSubscriptionCreate, db: Session = Depends(get_db)):
    """Create a new scheduled report."""
    
    # Validate cron
    try:
        iter = croniter(sub.schedule_cron, datetime.utcnow())
        next_run = iter.get_next(datetime)
    except (CroniterBadCronError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    prompts_json = [p.dict() for p in sub.prompts]

    new_sub = ReportSubscription(
        id=f"rep-{uuid.uuid4()}",
        name=sub.name,
        subscribers=sub.subscribers,
        schedule_cron=sub.schedule_cron,
        prompts=prompts_json,
        is_active=sub.is_active,
        next_run_at=next_run
    )
    db.add(new_sub)
    db.commit()
    db.refresh(new_sub)
    return new_sub

@router.get("/subscriptions/{id}", response_model=ReportSubscriptionResponse)
def get_subscription(id: str, db: Session = Depends(get_db)):
    """Get a specific subscription."""
    sub = db.query(ReportSubscription).filter(ReportSubscription.id == id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub

@router.put("/subscriptions/{id}", response_model=ReportSubscriptionResponse)
def update_subscription(id: str, update: ReportSubscriptionUpdate, db: Session = Depends(get_db)):
    """Update a subscription."""
    sub = db.query(ReportSubscription).filter(ReportSubscription.id == id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if update.name is not None: sub.name = update.name
    if update.subscribers is not None: sub.subscribers = update.subscribers
    if update.prompts is not None: sub.prompts = [p.dict() for p in update.prompts]
    if update.is_active is not None: sub.is_active = update.is_active
    
    if update.schedule_cron is not None:
        try:
            iter = croniter(update.schedule_cron, datetime.utcnow())
            next_run = iter.get_next(datetime)
            sub.schedule_cron = update.schedule_cron
            sub.next_run_at = next_run
        except (CroniterBadCronError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid cron expression")
            
    db.commit()
    db.refresh(sub)
    return sub

@router.delete("/subscriptions/{id}")
def delete_subscription(id: str, db: Session = Depends(get_db)):
    """Delete a subscription."""
    sub = db.query(ReportSubscription).filter(ReportSubscription.id == id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    db.delete(sub)
    db.commit()
    return {"status": "deleted"}

@router.get("/executions", response_model=List[ExecutionSummary])
def list_executions(subscription_id: Optional[str] = None, db: Session = Depends(get_db)):
    """List execution history (requests)."""
    query = db.query(RequestModel).filter(
        RequestModel.type == RequestType.REPORT_EXECUTION.value
    )
    
    # Filter by subscription ID stored in state_context
    # Since state_context is JSON, we might need a specific filter approach or just fetch and filter in python if volume is low.
    # SQLAlchemy JSON filtering varies by backend. Standard way:
    if subscription_id:
        # PostgreSQL specific syntax usually, but let's try generic string casting or python filtering 
        # since we might be on SQLite in dev.
        # SQLite doesn't support JSON operators easily without specific extensions.
        # Safe fallback: fetch latest 100 and filter
        pass 
        
    runs = query.order_by(desc(RequestModel.created_at)).limit(100).all()
    
    results = []
    for r in runs:
        if subscription_id:
            ctx = r.state_context or {}
            if ctx.get("subscription_id") != subscription_id:
                continue
                
        results.append({
            "id": r.id,
            "title": r.title,
            "status": r.status,
            "created_at": r.created_at,
            "completed_at": r.updated_at if r.status in ["completed", "failed"] else None
        })
        
    return results
