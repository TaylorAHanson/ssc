"""
Access management tools.
"""
from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.providers.idp import IDPProvider
from app.providers.notifications import NotificationProvider
from app.core.config import settings


class GrantAccessTool(BaseTool):
    """Grant access to resources."""
    
    def __init__(self):
        """Initialize providers from settings."""
        self.databricks = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN
        )
        self.idp = IDPProvider(
            base_url=settings.IDP_BASE_URL or "",
            api_key=settings.IDP_API_KEY or ""
        )
        notification_config = {
            "email": {
                "smtp_host": settings.NOTIFICATION_EMAIL_SMTP_HOST,
                "smtp_port": settings.NOTIFICATION_EMAIL_SMTP_PORT,
                "smtp_user": settings.NOTIFICATION_EMAIL_SMTP_USER,
                "smtp_password": settings.NOTIFICATION_EMAIL_SMTP_PASSWORD,
            },
            "slack": {
                "webhook_url": settings.NOTIFICATION_SLACK_WEBHOOK_URL,
            },
            "teams": {
                "webhook_url": settings.NOTIFICATION_TEAMS_WEBHOOK_URL,
            }
        }
        self.notifications = NotificationProvider(config=notification_config)
    
    async def execute(
        self,
        user: str,
        resource: str,
        permissions: List[str]
    ) -> bool:
        """Grant access to resource."""
        # Grant Databricks permissions
        await self.databricks.grant_access(user, resource, permissions)
        
        # Add to IDP groups if needed
        # TODO: Determine group from resource/permissions
        # await self.idp.add_to_group(user, group_id)
        
        # Notify user
        await self.notifications.send_email(
            to=user,
            subject=f"Access granted to {resource}",
            body=f"You now have {', '.join(permissions)} access to {resource}"
        )
        
        return True

