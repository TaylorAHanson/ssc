import sys
import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add current directory to path (assuming running from backend/)
sys.path.append(os.getcwd())
# Also try adding parent directory just in case
sys.path.append(os.path.dirname(os.getcwd()))

from app.db.base import Base
from app.db.request import RequestModel
from app.core.config import settings
from app.state_machines.factory import get_state_machine
from app.db.session import get_database_url
# Setup DB
print(f"Connecting to DB...")
engine = create_engine(get_database_url())
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

# Get latest request
request = db.query(RequestModel).order_by(RequestModel.created_at.desc()).first()

if request:
    print(f"Request ID: {request.id}")
    print(f"Type: {request.type}")
    print(f"Current State: {request.current_state}")
    
    # Get State Machine
    try:
        sm = get_state_machine(request, db)
        sm_state = sm.to_state_machine_state()
        print("\nState Machine Data:")
        for s in sm_state.states:
            print(f"  - ID: {s.id}, Name: {s.name}, Active: {s.isActive}, Completed: {s.isCompleted}")
    except Exception as e:
        print(f"Error: {e}")
    # Get Approvals
    from app.db.request import ApprovalModel
    approvals = db.query(ApprovalModel).filter(ApprovalModel.request_id == request.id).all()
    print(f"\nApprovals ({len(approvals)}):")
    for a in approvals:
        print(f"  - ID: {a.id}, Type: {a.approval_type}, Status: {a.status}, Created: {a.created_at}")
else:
    print("No requests found.")
