"""
FastAPI main application entry point for backend.

This application runs as a Databricks App.
"""
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.api.v1 import router as api_router
from app.core.config import settings
import asyncio
import logging
import os
from pathlib import Path
from app.workers.poller import start_poller
from app.middleware.auth import AuthMiddleware
from app.middleware.profiler import PyinstrumentMiddleware

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.DESCRIPTION,
    version=settings.VERSION,
    # Disable automatic trailing slash redirects - they cause issues with reverse proxies
    # because FastAPI constructs redirect URLs using localhost instead of the actual host
    redirect_slashes=False,
)

# Static directory definition
STATIC_DIR = Path(__file__).parent.parent / "static"

@app.exception_handler(404)
async def spa_fallback_handler(request: Request, exc):
    """
    Catch-all 404 handler to support SPA deep linking.
    If a route isn't found, we serve index.html unless it's an API route.
    """
    path = request.url.path
    
    # Don't intercept API or health routes - let them return real 404s
    if path.startswith("/api/") or path == "/health" or path.startswith("/.auth/"):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Not found"}
        )
    
    # If the file exists physically in static folder, serve it (should have been caught by StaticFiles)
    # This is a safety check
    local_path = STATIC_DIR / path.lstrip("/")
    if local_path.exists() and local_path.is_file():
        return FileResponse(str(local_path))
    
    # Fallback to index.html for all other routes (the SPA will handle routing)
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        logger.info(f"404 intercepted for {path}. Serving SPA index.html")
        return FileResponse(str(index_path))
    
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "Frontend not found"}
    )

@app.on_event("startup")
async def startup_event():
    """Start background tasks and initialize DB."""
    logger.info("Application starting up...")
    
    # Initialize DB (Seed Roles)
    try:
        from app.db.init_db import init_db
        from app.db.session import get_session_local, get_engine
        from app.db.base import Base
        
        # Create Tables (ensure models are loaded via init_db import or explicit import)
        # init_db imports models, so metadata should be populated
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        
        db = get_session_local()()
        try:
            init_db(db)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        # We don't stop startup, but we log strictly
        
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

# Authentication Middleware (Context Propagation)
app.add_middleware(AuthMiddleware)

# Profiling Middleware
app.add_middleware(PyinstrumentMiddleware)

# Include API routes
# Include API routes
app.include_router(api_router, prefix="/api/v1")

# Mount MCP Server (SSE)
# This enables external agents to connect via /mcp/sse
try:
    from app.mcp_server import mcp
    app.mount("/mcp", mcp.sse_app())
    logger.info("Mounted MCP Server at /mcp")
except Exception as e:
    logger.warning(f"Failed to mount MCP Server: {e}")


@app.get("/health")
async def health():
    """Detailed health check endpoint."""
    from app.db.session import get_database_url
    from app.core.config import settings
    url = get_database_url()
    db_type = "SQLite" if "sqlite" in url else "Lakebase (PostgreSQL)"
    
    # Mask URL for safe display
    masked_url = url
    if "@" in url:
        parts = url.split("@")
        prefix = parts[0].split(":")
        if len(prefix) > 2:
            masked_url = f"{prefix[0]}:{prefix[1]}:****@{parts[1]}"
    
    return {
        "status": "healthy",
        "service": "api",
        "platform": "Databricks App",
        "database_type": db_type,
        "database_url": masked_url,
        "env_db_host": settings.DATABASE_HOST,
        "env_db_user": settings.DATABASE_USER
    }


# Static file serving for frontend (production mode)
# The frontend is built and placed in the 'static' directory by CI/CD

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
        return {"status": "ok", "message": "API", "frontend": "index.html not found"}
else:
    logger.info("Static directory not found - running in API-only mode")
    
    @app.get("/")
    async def root():
        """Health check endpoint (API-only mode)."""
        return {
            "status": "ok",
            "message": "API is running",
            "platform": "Databricks App",
            "version": settings.VERSION,
            "frontend": "Not deployed - static directory not found"
        }
