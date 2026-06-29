from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

class BaseEmailProvider(ABC):
    """Base interface for email providers."""
    
    @abstractmethod
    async def send_email(self, to: str, subject: str, body: str, html_body: str, from_email: Optional[str] = None) -> bool:
        """Send an email."""
        pass


class MockEmailProvider(BaseEmailProvider):
    """Mock email provider that just logs the email."""
    
    async def send_email(self, to: str, subject: str, body: str, html_body: str, from_email: Optional[str] = None) -> bool:
        mock_msg = (
            f"\n======== MOCK EMAIL NOTIFICATION ========\n"
            f"From: {from_email}\n"
            f"To: {to}\n"
            f"Subject: {subject}\n"
            f"Body (Text length): {len(body)}\n"
            f"Body (HTML length): {len(html_body)}\n"
            f"=========================================\n"
        )
        logger.info(mock_msg)
        print(mock_msg)
        return True


class SMTPEmailProvider(BaseEmailProvider):
    """Email provider using SMTP."""
    
    def __init__(self, host: str, port: int, user: str = "", password: str = ""):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        
    async def send_email(self, to: str, subject: str, body: str, html_body: str, from_email: Optional[str] = None) -> bool:
        import smtplib
        
        msg = MIMEMultipart("alternative")
        msg['From'] = from_email or self.user or "noreply@databricks.com"
        msg['To'] = to
        msg['Subject'] = subject
        
        part1 = MIMEText(body, 'plain')
        part2 = MIMEText(html_body, 'html')
        msg.attach(part1)
        msg.attach(part2)
        
        # Using synchronous smtplib in async function
        # In a high-throughput scenario, this should be run in an executor
        with smtplib.SMTP(self.host, self.port) as server:
            if self.port == 587:
                server.starttls()
            
            if self.user and self.password:
                server.login(self.user, self.password)
            
            server.send_message(msg)
            
        return True


class SESEmailProvider(BaseEmailProvider):
    """Email provider using AWS SES, sent in-process via boto3.

    Authentication uses static IAM credentials (access key id + secret access
    key, optionally a session token) read from a Databricks secret scope. This
    deliberately avoids ``dbutils.credentials.getServiceCredentialsProvider``,
    which is not available from a serverless Databricks App. If no IAM
    credentials are configured, boto3 falls back to the ambient credential
    chain (env vars, instance profile, etc.).
    """

    def __init__(self, region: str):
        self.region = region

    def _send_sync(self, to: str, subject: str, body: str, html_body: str, from_email: str) -> str:
        """Synchronous boto3 SES send. Runs in a thread via send_email()."""
        import boto3
        from app.core.config import settings

        msg = MIMEMultipart("alternative")
        msg["From"] = from_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # IAM credentials from a Databricks secret. Returns None to let boto3
        # use its default credential resolution chain.
        creds = settings.get_ses_aws_credentials() or {}
        if not creds:
            logger.warning(
                "No SES IAM credentials resolved from Databricks secrets "
                "(scope=%s); boto3 will use the ambient AWS credential chain. On a "
                "serverless Databricks App this usually yields 'Unable to locate "
                "credentials' — set NOTIFICATION_EMAIL_SES_SECRET_SCOPE and the "
                "access/secret key names to a scope the app SP can READ.",
                settings.NOTIFICATION_EMAIL_SES_SECRET_SCOPE or "<unset>",
            )

        session = boto3.Session(region_name=self.region, **creds)
        client = session.client("ses")

        response = client.send_raw_email(
            Source=from_email,
            Destinations=[to],
            RawMessage={"Data": msg.as_string()},
        )
        return response.get("MessageId", "")

    async def send_email(self, to: str, subject: str, body: str, html_body: str, from_email: Optional[str] = None) -> bool:
        import asyncio

        sender = from_email or "noreply@databricks.com"
        try:
            message_id = await asyncio.to_thread(
                self._send_sync, to, subject, body, html_body, sender
            )
            logger.info(f"SES email sent to {to}. MessageId: {message_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send SES email to {to}: {e}")
            return False
