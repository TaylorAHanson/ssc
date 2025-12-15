"""
FastAPI main application entry point for EDAS Hub backend.

This application runs as a Databricks App.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import router as api_router
from app.core.config import settings

app = FastAPI(
    title="EDAS Hub API",
    description="Enterprise Data and Analytics Services Self-Service Hub Backend (Databricks App)",
    version="1.0.0",
)

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
    return {
        "status": "healthy",
        "service": "edas-hub-api",
        "platform": "Databricks App",
        "version": "1.0.0",
        "database": "Lakebase (PostgreSQL)",
        "workers": "ARQ"
    }

