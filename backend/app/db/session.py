"""
Database session management for Lakebase (PostgreSQL).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from app.core.config import settings
from typing import Generator, Optional

# Lazy initialization - only create engine when needed
_engine = None
_SessionLocal = None


def get_database_url() -> Optional[str]:
    """Get database URL, constructing it if needed."""
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
    
    # Only construct URL if all required parts are present
    if (settings.DATABASE_HOST and 
        settings.DATABASE_USER and 
        settings.DATABASE_PASSWORD and 
        settings.DATABASE_NAME):
        return (
            f"postgresql://{settings.DATABASE_USER}:{settings.DATABASE_PASSWORD}"
            f"@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
        )
    
    return None


def get_engine():
    """Get or create database engine (lazy initialization)."""
    global _engine
    if _engine is None:
        database_url = get_database_url()
        if not database_url:
            raise ValueError(
                "Database not configured. Please set DATABASE_URL or "
                "DATABASE_HOST, DATABASE_USER, DATABASE_PASSWORD, and DATABASE_NAME"
            )
        _engine = create_engine(
            database_url,
            poolclass=NullPool,  # Use NullPool for serverless/connection-per-request
            echo=False,  # Set to True for SQL query logging
        )
    return _engine


def get_session_local():
    """Get or create session factory (lazy initialization)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False, 
            autoflush=False, 
            bind=get_engine()
        )
    return _SessionLocal


# For backward compatibility - use get_session_local() function instead
# SessionLocal is now accessed via get_session_local() function


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for getting database session.
    Use this in FastAPI route dependencies.
    """
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_lakebase_session() -> Session:
    """
    Get a database session for use in workers/tasks.
    Caller is responsible for closing the session.
    """
    SessionLocal = get_session_local()
    return SessionLocal()

