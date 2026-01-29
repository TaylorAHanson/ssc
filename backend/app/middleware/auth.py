
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract user identity from headers (Databricks Apps)
    and provide mock defaults for local development.
    """
    async def dispatch(self, request: Request, call_next):
        # Extract headers (case-insensitive usually, but using request.headers.get is safe)
        # Databricks Apps headers:
        # X-Forwarded-Email: IdP email
        # X-Forwarded-Preferred-Username: IdP display/username
        # X-Forwarded-User: IdP user identifier
        
        email = request.headers.get("X-Forwarded-Email", settings.MOCK_USER_EMAIL)
        username = request.headers.get("X-Forwarded-Preferred-Username", settings.MOCK_USER_NAME)
        user_id = request.headers.get("X-Forwarded-User", settings.MOCK_USER_ID)
        
        
        # Extract OBO Token (Databricks Apps)
        # Check both X-Forwarded-Access-Token and Authorization header
        obo_token = request.headers.get("X-Forwarded-Access-Token")
        
        # Fallback to mock token for local dev if configured and no header present
        if not obo_token and settings.MOCK_USER_TOKEN:
             obo_token = settings.MOCK_USER_TOKEN
             # Only log this in debug mode to avoid leaking secrets in logs
             logger.debug("AuthMiddleware: Using MOCK_USER_TOKEN")
        
        # Store in request state
        request.state.user = {
            "email": email,
            "username": username,
            "id": user_id
        }
        request.state.token = obo_token
        
        # DEBUG: Decode token to verify scopes (if it looks like a JWT)
        if obo_token and obo_token.startswith("eyJ"):
             try:
                 import base64
                 import json
                 # Simple decode of middle part (payload)
                 parts = obo_token.split('.')
                 if len(parts) > 1:
                     payload_str = parts[1]
                     # Add padding if needed
                     payload_str += '=' * (-len(payload_str) % 4)
                     payload_data = json.loads(base64.b64decode(payload_str))
                     scopes = payload_data.get("scp") or payload_data.get("scope")
                     logger.info(f"DEBUG AUTH: OBO Token Scopes: {scopes}")
             except Exception as e:
                 logger.error(f"DEBUG AUTH: Failed to decode token scopes: {e}")

        # Log only if different from default (to avoid noise) or on every request for debugging
        if email != settings.MOCK_USER_EMAIL:
             logger.debug(f"AuthMiddleware: User context set for {email}")
        
        response = await call_next(request)
        return response
