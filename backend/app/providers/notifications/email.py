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
    """Email provider using AWS SES via a Databricks Job."""
    
    def __init__(self, region: str):
        self.region = region

    async def send_email(self, to: str, subject: str, body: str, html_body: str, from_email: Optional[str] = None) -> bool:
        from app.providers.databricks.client import DatabricksProvider
        from app.core.config import settings
        import json
        
        # Initialize Databricks provider
        db_provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        
        # Python script to be executed on the cluster
        python_code = f'''
import sys
import boto3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def main():
    if len(sys.argv) < 7:
        print("Missing arguments")
        sys.exit(1)
        
    to_email = sys.argv[1]
    subject = sys.argv[2]
    body = sys.argv[3]
    html_body = sys.argv[4]
    from_email = sys.argv[5]
    region = sys.argv[6]
    
    msg = MIMEMultipart("alternative")
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    
    part1 = MIMEText(body, 'plain')
    part2 = MIMEText(html_body, 'html')
    msg.attach(part1)
    msg.attach(part2)
    
    # Boto3 will automatically use instance profile or environment variables
    session = boto3.Session(region_name=region)
    client = session.client("ses")
    
    response = client.send_raw_email(
        Source=msg['From'],
        Destinations=[to_email],
        RawMessage={{'Data': msg.as_string()}}
    )
    print(f"Email sent successfully. MessageId: {{response.get('MessageId')}}")

if __name__ == "__main__":
    main()
'''
        
        parameters = [
            to,
            subject,
            body,
            html_body,
            from_email or "noreply@databricks.com",
            self.region
        ]
        
        try:
            run_id = await db_provider.submit_python_job(
                python_code=python_code,
                parameters=parameters,
                run_name=f"Send Email to {to}"
            )
            logger.info(f"SES email job submitted. RunId: {run_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to submit SES email job: {e}")
            return False
