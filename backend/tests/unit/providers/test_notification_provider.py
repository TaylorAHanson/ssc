import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.providers.notifications.client import NotificationProvider

@pytest.fixture
def provider():
    return NotificationProvider()

@pytest.mark.asyncio
async def test_send_email_mock_mode(caplog):
    """Test that email is mocked when provider is mock."""
    import logging
    caplog.set_level(logging.INFO)
    
    with patch("app.core.config.settings.NOTIFICATION_EMAIL_PROVIDER", "mock"):
        provider = NotificationProvider()
        result = await provider.send_email(
            to="user@example.com", 
            subject="Test Subject", 
            body="Hello World"
        )
        assert result is True
        assert "MOCK EMAIL NOTIFICATION" in caplog.text
        assert "To: user@example.com" in caplog.text

@pytest.mark.asyncio
async def test_send_email_smtp_mode():
    """Test that email uses SMTP when configured."""
    with patch("app.core.config.settings.NOTIFICATION_EMAIL_PROVIDER", "smtp"), \
         patch("app.core.config.settings.NOTIFICATION_EMAIL_SMTP_HOST", "smtp.example.com"), \
         patch("app.core.config.settings.NOTIFICATION_EMAIL_SMTP_PORT", 587), \
         patch("smtplib.SMTP") as mock_smtp:
        
        provider = NotificationProvider()
        
        # Setup mock context manager
        instance = mock_smtp.return_value.__enter__.return_value
        
        result = await provider.send_email(
            to="user@example.com", 
            subject="Real Email", 
            body="Real Body"
        )
        
        assert result is True
        mock_smtp.assert_called_with("smtp.example.com", 587)
        instance.starttls.assert_called_once()
        instance.send_message.assert_called_once()
        
        # Verify message content (MIMEMultipart)
        args, _ = instance.send_message.call_args
        msg = args[0]
        assert msg['To'] == "user@example.com"
        assert msg['Subject'] == "Real Email"

@pytest.mark.asyncio
async def test_send_email_ses_mode():
    """SES mode sends in-app via boto3 using IAM creds from a Databricks secret."""
    fake_creds = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
    }
    mock_boto3 = MagicMock()
    mock_ses_client = mock_boto3.Session.return_value.client.return_value
    mock_ses_client.send_raw_email.return_value = {"MessageId": "msg-123"}

    with patch("app.core.config.settings.NOTIFICATION_EMAIL_PROVIDER", "ses"), \
         patch("app.core.config.settings.NOTIFICATION_EMAIL_SES_REGION", "us-east-1"), \
         patch("app.core.config.settings.NOTIFICATION_EMAIL_SES_SOURCE", "source@databricks.com"), \
         patch("app.core.config.Settings.get_ses_aws_credentials", return_value=fake_creds), \
         patch.dict("sys.modules", {"boto3": mock_boto3}):

        provider = NotificationProvider()

        result = await provider.send_email(
            to="user@example.com",
            subject="SES Email",
            body="SES Body"
        )

        assert result is True

        # Session created with region + IAM creds from the secret.
        mock_boto3.Session.assert_called_once_with(region_name="us-east-1", **fake_creds)
        mock_boto3.Session.return_value.client.assert_called_once_with("ses")

        # Verify the raw email was sent with the configured source and recipient.
        _, kwargs = mock_ses_client.send_raw_email.call_args
        assert kwargs["Source"] == "source@databricks.com"
        assert kwargs["Destinations"] == ["user@example.com"]
        assert "SES Email" in kwargs["RawMessage"]["Data"]

def test_html_body_generation(provider):
    """Test HTML body generation with metadata."""
    metadata = {"status": "completed", "id": "req-123"}
    html = provider._get_html_body("Test Message", metadata)
    
    assert "Test Message" in html
    assert "req-123" in html
    assert "completed" in html
    assert "<!DOCTYPE html>" in html
