"""
Database session management for Lakebase (PostgreSQL) and SQLite (Dev).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, StaticPool
from app.core.config import settings
from typing import Generator, Optional
from urllib.parse import quote_plus
import os
import logging

logger = logging.getLogger(__name__)

# Lazy initialization - only create engine when needed
_engine = None
_SessionLocal = None
_connection_verified = False


def reset_database_connection():
    """Reset database engine and session factory (useful if credentials change)."""
    global _engine, _SessionLocal, _connection_verified
    logger.warning("Resetting database connection...")
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    _connection_verified = False


def get_database_url() -> str:
    """
    Get database URL, constructing it if needed.
    
    For Lakebase (Databricks PostgreSQL), we use native postgres roles
    with password from Databricks Secret Scope.
    """
    # If a full DATABASE_URL is provided, use it directly
    # WARNING: This bypasses all our logic - log it prominently
    if settings.DATABASE_URL:
        logger.warning(f"DATABASE_URL env var is set - using it directly!")
        logger.warning(f"First 50 chars: {settings.DATABASE_URL[:50]}...")
        return settings.DATABASE_URL
    
    # Check for Postgres/Lakebase config
    host = settings.DATABASE_HOST
    user = settings.DATABASE_USER or "atlas_app"  # Native Postgres role
    name = settings.DATABASE_NAME
    password = None
    
    # Clear ALL env vars that psycopg2/libpq might pick up
    # See: https://www.postgresql.org/docs/current/libpq-envars.html
    for pg_var in ["PGUSER", "PGPASSWORD", "PGHOST", "PGPORT", "PGDATABASE", "PGOPTIONS"]:
        old_val = os.environ.pop(pg_var, None)
        if old_val:
            logger.warning(f"Cleared {pg_var} env var (was: {old_val[:20] if pg_var != 'PGPASSWORD' else '***'}...)")
    
    # DEBUG: Print what we're using
    logger.debug(f"=== DATABASE SETTINGS DEBUG ===")
    logger.debug(f"FORCING DB USER = {user}")
    logger.debug(f"settings.DATABASE_HOST = {host}")
    logger.debug(f"settings.DATABASE_NAME = {name}")
    
    # Try to get password/token for Lakebase authentication
    if host and user and name:
        # Detect if running in Databricks (Apps, Notebooks, or Jobs)
        is_databricks = (
            os.environ.get("DATABRICKS_RUNTIME_VERSION") or 
            os.environ.get("DATABRICKS_HOST") or
            os.environ.get("DATABRICKS_INSTANCE_POOL_ID") or
            os.path.exists("/databricks") or  # Databricks Apps run in /databricks
            "database.cloud.databricks.com" in host  # Lakebase host indicates Databricks
        )
        
        # Method 1: Fetch short-lived OAuth token via Databricks API for Postgres
        if is_databricks:
            try:
                from databricks.sdk import WorkspaceClient
                
                sdk = WorkspaceClient()
                
                # The user is the Databricks Service Principal / User running the app
                # This overrides the default 'atlas_app' native role
                user = sdk.current_user.me().user_name
                logger.info(f"Using Databricks Workspace user for Lakebase: {user}")
                
                # Fetch all autoscaling projects
                projects_res = sdk.api_client.do("GET", "/api/2.0/postgres/projects")
                projects = projects_res.get("projects", [])
                
                target_project_name = settings.DATABASE_INSTANCE_NAME
                matched_project = None
                
                if target_project_name:
                    matched_project = next((p for p in projects if p.get("name", "").endswith(target_project_name)), None)
                elif projects:
                    # Auto-discover if only one project or just grab the first one
                    matched_project = projects[0]
                    target_project_name = matched_project.get("name")
                    logger.warning(f"DATABASE_INSTANCE_NAME not set in environment. Auto-discovered project: {target_project_name}")
                
                if matched_project and target_project_name:
                    # Construct the path to the primary endpoint on the production branch
                    endpoint_path = f"projects/{target_project_name}/branches/production/endpoints/primary"
                    logger.info(f"Found matching Lakebase project. Requesting credentials for: {endpoint_path}")
                    
                    # Request the token
                    res = sdk.api_client.do(
                        "POST", 
                        "/api/2.0/postgres/credentials",
                        body={"endpoint": endpoint_path}
                    )
                    
                    password = res.get("token")
                    if password:
                        logger.info("Successfully acquired short-lived OAuth token for Lakebase.")
                    else:
                        logger.error("API returned success but no token was found in the response.")
                else:
                    logger.error(f"Could not find any Lakebase projects to connect to.")
                    
            except Exception as e:
                logger.error(f"Failed to fetch Lakebase OAuth credentials: {type(e).__name__}: {e}")
                
        # Method 2: Use DATABASE_PASSWORD from environment (for local dev only)
        if not password:
            password = settings.DATABASE_PASSWORD
            invalid_passwords = ["CHANGE_ME", "{{DATABASE_PASSWORD}}", "", None]
            is_unresolved_template = password and password.startswith("{{")
            
            if password and password not in invalid_passwords and not is_unresolved_template:
                logger.info(f"Using DATABASE_PASSWORD from environment (length: {len(password)})")
            else:
                password = None
                logger.warning("No valid DATABASE_PASSWORD in environment")
        
        # Log final configuration
        logger.info(f"Final DB config - Host: {host}, User: {user}, Password set: {password is not None}")
        
        # If we have all required params, build the PostgreSQL URL
        if password:
            # URL-encode user and password to handle special characters like '@'
            safe_user = quote_plus(user)
            safe_password = quote_plus(password)
            
            url = f"postgresql://{safe_user}:{safe_password}@{host}:{settings.DATABASE_PORT}/{name}?sslmode=require"
            
            # CRITICAL: Log the final URL structure (without password) for debugging
            safe_url = f"postgresql://{safe_user}:***@{host}:{settings.DATABASE_PORT}/{name}?sslmode=require"
            logger.debug(f"FINAL DATABASE URL (safe): {safe_url}")
            logger.info(f"=== LAKEBASE CONNECTION ===")
            logger.info(f"  Host: {host}")
            logger.info(f"  User: {user} (encoded: {safe_user})")
            logger.info(f"  Database: {name}")
            logger.info(f"  Password length: {len(password)}")
            logger.info(f"  Settings.DATABASE_USER (IGNORED): {settings.DATABASE_USER}")
            logger.info(f"  Safe URL: {safe_url}")
            return url
        else:
            logger.warning("No valid password/token found for Lakebase. Falling back to SQLite.")
            logger.warning(f"  Settings.DATABASE_PASSWORD was: {settings.DATABASE_PASSWORD[:30] if settings.DATABASE_PASSWORD else 'None'}...")
    
    # Fallback to SQLite
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # If running in Databricks, try to use a persistent path
    if os.environ.get("DATABRICKS_RUNTIME_VERSION") or os.environ.get("DATABRICKS_HOST"):
        persistent_dir = "/tmp/atlas_hub_data"  # Default fallback
        
        # Try to find the user's workspace path
        for env_var in ["USER", "DATABRICKS_USER", "OWNER"]:
            db_user = os.environ.get(env_var)
            if db_user:
                persistent_dir = f"/Workspace/Users/{db_user}/atlas_hub_data"
                break
        
        try:
            os.makedirs(persistent_dir, exist_ok=True)
            db_path = os.path.join(persistent_dir, "atlas_hub.db")
            logger.info(f"Using persistent SQLite database at: {db_path}")
            return f"sqlite:///{db_path}"
        except Exception as e:
            logger.warning(f"Could not create persistent directory {persistent_dir}: {e}. Falling back to local.")
    
    db_path = os.path.join(base_dir, "atlas_hub.db")
    return f"sqlite:///{db_path}"


def get_engine():
    """Get or create database engine (lazy initialization)."""
    global _engine
    if _engine is None:
        database_url = get_database_url()
        
        if database_url.startswith("sqlite"):
            is_memory = ":memory:" in database_url
            _engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool if is_memory else NullPool,
                echo=False
            )
        else:
            _engine = create_engine(
                database_url,
                poolclass=NullPool,  # Use NullPool for serverless/connection-per-request
                echo=False,
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
