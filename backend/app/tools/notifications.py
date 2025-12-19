"""
Notification tools.
"""
from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.notifications import NotificationProvider


class SendNotificationTool(BaseTool):
    """Send notifications."""
    
    def __init__(self):
        self.notifications = NotificationProvider(config={})
    
    async def execute(
        self,
        user: str,
        message: str,
        notification_type: str = "email",
        **kwargs
    ) -> bool:
        """Send notification."""
        if notification_type == "email":
            return await self.notifications.send_email(
                to=user,
                subject=kwargs.get("subject", "Notification"),
                body=message
            )
        elif notification_type == "teams":
            return await self.notifications.send_teams(
                webhook=kwargs.get("webhook"),
                message={"text": message}
            )
        return False

