import pytest
from unittest.mock import MagicMock, patch
from app.providers.notifications.client import NotificationProvider

@pytest.fixture
def provider():
    return NotificationProvider()

@pytest.mark.asyncio
async def test_send_email_mock_mode(provider, caplog):
    """Test that email is mocked when SMTP host is not configured."""
    import logging
    caplog.set_level(logging.INFO)
    
    with patch("app.core.config.settings.NOTIFICATION_EMAIL_SMTP_HOST", None):
        result = await provider.send_email(
            to="user@example.com", 
            subject="Test Subject", 
            body="Hello World"
        )
        assert result is True
        assert "MOCK EMAIL NOTIFICATION" in caplog.text
        assert "To: user@example.com" in caplog.text

@pytest.mark.asyncio
async def test_send_email_smtp_mode(provider):
    """Test that email uses SMTP when configured."""
    with patch("app.core.config.settings.NOTIFICATION_EMAIL_SMTP_HOST", "smtp.example.com"), \
         patch("app.core.config.settings.NOTIFICATION_EMAIL_SMTP_PORT", 587), \
         patch("smtplib.SMTP") as mock_smtp:
        
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

def test_html_body_generation(provider):
    """Test HTML body generation with metadata."""
    metadata = {"status": "completed", "id": "req-123"}
    html = provider._get_html_body("Test Message", metadata)
    
    assert "Test Message" in html
    assert "req-123" in html
    assert "completed" in html
    assert "<!DOCTYPE html>" in html
