"""
State locking mechanism for concurrent state transitions.
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.db.request import RequestModel


def _utcnow() -> datetime:
    """Naive UTC 'now'.

    The ``locked_until`` column is timezone-naive (stores UTC), so we compare
    and write naive UTC throughout to stay consistent across SQLite (dev) and
    Postgres (prod) and avoid "can't compare offset-naive and offset-aware
    datetimes" errors.
    """
    return datetime.utcnow()


def _to_naive_utc(dt):
    """Coerce a possibly tz-aware datetime (e.g. a legacy row) to naive UTC."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def acquire_lock(db: Session, request_id: str, worker_id: str, timeout_minutes: int = 5) -> bool:
    """
    Acquire lock on request state using PostgreSQL. Returns True if lock acquired.
    
    Args:
        db: Database session
        request_id: Request ID to lock
        worker_id: Worker ID acquiring the lock
        timeout_minutes: Lock timeout in minutes
        
    Returns:
        True if lock acquired, False otherwise
    """
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request:
        return False
    
    # Check if already locked and not expired
    if request.locked_by and request.locked_until:
        if _to_naive_utc(request.locked_until) > _utcnow():
            return False  # Locked by another worker
        # Lock expired, we can take it
    
    # Acquire lock using PostgreSQL UPDATE with WHERE clause for atomicity
    # This provides ACID guarantees
    rows_updated = db.query(RequestModel).filter(
        RequestModel.id == request_id,
        or_(
            RequestModel.locked_by.is_(None),
            RequestModel.locked_until < _utcnow()
        )
    ).update({
        RequestModel.locked_by: worker_id,
        RequestModel.locked_until: _utcnow() + timedelta(minutes=timeout_minutes)
    })
    
    db.commit()
    return rows_updated > 0


def release_lock(db: Session, request_id: str):
    """
    Release lock on request state.
    
    Args:
        db: Database session
        request_id: Request ID to unlock
    """
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request:
        request.locked_by = None
        request.locked_until = None
        db.commit()


def heartbeat_lock(db: Session, request_id: str, worker_id: str, timeout_minutes: int) -> bool:
    """
    Extend lock expiration time (heartbeat) for a request.
    Only extends if the lock is still held by the same worker.
    
    Args:
        db: Database session
        request_id: Request ID to heartbeat
        worker_id: Worker ID that should hold the lock
        timeout_minutes: New timeout in minutes from now
        
    Returns:
        True if heartbeat successful, False if lock not held by this worker
    """
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request:
        return False
    
    # Only extend if we still hold the lock
    if request.locked_by != worker_id:
        return False
    
    # Extend the lock timeout
    rows_updated = db.query(RequestModel).filter(
        RequestModel.id == request_id,
        RequestModel.locked_by == worker_id
    ).update({
        RequestModel.locked_until: _utcnow() + timedelta(minutes=timeout_minutes)
    })
    
    db.commit()
    return rows_updated > 0

