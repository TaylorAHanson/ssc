"""
FastAPI main application entry point for backend.

This application runs as a Databricks App.
"""
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
from app.core.logging_formatter import setup_logging
setup_logging(settings.LOG_LEVEL)

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks and initialize DB."""
    logger.info("Application starting up...")

    # Initialize MLflow tracing (no-op unless MLFLOW_TRACING_ENABLED).
    try:
        from app.agents.tracing import init_tracing
        init_tracing()
    except Exception as e:
        logger.warning(f"MLflow tracing init skipped: {e}")
    
    # Initialize DB (Seed Roles)
    try:
        from app.db.init_db import init_db
        from app.db.session import get_session_local, get_engine
        from app.db.base import Base
        import app.db  # Ensure all models are imported and registered with Base.metadata
        
        # Create Tables (ensure models are loaded via init_db import or explicit import)
        # init_db imports models, so metadata should be populated
        engine = get_engine()
        # Apply in-place schema renames BEFORE create_all so renamed tables keep
        # their data instead of being recreated empty alongside the old ones.
        from app.db.migrate import run_startup_migrations, backfill_from_schema
        run_startup_migrations(engine)
        Base.metadata.create_all(bind=engine)
        # One-time adoption of legacy data from another schema (e.g. atlas) into
        # the app-owned DB_SCHEMA. No-op unless DB_MIGRATE_FROM_SCHEMA is set and
        # only fills empty target tables, so it's safe to leave configured.
        from app.db.session import get_db_schema
        backfill_from_schema(
            engine,
            source_schema=(settings.DB_MIGRATE_FROM_SCHEMA or "").strip(),
            target_schema=get_db_schema(),
        )
        
        db = get_session_local()()
        try:
            init_db(db)
            # Seed DB-backed Workflows from the legacy filesystem instructions once
            # (idempotent) so "workflows as data" has content on first boot.
            try:
                from app.services.workflow_service import WorkflowService
                WorkflowService.seed_from_filesystem(db)
                # Attach the workflow graph catalog as editable graph_spec data so
                # the no-code executor can run DB-authored graphs.
                WorkflowService.seed_specs_from_catalog(db)
                # Fold obsolete instruction-only workflow keys (e.g.
                # request_data_access) onto their executable catalog twins and
                # repair any subworkflow refs still pointing at the old keys, so
                # requests never fail with "no workflow graph registered".
                WorkflowService.consolidate_legacy_workflows(db)
                # Remove the legacy workflow-authoring guide from the Context
                # Catalog (idempotent cleanup): the authoring agent now relies on
                # list_workflow_building_blocks as its single source of truth, and
                # the shared catalog search must not surface admin authoring docs.
                from app.services.authoring_guide import remove_authoring_guide
                remove_authoring_guide(db)
                # Seed the dynamic Tool Registry from the locally-defined tools so
                # the agent has its data-driven gating populated on first boot, and
                # newly-added code tools get registry rows (idempotent; never
                # overrides admin toggles).
                from app.services.tool_registry_service import ToolRegistryService
                ToolRegistryService.sync_local_tools(db)
                # Seed training tracks/courses from the legacy training.json on
                # first boot (idempotent once any track exists), so the new
                # DB-backed Training LMS keeps the existing curriculum.
                from app.services.training_seed import seed_training_from_json
                seed_training_from_json(db)
            except Exception as e:
                logger.warning(f"Workflow seeding skipped: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        # We don't stop startup, but we log strictly

    # Start the embedded OPA server unless the user has wired up a remote
    # OPA explicitly via OPA_URL. Spawning OPA in-process turns the per-call
    # ~25ms subprocess startup into ~1ms localhost HTTP — critical for runs
    # over thousands of resources.
    if settings.OPA_EMBEDDED_ENABLED and not (settings.OPA_URL or "").strip():
        try:
            from app.providers.opa.client import OpaProvider
            from app.providers.opa.server_manager import start_embedded_opa

            resolver = OpaProvider({
                "use_local_binary": True,
                "opa_binary": (settings.OPA_BINARY_PATH or "").strip() or None,
                "policies_dir": (settings.OPA_POLICIES_DIR or "policies").strip(),
            })
            opa_exe = resolver._resolve_opa_executable()
            if opa_exe:
                policies_dir = (settings.OPA_POLICIES_DIR or "policies").strip()
                if not os.path.isabs(policies_dir):
                    policies_dir = os.path.join(os.getcwd(), policies_dir)
                start_embedded_opa(policies_dir=policies_dir, opa_binary=opa_exe)
            else:
                logger.warning(
                    "OPA binary could not be resolved; running policies in per-call CLI mode."
                )
        except Exception as e:
            logger.warning(f"Embedded OPA server failed to start: {e}", exc_info=True)

    # Governance & routing posture — make the active configuration explicit in
    # the logs so "built but dormant" guardrails are never silently off.
    if settings.AGENT_TOOL_OPA_ENFORCE:
        logger.info("GOVERNANCE: agent-tool OPA is in ENFORCE mode (mutating policy gates active).")
    else:
        logger.warning(
            "GOVERNANCE: agent-tool OPA is in SHADOW mode (AGENT_TOOL_OPA_ENFORCE=false) — "
            "mutating tool-call decisions are logged but NOT enforced. "
            "Set AGENT_TOOL_OPA_ENFORCE=true in any non-dev environment."
        )
    if (settings.AI_GATEWAY_ENDPOINT or "").strip():
        logger.info("LLM routing: via AI Gateway endpoint '%s'.", settings.AI_GATEWAY_ENDPOINT)
    else:
        logger.info(
            "LLM routing: direct to Model Serving (AI_GATEWAY_ENDPOINT unset) — "
            "set it to route through the gateway for A/B, input guardrails, and rate/cost limits."
        )
    logger.info(
        "Observability: MLflow tracing %s.",
        "ENABLED" if settings.MLFLOW_TRACING_ENABLED else "disabled (set MLFLOW_TRACING_ENABLED=true)",
    )

    if not os.environ.get("TESTING"):
        logger.info("Starting background poller thread...")
        import threading
        def run_poller_thread():
            # Create a new event loop for this thread
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(start_poller())
            except Exception as e:
                logger.error(f"Poller thread crashed: {e}", exc_info=True)
            finally:
                loop.close()
                
        thread = threading.Thread(target=run_poller_thread, daemon=True, name="PollerThread")
        thread.start()

    # Start the MCP Streamable HTTP session manager. The mounted ASGI app
    # (mcp.streamable_http_app(), attached below) only handles requests while
    # its session manager task group is running, so we enter it here for the
    # lifetime of the app and exit it on shutdown. Best-effort: a failure here
    # must not prevent the rest of the API from serving.
    mcp_session_cm = None
    try:
        from app.mcp_server import mcp as _mcp
        session_manager = getattr(_mcp, "session_manager", None)
        if session_manager is not None:
            mcp_session_cm = session_manager.run()
            await mcp_session_cm.__aenter__()
            logger.info("MCP Streamable HTTP session manager started.")
    except Exception as e:
        logger.warning(f"MCP session manager failed to start: {e}", exc_info=True)
        mcp_session_cm = None

    yield

    logger.info("Application shutting down...")
    if mcp_session_cm is not None:
        try:
            await mcp_session_cm.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error stopping MCP session manager: {e}")
    try:
        from app.providers.opa.server_manager import stop_embedded_opa
        stop_embedded_opa()
    except Exception as e:
        logger.warning(f"Error stopping embedded OPA server: {e}")
    # Close the shared async HTTP client used by OpaProvider, if it was opened.
    try:
        from app.providers.opa.client import close_shared_async_client
        await close_shared_async_client()
    except Exception:
        pass

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.DESCRIPTION,
    version=settings.VERSION,
    # Disable automatic trailing slash redirects - they cause issues with reverse proxies
    # because FastAPI constructs redirect URLs using localhost instead of the actual host
    redirect_slashes=False,
    lifespan=lifespan
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

# Mount MCP Server (Streamable HTTP)
# Streamable HTTP is the transport Databricks AI Gateway's custom/external MCP
# registration expects. The session manager's lifespan is started in `lifespan`
# above (the mount only attaches the ASGI app; the manager must be run()).
# The FastMCP streamable app serves at the mount's root, so the canonical
# endpoint is "/mcp/" (with trailing slash). Clients and AI Gateway commonly
# omit the slash; without this redirect a bare "/mcp" would fall through to the
# SPA 404 handler. Registered BEFORE the mount so the exact-path route wins over
# the "/mcp" prefix mount, and uses 307 to preserve the POST method + body.
@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def _mcp_trailing_slash_redirect(request: Request):
    query = request.url.query
    return RedirectResponse(url="/mcp/" + (f"?{query}" if query else ""), status_code=307)


try:
    from app.mcp_server import mcp
    app.mount("/mcp", mcp.streamable_http_app())
    logger.info("Mounted MCP Server (Streamable HTTP) at /mcp")
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
