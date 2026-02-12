"""
Fact management for fact-based state calculation.

Facts are immutable events that represent what has happened.
State is calculated from facts, not stored directly.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import EventModel, RequestModel
import uuid
import logging

logger = logging.getLogger(__name__)


def add_fact(
    db: Session,
    request_id: str,
    fact_type: str,
    fact_data: Dict[str, Any],
    actor: Optional[str] = None
) -> EventModel:
    """
    Add a fact (immutable event) to the request's fact history.
    
    Facts are the source of truth. State is calculated from facts.
    
    Args:
        db: Database session
        request_id: Request ID
        fact_type: Type of fact (e.g., 'approval_received', 'workspace_created', 'training_completed')
        fact_data: Fact-specific data
        actor: Who/what caused this fact (e.g., 'manager_123', 'terraform', 'system')
        
    Returns:
        Created EventModel instance
    """
    fact_id = f"fact-{uuid.uuid4()}"
    
    fact = EventModel(
        id=fact_id,
        request_id=request_id,
        event_type=fact_type,
        event_data={
            **fact_data,
            "actor": actor,
            "timestamp": datetime.utcnow().isoformat()
        },
        created_at=datetime.utcnow()
    )
    
    db.add(fact)
    db.commit()
    db.refresh(fact)
    
    logger.info(f"Added fact {fact_type} for request {request_id}")
    return fact


def get_facts(
    db: Session,
    request_id: str,
    fact_type: Optional[str] = None
) -> List[EventModel]:
    """
    Get all facts (or facts of a specific type) for a request.
    
    Args:
        db: Database session
        request_id: Request ID
        fact_type: Optional filter by fact type
        
    Returns:
        List of EventModel instances, ordered by creation time
    """
    query = db.query(EventModel).filter(EventModel.request_id == request_id)
    
    if fact_type:
        query = query.filter(EventModel.event_type == fact_type)
    
    return query.order_by(EventModel.created_at.asc()).all()


def has_fact(
    db: Session,
    request_id: str,
    fact_type: str,
    **conditions
) -> bool:
    """
    Check if a request has a fact of a given type, optionally matching conditions.
    
    Args:
        db: Database session
        request_id: Request ID
        fact_type: Type of fact to check
        **conditions: Optional key-value pairs to match in fact_data
        
    Returns:
        True if matching fact exists, False otherwise
        
    Example:
        has_fact(db, "req-123", "workspace_created", workspace_id="ws-456")
    """
    facts = get_facts(db, request_id, fact_type)
    
    if not facts:
        return False
    
    if not conditions:
        return True
    
    # Check if any fact matches all conditions
    for fact in facts:
        fact_data = fact.event_data or {}
        if all(
            fact_data.get(key) == value
            for key, value in conditions.items()
        ):
            return True
    
    return False


def get_latest_fact(
    db: Session,
    request_id: str,
    fact_type: str,
    **conditions
) -> Optional[EventModel]:
    """
    Get the most recent fact of a given type, optionally matching conditions.
    
    Args:
        db: Database session
        request_id: Request ID
        fact_type: Type of fact
        **conditions: Optional key-value pairs to match in fact_data
        
    Returns:
        Most recent matching EventModel of that type, or None
    """
    facts = get_facts(db, request_id, fact_type)
    
    if not facts:
        return None
        
    if not conditions:
        return facts[-1]
        
    # Check facts in reverse order (latest first)
    for fact in reversed(facts):
        fact_data = fact.event_data or {}
        if all(
            fact_data.get(key) == value
            for key, value in conditions.items()
        ):
            return fact
            
    return None


def get_fact_data(
    db: Session,
    request_id: str,
    fact_type: str,
    default: Any = None
) -> Any:
    """
    Get the data from the most recent fact of a given type.
    
    Args:
        db: Database session
        request_id: Request ID
        fact_type: Type of fact
        default: Default value if fact doesn't exist
        
    Returns:
        event_data from the most recent fact, or default
    """
    fact = get_latest_fact(db, request_id, fact_type)
    if fact:
        return fact.event_data
    return default

