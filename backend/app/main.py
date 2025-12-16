"""
FastAPI main application entry point for EDAS Hub backend.

This application runs as a Databricks App.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import router as api_router
from app.core.config import settings
import asyncio
import logging
from app.workers.poller import start_poller

logger = logging.getLogger(__name__)

app = FastAPI(
    title="EDAS Hub API",
    description="Enterprise Data and Analytics Services Self-Service Hub Backend (Databricks App)",
    version="1.0.0",
)

@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    logger.info("Application starting up...")
    asyncio.create_task(start_poller())

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "EDAS Hub API is running",
        "platform": "Databricks App",
        "version": "1.0.0"
    }


@app.get("/health")
async def health():
    """Detailed health check endpoint."""
    from app.db.session import get_database_url
    db_type = "SQLite" if "sqlite" in get_database_url() else "Lakebase (PostgreSQL)"
    
    return {
        "status": "healthy",
        "service": "edas-hub-api",
        "platform": "Databricks App",
        "version": "1.0.0",
        "database": db_type,
        "workers": "Background Poller"
    }

