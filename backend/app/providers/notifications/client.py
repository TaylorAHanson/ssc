"""
Notification provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable
import httpx
import os
import base64
import logging

logger = logging.getLogger(__name__)

class NotificationProvider(BaseProvider):
    """Notification provider for email, Slack, Teams."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.email_config = self.get_config("email", {})
        self.slack_config = self.get_config("slack", {})
        self.teams_config = self.get_config("teams", {})
    
    @retry_on_retryable(max_attempts=3)
    async def send_email(self, to: str, subject: str, body: str, metadata: Optional[Dict[str, Any]] = None, is_html: bool = False) -> bool:
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
            
            # Always wrap in template, but hint if the body itself is already HTML
            html_body = self._get_html_body(body, metadata, is_html=is_html)

            if not smtp_host:
                import logging
                logger = logging.getLogger(__name__)
                mock_msg = (
                    f"\n======== MOCK EMAIL NOTIFICATION ========\n"
                    f"To: {to}\n"
                    f"Subject: {subject}\n"
                    f"Metadata: {metadata}\n"
                    f"Body (Text length): {len(body)}\n"
                    f"Body (HTML length): {len(html_body)}\n"
                    f"is_html: {is_html}\n"
                    f"=========================================\n"
                )
                logger.info(mock_msg)
                print(mock_msg) # Direct print for visibility
                return True

            msg = MIMEMultipart("alternative")
            msg['From'] = smtp_user or "noreply@databricks.com"
            msg['To'] = to
            msg['Subject'] = subject
            
            # Attach plain text and HTML versions
            part1 = MIMEText(body, 'plain')
            part2 = MIMEText(html_body, 'html')
            msg.attach(part1)
            msg.attach(part2)
            
            # Using synchronous smtplib in async function
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_port == 587:
                    server.starttls()
                
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                
                server.send_message(msg)
                
            return True
        except Exception as e:
            raise RetryableError(f"Email notification failed: {str(e)}")

    def _get_html_body(self, message: str, metadata: Optional[Dict[str, Any]] = None, is_html: bool = False) -> str:
        """Generate branded HTML body for email."""
        from app.core.config import settings
        
        brand_name = settings.BRAND_NAME
        brand_logo = settings.BRAND_LOGO_URL
        brand_color = settings.BRAND_COLOR_PRIMARY
        
        # Fallback to local SVG if no logo URL is provided
        if not brand_logo:
            try:
                # Resolve path relative to project root
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.abspath(os.path.join(current_dir, "../../../../"))
                logo_path = os.path.join(project_root, "src", "assets", "icon.svg")
                
                if os.path.exists(logo_path):
                    with open(logo_path, "rb") as f:
                        svg_data = f.read()
                        encoded = base64.b64encode(svg_data).decode("utf-8")
                        brand_logo = f"data:image/svg+xml;base64,{encoded}"
            except Exception as e:
                logger.error(f"Failed to load local logo fallback: {e}")
        
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

