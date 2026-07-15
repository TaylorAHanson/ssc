"""
Notification provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable
import httpx
import logging

logger = logging.getLogger(__name__)

class NotificationProvider(BaseProvider):
    """Notification provider for email, Slack, Teams."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.email_config = self.get_config("email", {})
        self.slack_config = self.get_config("slack", {})
        self.teams_config = self.get_config("teams", {})
        
        self._email_provider = self._init_email_provider()

    def _init_email_provider(self):
        from app.core.config import settings
        from app.providers.notifications.email import (
            SMTPEmailProvider, 
            SESEmailProvider, 
            MockEmailProvider
        )
        
        provider_type = getattr(settings, "NOTIFICATION_EMAIL_PROVIDER", "smtp").lower()
        
        if provider_type == "ses":
            region = getattr(settings, "NOTIFICATION_EMAIL_SES_REGION", "us-west-2")
            source = getattr(settings, "NOTIFICATION_EMAIL_SES_SOURCE", "")
            scope = getattr(settings, "NOTIFICATION_EMAIL_SES_SECRET_SCOPE", "")
            logger.info(
                "Email notifications using SES provider (region=%s, source=%s, iam_secret_scope=%s)",
                region, source or "<unset>", scope or "<unset -> ambient AWS creds>",
            )
            return SESEmailProvider(region=region)
        elif provider_type == "smtp":
            if settings.NOTIFICATION_EMAIL_SMTP_HOST:
                logger.info(
                    "Email notifications using SMTP provider (host=%s, port=%s)",
                    settings.NOTIFICATION_EMAIL_SMTP_HOST, settings.NOTIFICATION_EMAIL_SMTP_PORT,
                )
                return SMTPEmailProvider(
                    host=settings.NOTIFICATION_EMAIL_SMTP_HOST,
                    port=settings.NOTIFICATION_EMAIL_SMTP_PORT,
                    user=settings.NOTIFICATION_EMAIL_SMTP_USER,
                    password=settings.NOTIFICATION_EMAIL_SMTP_PASSWORD
                )
            else:
                logger.warning(
                    "NOTIFICATION_EMAIL_PROVIDER=smtp but NOTIFICATION_EMAIL_SMTP_HOST is "
                    "unset; using Mock provider (emails are logged, NOT delivered)."
                )
                return MockEmailProvider()
        else:
            logger.info(
                "Email notifications using Mock provider (provider=%r); emails are "
                "logged, NOT delivered.", provider_type,
            )
            return MockEmailProvider()
    
    @retry_on_retryable(max_attempts=3)
    async def send_email(self, to: str, subject: str, body: str, metadata: Optional[Dict[str, Any]] = None, is_html: bool = False) -> bool:
        """Send email notification."""
        try:
            from app.core.config import settings
            
            # Always wrap in template, but hint if the body itself is already HTML
            html_body = self._get_html_body(body, metadata, is_html=is_html)
            
            provider_type = getattr(settings, "NOTIFICATION_EMAIL_PROVIDER", "smtp").lower()
            from_email = None
            if provider_type == "ses":
                from_email = getattr(settings, "NOTIFICATION_EMAIL_SES_SOURCE", None)
            else:
                from_email = settings.NOTIFICATION_EMAIL_SMTP_USER or None
            
            return await self._email_provider.send_email(
                to=to,
                subject=subject,
                body=body,
                html_body=html_body,
                from_email=from_email
            )
        except Exception as e:
            raise RetryableError(f"Email notification failed: {str(e)}")

    def _get_html_body(self, message: str, metadata: Optional[Dict[str, Any]] = None, is_html: bool = False) -> str:
        """Generate branded HTML body for email."""
        from app.core.config import settings
        
        brand_name = settings.BRAND_NAME
        brand_logo = settings.BRAND_LOGO_URL
        brand_color = settings.BRAND_COLOR_PRIMARY

        # No bundled default logo: when BRAND_LOGO_URL isn't configured we render
        # no logo at all rather than embedding a stock image.
        logo_html = f'<img src="{brand_logo}" alt="{brand_name}" style="max-height: 24px; vertical-align: middle; margin-right: 10px;">' if brand_logo else ""
        
        details_html = ""
        if metadata:
            details_html = '<div class="details-section"><h3>Request Details</h3><ul>'
            if metadata.get("id"):
                details_html += f'<li><strong>Request ID:</strong> <code>{metadata["id"]}</code></li>'
            if metadata.get("status"):
                details_html += f'<li><strong>Status:</strong> {metadata["status"]}</li>'
            if metadata.get("requested_by"):
                details_html += f'<li><strong>Requested By:</strong> {metadata["requested_by"]}</li>'
            details_html += '</ul></div>'
        
        # If is_html is True, don't escape newlines
        formatted_message = message if is_html else message.replace('\n', '<br>')
            
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #f3f4f6;
            margin: 0;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            text-align: center;
            padding: 24px 30px;
            background-color: #ffffff;
            border-bottom: 3px solid {brand_color};
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .brand-name {{
            color: {brand_color};
            font-size: 22px;
            font-weight: bold;
            margin: 0;
        }}
        .main-content {{
            padding: 40px;
        }}
        .message {{
            color: #374151;
            line-height: 1.6;
            font-size: 16px;
            margin-bottom: 30px;
        }}
        .details-section {{
            background-color: #f9fafb;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            padding: 20px;
        }}
        .details-section h3 {{
            margin-top: 0;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #6b7280;
            margin-bottom: 12px;
        }}
        .details-section ul {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        .details-section li {{
            font-size: 14px;
            color: #4b5563;
            margin-bottom: 6px;
        }}
        .details-section li:last-child {{
            margin-bottom: 0;
        }}
        .footer {{
            text-align: center;
            padding: 20px;
            color: #9ca3af;
            font-size: 12px;
        }}
        code {{
            background-color: #f3f4f6;
            padding: 2px 4px;
            border-radius: 4px;
            font-family: monospace;
        }}
        /* Table styles for reports */
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 1rem; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <!--[if mso]>
    <table role="presentation" align="center" width="800" cellpadding="0" cellspacing="0" border="0" style="width:800px;"><tr><td>
    <![endif]-->
    <div class="container">
        <div class="header">
            {logo_html}
            <h1 class="brand-name" style="display: inline-block; vertical-align: middle;">{brand_name}</h1>
        </div>
        <div class="main-content">
            <div class="message">
                {formatted_message}
            </div>
            {details_html}
        </div>
    </div>
    <!--[if mso]>
    </td></tr></table>
    <![endif]-->
    <div class="footer">
        Automated notification from {brand_name}
    </div>
</body>
</html>
"""
    
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

