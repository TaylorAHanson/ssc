"""
Service principal tools.
"""
from typing import Dict, Any
from app.tools.base import BaseTool
from app.providers.idp import IDPProvider
from app.providers.databricks import DatabricksProvider
from app.core.config import settings


class CreateServicePrincipalTool(BaseTool):
    """Create service principal."""
    
    def __init__(self):
        self.idp = IDPProvider(
            base_url="",  # TODO: Add to settings
            api_key=""  # TODO: Add to settings
        )
        self.databricks = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN
        )
    
    async def execute(
        self,
        name: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create service principal."""
        # Create in IDP
        principal = await self.idp.create_service_principal(name, config)
        
        # Create API key if requested
        api_key = None
        if config.get("create_api_key", False):
            api_key_result = await self.idp.create_api_key(principal["id"], f"{name}-api-key")
            api_key = api_key_result.get("key")
        
        # Grant Databricks access
        if config.get("databricks_resources"):
            await self.databricks.grant_access(
                principal["id"],
                config["databricks_resources"],
                config.get("permissions", ["user"])
            )
        
        return {
            "principal_id": principal["id"],
            "api_key": api_key,
            "status": "completed"
        }

