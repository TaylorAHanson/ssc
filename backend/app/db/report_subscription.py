from sqlalchemy import Column, String, DateTime, Boolean, JSON, func
from app.db.base import Base

class ReportSubscription(Base):
    __tablename__ = "report_subscriptions"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)          # e.g., 'Weekly Admin Audit'
    subscribers = Column(String, nullable=False)   # Comma-separated emails
    schedule_cron = Column(String, nullable=False) # e.g., '0 7 * * 1'
    
    # Dynamic definition
    prompts = Column(JSON, nullable=False)         # List of {label, prompt} objects
    
    # State
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=False)    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
