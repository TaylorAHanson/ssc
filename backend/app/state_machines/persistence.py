"""
State machine persistence to database.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.db.request import RequestModel
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.factory import get_state_machine
import logging

logger = logging.getLogger(__name__)

def load_state_machine(request: RequestModel, db: Session) -> BaseRequestStateMachine:
    """
    Load state machine from database model.
    
    Args:
        request: RequestModel from database
        db: Database session (required for operations)
        
    Returns:
        BaseRequestStateMachine subclass instance
    """
    # The factory handles hydration internally via __init__ -> _hydrate_visual_state
    sm = get_state_machine(request, db)
    return sm

def save_state_machine(db: Session, request: RequestModel, state_machine: BaseRequestStateMachine):
    """
    Save state machine state to database.
    
    Args:
        db: Database session
        request: RequestModel instance
        state_machine: State machine instance
    """
    state_machine.save()
