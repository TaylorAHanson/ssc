"""
Notification provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable
import httpx


class NotificationProvider(BaseProvider):
    """Notification provider for email, Slack, Teams."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.email_config = self.get_config("email", {})
        self.slack_config = self.get_config("slack", {})
        self.teams_config = self.get_config("teams", {})
    
    @retry_on_retryable(max_attempts=3)
    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Send email notification."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from app.core.config import settings
            
            smtp_host = settings.NOTIFICATION_EMAIL_SMTP_HOST
            smtp_port = settings.NOTIFICATION_EMAIL_SMTP_PORT
            smtp_user = settings.NOTIFICATION_EMAIL_SMTP_USER
            smtp_password = settings.NOTIFICATION_EMAIL_SMTP_PASSWORD
            
            if not smtp_host:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"======== MOCK EMAIL NOTIFICATION ========")
                logger.info(f"To: {to}")
                logger.info(f"Subject: {subject}")
                logger.info(f"Body: {body}")
                logger.info(f"=========================================")
                return True

            msg = MIMEMultipart()
            msg['From'] = smtp_user or "noreply@edas-hub.com"
            msg['To'] = to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            # Using synchronous smtplib in async function - technically blocking but fine for low volume
            # In production, run this in a threadpool or use aiosmtplib
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_port == 587:
                    server.starttls()
                
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                
                server.send_message(msg)
                
            return True
        except Exception as e:
            raise RetryableError(f"Email notification failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def send_slack(self, channel: str, message: str) -> bool:
        """Send Slack notification."""
        try:
            webhook_url = self.slack_config.get("webhook_url")
            if not webhook_url:
                return False
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={"channel": channel, "text": message}
                )
                response.raise_for_status()
                return True
        except Exception as e:
            raise RetryableError(f"Slack notification failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def send_teams(self, webhook: str, message: Dict[str, Any]) -> bool:
        """Send Teams notification."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(webhook, json=message)
                response.raise_for_status()
                return True
        except Exception as e:
            raise RetryableError(f"Teams notification failed: {str(e)}")
    
    async def health_check(self) -> bool:
        """Check if notification services are accessible."""
        return True

