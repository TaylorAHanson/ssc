"""
Catalog tools.
"""
from typing import Dict, Any
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings


class CreateCatalogTool(BaseTool):
    """Create Unity Catalog catalog."""
    
    def __init__(self):
        self.databricks = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN
        )
    
    async def execute(
        self,
        name: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create catalog."""
        result = await self.databricks.create_catalog(name, config)
        return result

