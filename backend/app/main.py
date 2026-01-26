"""
FastAPI main application entry point for EDAS Hub backend.

This application runs as a Databricks App.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.v1 import router as api_router
from app.core.config import settings
import asyncio
import logging
import os
from pathlib import Path
from app.workers.poller import start_poller

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="EDAS Hub API",
    description="Enterprise Data and Analytics Services Self-Service Hub Backend (Databricks App)",
    version="0.0.1",
)

@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    logger.info("Application starting up...")
    logger.info("Starting background poller task...")
    task = asyncio.create_task(start_poller())
    logger.info(f"Background poller task created: {task}")
    # Don't await - let it run in background

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


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


# Static file serving for frontend (production mode)
# The frontend is built and placed in the 'static' directory by CI/CD
STATIC_DIR = Path(__file__).parent.parent / "static"

if STATIC_DIR.exists():
    logger.info(f"Serving static files from: {STATIC_DIR}")
    
    # Mount static assets (JS, CSS, images)
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    
    # Serve index.html for the root
    @app.get("/")
    async def serve_root():
        """Serve the SPA frontend."""
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return {"status": "ok", "message": "EDAS Hub API", "frontend": "index.html not found"}
    
    # Handle all non-API routes for SPA routing
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the SPA frontend for all non-API routes."""
        # Don't intercept API or health routes
        if full_path.startswith("api/") or full_path == "health":
            return {"error": "Not found"}
        
        # Try to serve the exact file first
        file_path = STATIC_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        
        # For all other routes, serve index.html (SPA routing)
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        
        return {"error": "Frontend not found"}
else:
    logger.info("Static directory not found - running in API-only mode")
    
    @app.get("/")
    async def root():
        """Health check endpoint (API-only mode)."""
        return {
            "status": "ok",
            "message": "EDAS Hub API is running",
            "platform": "Databricks App",
            "version": "1.0.0",
            "frontend": "Not deployed - static directory not found"
        }

