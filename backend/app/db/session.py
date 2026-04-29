"""
Database session management for Lakebase (PostgreSQL) and SQLite (Dev).
"""
from sqlalchemy import create_engine, event
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
    # 1. First, check if Databricks Apps auto-injected PG variables
    pg_host = os.environ.get("PGHOST")
    pg_user = os.environ.get("PGUSER")
    pg_name = os.environ.get("PGDATABASE")
    pg_port = os.environ.get("PGPORT", "5432")
    
    host = pg_host or settings.DATABASE_HOST
    user = pg_user or settings.DATABASE_USER or "atlas_app"  # Native Postgres role
    name = pg_name or settings.DATABASE_NAME
    port = pg_port or settings.DATABASE_PORT
    password = settings.DATABASE_PASSWORD
    
    # DEBUG: Print what we're using
    logger.debug(f"=== DATABASE SETTINGS DEBUG ===")
    logger.debug(f"FORCING DB USER = {user}")
    logger.debug(f"HOST = {host}")
    logger.debug(f"NAME = {name}")
    
    db_id = None
    if host and user and name:
        if pg_host:
            logger.info("Using Databricks Apps auto-injected PG variables for Lakebase connection.")
            try:
                from databricks.sdk import WorkspaceClient
                sdk = WorkspaceClient()
                auth_headers = sdk.config.authenticate()
                if auth_headers and "Authorization" in auth_headers:
                    password = auth_headers["Authorization"].replace("Bearer ", "")
                elif hasattr(sdk.config, "token") and sdk.config.token:
                    password = sdk.config.token
                
                if password:
                    logger.info("Successfully acquired Databricks OAuth token for Lakebase password.")
                else:
                    logger.error("Failed to acquire OAuth token from WorkspaceClient.")
            except Exception as e:
                logger.error(f"Failed to fetch Databricks OAuth token: {type(e).__name__}: {e}")
        elif settings.DATABASE_PASSWORD:
            logger.info("Using injected DATABASE_PASSWORD from environment (Resource Binding).")
            password = settings.DATABASE_PASSWORD
            # When a resource is bound, Databricks injects the specific DATABASE_USER and DATABASE_NAME too
            user = settings.DATABASE_USER
            name = settings.DATABASE_NAME
        else:
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
                        
                        # Fetch databases in this branch to use the database ID as dbname
                        try:
                            db_res = sdk.api_client.do("GET", f"/api/2.0/postgres/projects/{target_project_name}/branches/production/databases")
                            databases = db_res.get("databases", [])
                            db_id = None
                            if databases:
                                # Extract the actual database ID (e.g. db-xxxxxxxx)
                                # Name format is usually "projects/.../databases/db-xxxx"
                                db_name_full = databases[0].get("name", "")
                                db_id = db_name_full.split("/")[-1]
                                logger.info(f"Auto-discovered Database ID: {db_id}")
                            else:
                                logger.warning("Could not find any databases in the production branch!")
                        except Exception as db_e:
                            logger.warning(f"Failed to auto-discover database ID, falling back to name. Error: {db_e}")
                            db_id = None
                        
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
                
        # Log final configuration
        logger.info(f"Final DB config - Host: {host}, User: {user}, Password set: {password is not None}")
        
        # If we have all required params, build the PostgreSQL URL
        if password:
            # URL-encode user and password to handle special characters like '@'
            safe_user = quote_plus(user)
            safe_password = quote_plus(password)
            
            # The database name MUST be the database_id for autoscaling Lakebase!
            # If the API returned it above, use it; otherwise fallback to DATABASE_NAME
            db_name_to_use = db_id if db_id else name
            
            url = f"postgresql://{safe_user}:{safe_password}@{host}:{settings.DATABASE_PORT}/{db_name_to_use}?sslmode=require"
            
            # CRITICAL: Log the final URL structure (without password) for debugging
            safe_url = f"postgresql://{safe_user}:***@{host}:{settings.DATABASE_PORT}/{db_name_to_use}?sslmode=require"
            logger.debug(f"FINAL DATABASE URL (safe): {safe_url}")
            logger.info(f"=== LAKEBASE CONNECTION ===")
            logger.info(f"  Host: {host}")
            logger.info(f"  User: {user} (encoded: {safe_user})")
            logger.info(f"  Database: {db_name_to_use}")
            logger.info(f"  Password length: {len(password)}")
            logger.info(f"  Safe URL: {safe_url}")
            return url
        else:
            logger.warning("No valid password/token found for Lakebase. Falling back to SQLite.")
            
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
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,  # checks connection is alive before using it
                echo=False,
            )
            
            @event.listens_for(_engine, "connect")
            def on_connect(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    # By default in PG 15+, public schema doesn't allow CREATE
                    # Create our own schema and use it instead
                    schema_name = "edh-ssc"
                    cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";')
                    cursor.execute(f'SET search_path TO "{schema_name}";')
                except Exception as e:
                    logger.warning(f"Failed to setup schema or search_path: {e}")
                finally:
                    cursor.close()
                    
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
