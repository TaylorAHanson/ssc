"""
Workspace tools.
"""
from typing import Dict, Any
from app.tools.base import BaseTool
from app.providers.terraform import TerraformProvider
from app.providers.databricks import DatabricksProvider
from app.providers.idp import IDPProvider
from app.providers.notifications import NotificationProvider
from app.core.config import settings


class CreateWorkspaceTool(BaseTool):
    """Create a Databricks workspace."""
    
    def __init__(self):
        # TODO: Get these from settings/config
        self.terraform = TerraformProvider(
            workspace_dir="/tmp/terraform",
            config={}
        )
        self.databricks = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN
        )
        self.idp = IDPProvider(
            base_url="",  # TODO: Add to settings
            api_key=""  # TODO: Add to settings
        )
        self.notifications = NotificationProvider(
            config={}
        )
    
    async def execute(
        self,
        name: str,
        environment: str,
        config: Dict[str, Any],
        requested_by: str
    ) -> Dict[str, Any]:
        """Create workspace using providers."""
        # Step 1: Provision infrastructure via Terraform
        tf_config = self._build_terraform_config(name, environment, config)
        tf_result = await self.terraform.apply(tf_config, variables=config)
        
        workspace_id = tf_result.get("workspace_id")
        
        # Step 2: Configure Databricks workspace
        db_result = await self.databricks.create_workspace(
            workspace_id=workspace_id,
            config=config.get("databricks_config", {})
        )
        
        # Step 3: Grant access to requester
        await self.idp.grant_permission(
            principal_id=requested_by,
            resource=f"workspace:{workspace_id}",
            permissions=config.get("permissions", ["user"])
        )
        
        # Step 4: Notify user
        await self.notifications.send_email(
            to=requested_by,
            subject=f"Workspace {name} has been provisioned",
            body=f"Your workspace is ready at {db_result.get('url', 'N/A')}"
        )
        
        return {
            "workspace_id": workspace_id,
            "workspace_url": db_result.get("url"),
            "status": "completed"
        }
    
    def _build_terraform_config(self, name: str, environment: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Build Terraform configuration."""
        # TODO: Implement Terraform config building
        return {}

