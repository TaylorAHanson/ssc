import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.core.config import _yaml_config, settings
import logging

logger = logging.getLogger(__name__)

class WorkspaceConfig(BaseModel):
    name: str
    host: str
    environment: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None

def get_target_workspaces() -> List[WorkspaceConfig]:
    """
    Get the list of target workspaces configured in configuration.yaml.
    Falls back to the default workspace in settings if none are configured.
    """
    workspaces_config = _yaml_config.get("target_workspaces", [])
    
    workspaces = []
    for ws in workspaces_config:
        # Resolve credentials from env vars specified in config
        client_id_env = ws.get("client_id_env")
        client_secret_env = ws.get("client_secret_env")
        token_env = ws.get("token_env")
        
        client_id = os.getenv(client_id_env) if client_id_env else None
        client_secret = os.getenv(client_secret_env) if client_secret_env else None
        token = os.getenv(token_env) if token_env else None
        
        # Fallback to default credentials if specific ones aren't found
        if not client_id and not token:
            client_id = settings.DATABRICKS_CLIENT_ID
            client_secret = settings.DATABRICKS_CLIENT_SECRET
            token = settings.DATABRICKS_TOKEN
            
        workspaces.append(WorkspaceConfig(
            name=ws.get("name", "unknown"),
            host=ws.get("host", ""),
            environment=ws.get("environment", "unknown"),
            client_id=client_id,
            client_secret=client_secret,
            token=token
        ))
        
    # If no workspaces configured, add the default one
    if not workspaces and (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL):
        workspaces.append(WorkspaceConfig(
            name="default",
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            environment=settings.ENVIRONMENT,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            token=settings.DATABRICKS_TOKEN
        ))
        
    return workspaces

def get_workspace_config(host_or_name: str) -> Optional[WorkspaceConfig]:
    """
    Get the configuration for a specific workspace by host URL or name.
    """
    workspaces = get_target_workspaces()
    
    for ws in workspaces:
        if ws.host == host_or_name or ws.name == host_or_name:
            return ws
            
    # If not found, but we have a host URL, create a fallback config using default credentials
    if host_or_name.startswith("https://"):
        logger.warning(f"Workspace {host_or_name} not found in config. Falling back to default credentials.")
        return WorkspaceConfig(
            name="fallback",
            host=host_or_name,
            environment="unknown",
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            token=settings.DATABRICKS_TOKEN
        )
        
    return None
